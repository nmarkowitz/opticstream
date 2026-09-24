"""`ops oct watch` runs enface previews; pipeline mosaic JPEGs skip Slack by default."""
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from opticstream.cli.oct.watch import build_watch_previews
from opticstream.config.enface_preview import EnfacePreviewConfig
from opticstream.config.psoct_scan_config import PSOCTAcquisitionParams
from opticstream.flows.psoct import mosaic_enface_qc_flow as qc
from opticstream.state.oct_project_state import OCTMosaicId
from opticstream.utils import polling_watcher
from opticstream.utils.polling_watcher import PollingStableWatcher


def scan_config(root, **preview):
    return SimpleNamespace(
        project_base_path=Path(root) / "out",
        mosaics_per_slice=2,
        enface_preview=EnfacePreviewConfig(
            aip_pattern="{acquisition}_aip_{image}.nii",
            mip_pattern="{acquisition}_mip_{image}.nii",
            ori_pattern="{acquisition}_ori_{image}.nii",
            ret_pattern="{acquisition}_ret_{image}.nii",
            **preview,
        ),
        acquisition=PSOCTAcquisitionParams(
            grid_size_y=2, grid_size_x_normal=2, grid_size_x_tilted=3, tile_overlap=0,
            acquisition_mosaic_map={"normal0deg": 1, "tilted15deg": 2},
        ),
    )


class WatchPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def build(self, cfg, **kwargs):
        args = dict(project_name="proj", folder_path=self.root, fixed_slice_id=1,
                    fixed_mosaic_slot=1, acquisition=None, slice_offset=0,
                    stability_seconds=0, poll_interval=1)
        args.update(kwargs)
        return build_watch_previews(cfg, **args)

    def worker_of(self, watcher):
        return watcher.discover_candidates.__self__

    def test_needs_slice_and_mosaic(self):
        cfg = scan_config(self.root)
        self.assertIsNone(self.build(cfg, fixed_slice_id=None))
        self.assertIsNone(self.build(cfg, fixed_mosaic_slot=None))

    def test_disabled_in_block(self):
        cfg = scan_config(self.root, batch_enabled=False, acquisition_enabled=False)
        self.assertIsNone(self.build(cfg))

    def test_acquisition_label_and_slice_offset(self):
        cfg = scan_config(self.root)
        worker = self.worker_of(self.build(cfg, fixed_slice_id=3, fixed_mosaic_slot=2,
                                           slice_offset=2))
        self.assertEqual(worker.acquisition, "tilted15deg")
        self.assertEqual(worker.ident, OCTMosaicId(project_name="proj", slice_id=5, mosaic_id=10))
        worker = self.worker_of(self.build(cfg, acquisition="normal0deg"))
        self.assertEqual(worker.acquisition, "normal0deg")
        self.assertEqual(worker.ident.mosaic_id, 1)


class CompanionTests(unittest.TestCase):
    def watcher(self, discover, poll_interval=1):
        return PollingStableWatcher(discover_candidates=discover, candidate_key=str,
                                    fingerprint=str, process=lambda c: 0,
                                    poll_interval=poll_interval, stability_seconds=0)

    def test_companions_run_while_main_watcher_is_busy(self):
        companion_ran = threading.Event()
        seen = {}

        def busy_main_discover():
            # Simulates a long MATLAB batch: returns only once the companion has
            # run (or after a timeout if companions were serialized behind it).
            seen["companion_during_main"] = companion_ran.wait(timeout=5)
            polling_watcher._shutdown_requested = True
            return []

        def broken_companion_discover():
            companion_ran.set()
            raise ValueError("bad preview pattern")

        with patch.object(polling_watcher.signal, "signal"):
            self.watcher(busy_main_discover).run(self.watcher(broken_companion_discover))
        self.assertTrue(seen["companion_during_main"])

    def test_acquisition_preview_is_checked_first(self):
        from opticstream.cli.oct.watch_enface import EnfacePreviewWatcher
        with TemporaryDirectory() as temp:
            worker = EnfacePreviewWatcher(scan_config(temp), OCTMosaicId(
                project_name="proj", slice_id=1, mosaic_id=1), temp, "normal0deg")
        self.assertEqual(worker.candidates, [None, 1, 2])


class StitchedEnfaceSlackTests(unittest.TestCase):
    def run_qc(self, root, **kwargs):
        outputs = {"aip": root / "mosaic_001_aip.nii.gz", "mask": root / "mosaic_001_mask.nii.gz"}
        for path in outputs.values():
            path.write_bytes(b"x")

        def convert(*, mosaic_ident, modality, nifti_path):
            jpeg = Path(str(nifti_path).replace(".nii.gz", ".jpg"))
            jpeg.write_bytes(b"jpeg")
            return jpeg

        ident = OCTMosaicId(project_name="proj", slice_id=1, mosaic_id=1)
        with patch.object(qc.convert_enface_nifti_to_jpeg_task, "submit",
                          side_effect=lambda **kw: SimpleNamespace(result=lambda: convert(**kw))), \
             patch.object(qc, "get_psoct_scan_config",
                          return_value=SimpleNamespace(stitched_enface_slack_upload=False)), \
             patch.object(qc, "upload_multiple_files_to_slack",
                          side_effect=lambda **kw: {p: True for p in kw["filepaths"]}) as upload, \
             patch.object(qc, "send_slack_message") as message, \
             patch.object(qc, "get_run_logger"):
            result = qc.mosaic_enface_qc_slack_upload_flow.fn(
                mosaic_ident=ident, enface_outputs=outputs, **kwargs)
        return result, upload, message

    def test_jpegs_written_but_not_posted_by_default(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            result, upload, message = self.run_qc(root)
            self.assertTrue((root / "mosaic_001_aip.jpg").is_file())
        upload.assert_not_called()
        message.assert_not_called()
        self.assertEqual(result, {"aip": False})

    def test_manual_override_posts(self):
        with TemporaryDirectory() as temp:
            result, upload, _ = self.run_qc(Path(temp), upload_to_slack=True)
        upload.assert_called_once()
        self.assertEqual(result, {"aip": True})


if __name__ == "__main__":
    unittest.main()
