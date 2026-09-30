import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import nibabel as nib
import numpy as np

from niizarr import ZarrConfig

from opticstream.config.psoct_scan_config import PSOCTAcquisitionParams, PSOCTScanConfigModel
from opticstream.flows.psoct import acquisition_preview_flow as preview
from opticstream.cli.oct.watch_enface import EnfacePreviewWatcher
from opticstream.state.oct_project_state import OCTMosaicId


def config(root, pattern="{modality}_{image}.nii"):
    return SimpleNamespace(project_base_path=Path(root) / "out", mosaics_per_slice=2,
        acquisition=PSOCTAcquisitionParams(grid_size_y=2, grid_size_x_normal=2,
            grid_size_x_tilted=3, tile_overlap=0, filename_pattern=pattern))


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = config(self.root)
        self.ident = OCTMosaicId(project_name="test", slice_id=1, mosaic_id=1)

    def create_tiles(self, count=4):
        for m in preview.MODALITIES:
            for i in range(1, count + 1):
                data = np.full((8, 6, 1), i, dtype=np.float32)
                nib.save(nib.Nifti1Image(data, np.eye(4)), self.root / f"{m}_{i:04d}.nii")

    def test_naming_must_cover_enface_maps(self):
        self.assertIsNone(preview.naming_error(self.cfg))
        for pattern in (None, "spectral_{image}.nii"):
            cfg = config(self.root, pattern)
            self.assertIsNotNone(preview.naming_error(cfg))
            with self.assertRaises(ValueError):
                preview.expected_tiles(cfg, self.ident, self.root, "normal")
        cfg = config(self.root)
        cfg.acquisition.filename_modality_map = {"spectral": "spectral", "aip": "aip"}
        self.assertIn("mip, ori, ret", preview.naming_error(cfg))

    def test_expected_tiles_by_batch(self):
        self.create_tiles()
        files = preview.expected_tiles(self.cfg, self.ident, self.root, "normal", 2)
        self.assertEqual(list(files["aip"]), [2, 3])
        self.assertEqual(files["aip"][2].name, "aip_0003.nii")
        self.assertEqual(files["ori"][3].name, "ori_0004.nii")
        with self.assertRaises(ValueError):
            preview.expected_tiles(self.cfg, self.ident, self.root, "normal", 3)

    def test_modality_labels_and_any_padding(self):
        self.cfg = config(self.root, "spectral_{image}_{modality}.nii")
        self.cfg.acquisition.filename_modality_map = {
            "spectral": "spectral", "processed_aip": "aip", "processed_mip": "mip",
            "processed_orientation": "ori", "processed_retardance": "ret"}
        for name in ("spectral_0003_processed_aip.nii", "spectral_4_processed_aip.nii",
                     "spectral_0003_processed_orientation.nii", "spectral_0003_spectral.nii",
                     "spectral_0003_processed_surface_finding.nii",
                     "spectral_0003_processed_aip.nii.bak"):
            (self.root / name).write_bytes(b"x")
        files = preview.expected_tiles(self.cfg, self.ident, self.root, "normal", 2)
        self.assertEqual(files["aip"][2].name, "spectral_0003_processed_aip.nii")
        self.assertEqual(files["aip"][3].name, "spectral_4_processed_aip.nii")
        self.assertEqual(files["ori"][2].name, "spectral_0003_processed_orientation.nii")
        # Missing tiles get a path that does not exist.
        self.assertFalse(files["mip"][2].exists())
        files = preview.expected_tiles(self.cfg, self.ident, self.root, "normal", 1)
        self.assertFalse(files["aip"][0].exists())

    def test_slice_and_acquisition_filter_and_ambiguity(self):
        self.cfg = config(self.root, "s{slice}_{acq}_{modality}_{image}.nii")
        for name in ("s1_normal_aip_01.nii", "s2_normal_aip_02.nii", "s1_tilted_aip_02.nii"):
            (self.root / name).write_bytes(b"x")
        files = preview.expected_tiles(self.cfg, self.ident, self.root, "normal", 1)
        self.assertEqual(files["aip"][0].name, "s1_normal_aip_01.nii")
        self.assertFalse(files["aip"][1].exists())
        (self.root / "s1_normal_aip_1.nii").write_bytes(b"x")
        with self.assertRaises(ValueError):
            preview.expected_tiles(self.cfg, self.ident, self.root, "normal", 1)

    def test_block_migration_drops_enface_preview(self):
        legacy = dict(
            project_name="p", project_base_path=str(self.root), zarr_config=ZarrConfig(),
            acquisition=dict(grid_size_y=2, grid_size_x_normal=2, grid_size_x_tilted=2),
            enface_preview=dict(aip_pattern="aip_{image}.nii", batch_enabled=True,
                                grid_config={"grid_type": "row-by-row", "order": "right-down"},
                                orientation_units="radians"))
        migrated = PSOCTScanConfigModel.model_validate(legacy)
        self.assertNotIn("enface_preview", migrated.model_dump())
        self.assertEqual(migrated.acquisition.orientation_units, "radians")
        legacy["enface_preview"] = {}
        self.assertEqual(
            PSOCTScanConfigModel.model_validate(legacy).acquisition.orientation_units, "degrees")

    def layout(self, files):
        self.cfg.acquisition.tile_overlap = 20
        with TemporaryDirectory() as scratch:
            out = preview.tile_config(self.cfg, files, (10, 20), .2, Path(scratch) / "tiles.yaml")
            import yaml
            return {t["tile_number"] - 1: (t["x"], t["y"], t["filepath"])
                    for t in yaml.safe_load(out.read_text())["tiles"]}

    def test_tilted_mosaic_uses_grid_size_y_tilted(self):
        self.cfg.acquisition.grid_size_y_tilted = 3
        tilted = OCTMosaicId(project_name="test", slice_id=1, mosaic_id=2)
        self.assertEqual(list(preview.expected_tiles(self.cfg, tilted, self.root, "t", 2)["aip"]),
                         [3, 4, 5])
        self.assertEqual(len(preview.expected_tiles(self.cfg, tilted, self.root, "t")["aip"]), 9)
        # Normal mosaics keep grid_size_y.
        self.assertEqual(list(preview.expected_tiles(self.cfg, self.ident, self.root, "n", 2)["aip"]),
                         [2, 3])
        self.create_tiles(9)
        def stitch(cfg, files, modality, output, batch_id, per_strip=None):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"jpeg")
        with patch.object(preview, "stitch_preview_modality", side_effect=stitch) as stitched, \
             patch.object(preview, "slack_notifications_enabled", return_value=False):
            preview.run_preview(self.cfg, tilted, self.root, "t")
        self.assertEqual({c.kwargs["per_strip"] for c in stitched.call_args_list}, {3})
        # 3 strips of 3 tiles: tile 4 starts the second strip.
        files = {i: Path(f"/data/image_{i + 1}.nii") for i in range(9)}
        with TemporaryDirectory() as scratch:
            import yaml
            out = preview.tile_config(self.cfg, files, (10, 20), 0, Path(scratch) / "t.yaml", 3)
            xs = {t["tile_number"]: t["x"] for t in yaml.safe_load(out.read_text())["tiles"]}
        self.assertEqual(len(set(xs.values())), 3)
        self.assertEqual(xs[1], xs[3])
        self.assertNotEqual(xs[3], xs[4])

    def test_tile_config_uses_linc_convert_grid(self):
        from linc_convert.modalities.psoct import generate_tile_config as grids
        # Fixed traversal; non-square acquisition: 3 strips of 2 tiles.
        self.assertEqual((preview.GRID_TYPE, preview.GRID_ORDER), ("snake-by-columns", "up-right"))
        expected = grids._generate_snake_by_columns(3, 2, 10, 20, .2, .2, "up-right",
                                                    "tile_{tile_number:04d}.nii")
        expected = [(round(t["x"]), round(t["y"])) for t in expected]
        files = {i: Path(f"/data/image_{i + 7}.nii") for i in range(6)}
        actual = self.layout(files)
        x0, y0 = min(x for x, _ in expected), min(y for _, y in expected)
        self.assertEqual([actual[i][:2] for i in range(6)],
                         [(x - x0, y - y0) for x, y in expected])
        self.assertEqual(actual[4][2], str(Path("/data/image_11.nii")))
        for batch in range(1, 4):
            points = expected[(batch - 1) * 2:batch * 2]
            cropped = [(x - min(p[0] for p in points), y - min(p[1] for p in points)) for x, y in points]
            strip = self.layout({i: files[i] for i in range((batch - 1) * 2, batch * 2)})
            self.assertEqual([strip[i][:2] for i in sorted(strip)], cropped)

    @patch("opticstream.cli.oct.watch_enface.slack_notifications_enabled", return_value=False)
    def test_complete_batches_and_acquisition_only(self, slack):
        watcher = EnfacePreviewWatcher(self.cfg, self.ident, self.root, "normal",
                                       batch_previews=True)
        self.create_tiles(2)
        self.assertEqual(list(watcher.discover()), [1])
        self.create_tiles(4)
        self.assertEqual(list(watcher.discover()), [None, 1, 2])
        (self.root / "ret_0004.nii").unlink()
        self.assertEqual(list(watcher.discover()), [1])

    def test_preview_switches_choose_candidates(self):
        make = lambda **kw: EnfacePreviewWatcher(self.cfg, self.ident, self.root, "normal", **kw)
        self.assertEqual(make().candidates, [None])
        self.assertEqual(make(batch_previews=True).candidates, [None, 1, 2])
        self.assertEqual(make(acquisition_previews=False, batch_previews=True).candidates, [1, 2])
        self.assertEqual(make(acquisition_previews=False).candidates, [])

    def test_tilted_grid(self):
        ident = OCTMosaicId(project_name="test", slice_id=1, mosaic_id=2)
        files = preview.expected_tiles(self.cfg, ident, self.root, "tilted")
        self.assertEqual(len(files["aip"]), 6)

    @patch.object(preview, "slack_notifications_enabled", return_value=False)
    def test_checkpoint_resume_then_slack(self, slack):
        self.create_tiles()
        def stitch(cfg, files, modality, output, batch_id, per_strip=None):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"jpeg")
        with patch.object(preview, "stitch_preview_modality", side_effect=stitch) as stitch_mock, \
             patch.object(preview, "upload_multiple_files_to_slack") as upload:
            preview.preview_enface_batch.fn(self.cfg, self.ident, self.root, "normal", 1)
            self.assertEqual(stitch_mock.call_count, len(preview.MODALITIES))
            upload.assert_not_called()
            preview.preview_enface_batch.fn(self.cfg, self.ident, self.root, "normal", 1)
            self.assertEqual(stitch_mock.call_count, len(preview.MODALITIES))
            slack.return_value = True
            upload.side_effect = lambda **kw: {p: True for p in kw["filepaths"]}
            preview.preview_enface_batch.fn(self.cfg, self.ident, self.root, "normal", 1)
            preview.preview_enface_batch.fn(self.cfg, self.ident, self.root, "normal", 1)
            upload.assert_called_once()
            self.assertEqual(stitch_mock.call_count, len(preview.MODALITIES))

    @patch.object(preview, "slack_notifications_enabled", return_value=True)
    def test_failed_upload_retries_only_missing_modalities(self, slack):
        self.create_tiles()
        def stitch(cfg, files, modality, output, batch_id, per_strip=None):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"jpeg")
        with patch.object(preview, "stitch_preview_modality", side_effect=stitch) as stitched, \
             patch.object(preview, "upload_multiple_files_to_slack") as upload:
            upload.side_effect = lambda **kw: {p: not Path(p).stem.endswith("_ret") for p in kw["filepaths"]}
            with self.assertRaises(RuntimeError):
                preview.run_preview(self.cfg, self.ident, self.root, "normal", 1)
            upload.side_effect = lambda **kw: {p: True for p in kw["filepaths"]}
            preview.run_preview(self.cfg, self.ident, self.root, "normal", 1)
            self.assertEqual(len(upload.call_args.kwargs["filepaths"]), 1)
            self.assertEqual(stitched.call_count, len(preview.MODALITIES))

    @patch.object(preview, "slack_notifications_enabled", return_value=False)
    def test_outputs_in_slice_folder_named_by_mosaic(self, slack):
        self.create_tiles()
        def stitch(cfg, files, modality, output, batch_id, per_strip=None):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"jpeg")
        folder = self.root / "out" / "slice-01" / "acquisition_previews"
        with patch.object(preview, "stitch_preview_modality", side_effect=stitch):
            result = preview.run_preview(self.cfg, self.ident, self.root, "normal")
            preview.run_preview(self.cfg, self.ident, self.root, "normal", 2)
            mosaic2 = OCTMosaicId(project_name="test", slice_id=1, mosaic_id=2)
            self.cfg.acquisition.grid_size_x_tilted = 2
            preview.run_preview(self.cfg, mosaic2, self.root, "tilted")
        self.assertEqual(result["aip"], str(folder / "mosaic_001_aip.jpg"))
        names = {p.name for p in folder.iterdir()}
        for prefix in ("mosaic_001", "mosaic_001_batch_0002", "mosaic_002"):
            for m in preview.MODALITIES:
                self.assertIn(f"{prefix}_{m}.jpg", names)
            self.assertIn(f"{prefix}_progress.json", names)
        self.assertEqual(len(names), 3 * (len(preview.MODALITIES) + 1))  # JPEGs only, no NIfTI

    def test_changed_input_invalidates_progress(self):
        self.create_tiles()
        files = preview.expected_tiles(self.cfg, self.ident, self.root, "normal", 1)
        before = preview.fingerprint(self.cfg, files)
        progress_file = preview.progress_path(self.cfg, self.ident, 1)
        preview.save_progress(progress_file, {"signature": before, "stitched": list(preview.MODALITIES), "uploaded": []})
        with (self.root / "aip_0001.nii").open("ab") as stream:
            stream.write(b"changed")
        after = preview.fingerprint(self.cfg, files)
        self.assertNotEqual(before, after)
        self.assertEqual(preview.read_progress(progress_file, after)["stitched"], [])

    @patch("opticstream.cli.oct.watch_enface.slack_notifications_enabled", return_value=False)
    def test_polling_waits_for_stability(self, slack):
        from opticstream.utils.polling_watcher import PollingStableWatcher
        self.create_tiles(2)
        worker = EnfacePreviewWatcher(self.cfg, self.ident, self.root, "normal",
                                      batch_previews=True)
        with patch.object(worker, "process", return_value=1) as process:
            poller = PollingStableWatcher(discover_candidates=worker.discover,
                candidate_key=lambda batch: batch, fingerprint=worker.fingerprint,
                process=process, stability_seconds=15)
            with patch("opticstream.utils.polling_watcher.time.time") as clock:
                for instant in (0, 1, 10):
                    clock.return_value = instant
                    poller._run_iteration()
                process.assert_not_called()
                clock.return_value = 17
                poller._run_iteration()
                process.assert_called_once_with(1)

    def test_real_mosaic2d_singleton_nifti(self):
        from linc_convert.modalities.psoct import mosaic as linc_mosaic
        self.create_tiles()
        files = preview.expected_tiles(self.cfg, self.ident, self.root, "normal")
        stitched = {}
        real_mosaic2d = linc_mosaic.mosaic2d

        def capture(*args, nifti_output, **kwargs):
            # The stitched NIfTI lives only in a scratch dir; read it before cleanup.
            real_mosaic2d(*args, nifti_output=nifti_output, **kwargs)
            stitched["data"] = nib.load(nifti_output, mmap=False).get_fdata().squeeze()

        for modality in ("aip", "ori"):
            output = self.root / "stitched" / f"{modality}.jpg"
            with patch.object(linc_mosaic, "mosaic2d", side_effect=capture):
                jpeg = preview.stitch_preview_modality.fn(self.cfg, files[modality], modality, output)
            self.assertEqual(jpeg, output)
            self.assertTrue(jpeg.is_file())
            # Only the JPEG is written next to the previews.
            self.assertEqual(sorted(p.name for p in output.parent.iterdir()),
                             sorted({"aip.jpg", modality + ".jpg"}))
            result = stitched["data"]
            self.assertEqual(result.shape, (16, 12))
            # snake-by-columns / up-right: strip 1 goes up from the bottom-left
            # (tile 1 below tile 2), strip 2 comes back down (tile 3 above tile 4).
            self.assertAlmostEqual(result[3, 9], 1, places=4)
            self.assertAlmostEqual(result[3, 3], 2, places=4)
            self.assertAlmostEqual(result[11, 3], 3, places=4)
            self.assertAlmostEqual(result[11, 9], 4, places=4)

    def test_surf_previewed_only_when_mapped(self):
        self.assertIn("surf", preview.preview_modalities(self.cfg))
        self.cfg.acquisition.filename_modality_map = {m: m for m in preview.REQUIRED_MODALITIES}
        self.assertEqual(preview.preview_modalities(self.cfg), preview.REQUIRED_MODALITIES)
        self.assertIsNone(preview.naming_error(self.cfg))
        self.create_tiles()
        self.assertNotIn("surf", preview.expected_tiles(self.cfg, self.ident, self.root, "normal"))


if __name__ == "__main__":
    unittest.main()
