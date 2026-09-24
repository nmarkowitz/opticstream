import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import nibabel as nib
import numpy as np

from opticstream.config.enface_preview import EnfacePreviewConfig, PreviewGridConfig
from opticstream.config.psoct_scan_config import PSOCTAcquisitionParams
from opticstream.flows.psoct import acquisition_preview_flow as preview
from opticstream.cli.oct.watch_enface import EnfacePreviewWatcher
from opticstream.state.oct_project_state import OCTMosaicId


def config(root):
    return SimpleNamespace(project_base_path=Path(root) / "out", mosaics_per_slice=2,
        enface_preview=EnfacePreviewConfig(**{f"{m}_pattern": f"{m}_{{image:04d}}.nii" for m in preview.MODALITIES}),
        acquisition=PSOCTAcquisitionParams(grid_size_y=2, grid_size_x_normal=2,
            grid_size_x_tilted=3, tile_overlap=0))


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

    def test_patterns(self):
        for value in ("aip.nii", "../{image}.nii", "{unknown}_{image}.nii"):
            with self.assertRaises(ValueError):
                EnfacePreviewConfig(aip_pattern=value)
        files = preview.expected_tiles(self.cfg, self.ident, self.root, "normal", 2)
        self.assertEqual(list(files["aip"]), [2, 3])
        self.assertEqual(files["aip"][2].name, "aip_0003.nii")
        with self.assertRaises(ValueError):
            preview.expected_tiles(self.cfg, self.ident, self.root, "normal", 3)

    def test_plain_image_matches_any_padding(self):
        self.cfg.enface_preview.aip_pattern = "spectral_{image}_processed_aip.nii"
        (self.root / "spectral_0003_processed_aip.nii").write_bytes(b"x")
        (self.root / "spectral_4_processed_aip.nii").write_bytes(b"x")
        (self.root / "spectral_0003_processed_aip.nii.bak").write_bytes(b"x")
        files = preview.expected_tiles(self.cfg, self.ident, self.root, "normal", 2)
        self.assertEqual(files["aip"][2].name, "spectral_0003_processed_aip.nii")
        self.assertEqual(files["aip"][3].name, "spectral_4_processed_aip.nii")
        # Missing tiles keep a rendered name and are reported absent.
        files = preview.expected_tiles(self.cfg, self.ident, self.root, "normal", 1)
        self.assertEqual(files["aip"][0].name, "spectral_1_processed_aip.nii")
        self.assertFalse(files["aip"][0].exists())

    def test_plain_image_with_other_placeholders_and_ambiguity(self):
        self.cfg.enface_preview.aip_pattern = "s{slice}_m{mosaic:03d}_{acquisition}_{image}.nii"
        (self.root / "s1_m001_normal_01.nii").write_bytes(b"x")
        (self.root / "s1_m002_normal_02.nii").write_bytes(b"x")
        files = preview.expected_tiles(self.cfg, self.ident, self.root, "normal", 1)
        self.assertEqual(files["aip"][0].name, "s1_m001_normal_01.nii")
        self.assertFalse(files["aip"][1].exists())
        (self.root / "s1_m001_normal_1.nii").write_bytes(b"x")
        with self.assertRaises(ValueError):
            preview.expected_tiles(self.cfg, self.ident, self.root, "normal", 1)

    def test_geometry(self):
        self.assertEqual(preview.tile_position(2, 2, (10, 20), .2, "column-by-column"), (8, 0))
        self.assertEqual(preview.tile_position(2, 2, (10, 20), .2, "snake-by-columns"), (8, 16))
        self.assertEqual(preview.tile_position(2, 2, (10, 20), .2, "snake-by-columns", 2), (0, 16))

    def test_grid_modes_match_linc_convert(self):
        from linc_convert.modalities.psoct import generate_tile_config as grids
        generators = {
            "row-by-row": grids._generate_row_by_row,
            "column-by-column": grids._generate_column_by_column,
            "snake-by-rows": grids._generate_snake_by_rows,
            "snake-by-columns": grids._generate_snake_by_columns,
        }
        for mode, generator in generators.items():
            row_based = mode in {"row-by-row", "snake-by-rows"}
            orders = ["right-down", "left-down", "right-up", "left-up"] if row_based else ["down-right", "down-left", "up-right", "up-left"]
            for order in orders:
                with self.subTest(mode=mode, order=order):
                    # Non-square acquisition: 3 strips of 2 tiles.
                    expected = generator(2 if row_based else 3, 3 if row_based else 2,
                                         10, 20, .2, .2, order, "tile_{tile_number:04d}.nii")
                    actual = [preview.tile_position(i, 2, (10, 20), .2, mode,
                              order=order, columns=3) for i in range(6)]
                    self.assertEqual(actual, [(t["x"], t["y"]) for t in expected])
                    for batch in range(1, 4):
                        points = actual[(batch - 1) * 2:batch * 2]
                        cropped = [(x-min(p[0] for p in points), y-min(p[1] for p in points)) for x, y in points]
                        strip = [preview.tile_position(i, 2, (10, 20), .2, mode, batch,
                                 order=order, columns=3) for i in range((batch-1)*2, batch*2)]
                        self.assertEqual(strip, cropped)

    def test_grid_schema_validation_and_legacy_migration(self):
        for mode in ("column-by-column", "snake-by-columns"):
            migrated = EnfacePreviewConfig.model_validate({"traversal": mode})
            self.assertEqual(migrated.grid_config.grid_type, mode)
            self.assertEqual(migrated.grid_config.order, "down-right")
            self.assertNotIn("traversal", migrated.model_dump())
        with self.assertRaises(ValueError):
            PreviewGridConfig(grid_type="row-by-row", order="down-right")
        cfg = EnfacePreviewConfig(grid_config={"grid_type": "snake-by-rows", "order": "left-up"})
        self.assertEqual(cfg.grid_config.order, "left-up")

    @patch("opticstream.cli.oct.watch_enface.slack_notifications_enabled", return_value=False)
    def test_complete_batches_and_acquisition_only(self, slack):
        watcher = EnfacePreviewWatcher(self.cfg, self.ident, self.root, "normal")
        self.create_tiles(2)
        self.assertEqual(list(watcher.discover()), [1])
        self.create_tiles(4)
        self.assertEqual(list(watcher.discover()), [None, 1, 2])
        (self.root / "ret_0004.nii").unlink()
        self.assertEqual(list(watcher.discover()), [1])

    def test_tilted_grid_and_duplicate_patterns(self):
        ident = OCTMosaicId(project_name="test", slice_id=1, mosaic_id=2)
        files = preview.expected_tiles(self.cfg, ident, self.root, "tilted")
        self.assertEqual(len(files["aip"]), 6)
        self.cfg.enface_preview.mip_pattern = self.cfg.enface_preview.aip_pattern
        with self.assertRaises(ValueError):
            preview.expected_tiles(self.cfg, ident, self.root, "tilted")

    @patch.object(preview, "slack_notifications_enabled", return_value=False)
    def test_checkpoint_resume_then_slack(self, slack):
        self.create_tiles()
        def stitch(cfg, files, modality, output, batch_id):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"nifti")
            output.with_suffix(".jpg").write_bytes(b"jpeg")
        with patch.object(preview, "stitch_preview_modality", side_effect=stitch) as stitch_mock, \
             patch.object(preview, "upload_multiple_files_to_slack") as upload:
            preview.preview_enface_batch.fn(self.cfg, self.ident, self.root, "normal", 1)
            self.assertEqual(stitch_mock.call_count, 4)
            upload.assert_not_called()
            preview.preview_enface_batch.fn(self.cfg, self.ident, self.root, "normal", 1)
            self.assertEqual(stitch_mock.call_count, 4)
            slack.return_value = True
            upload.side_effect = lambda **kw: {p: True for p in kw["filepaths"]}
            preview.preview_enface_batch.fn(self.cfg, self.ident, self.root, "normal", 1)
            preview.preview_enface_batch.fn(self.cfg, self.ident, self.root, "normal", 1)
            upload.assert_called_once()
            self.assertEqual(stitch_mock.call_count, 4)

    def test_disabled_flow_no_io(self):
        self.cfg.enface_preview.batch_enabled = False
        with patch.object(preview, "stitch_preview_modality") as stitch:
            self.assertEqual(preview.preview_enface_batch.fn(self.cfg, self.ident, self.root, "normal", 1), {})
            stitch.assert_not_called()

    @patch.object(preview, "slack_notifications_enabled", return_value=True)
    def test_failed_upload_retries_only_missing_modalities(self, slack):
        self.create_tiles()
        def stitch(cfg, files, modality, output, batch_id):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"nifti")
            output.with_suffix(".jpg").write_bytes(b"jpeg")
        with patch.object(preview, "stitch_preview_modality", side_effect=stitch) as stitched, \
             patch.object(preview, "upload_multiple_files_to_slack") as upload:
            upload.side_effect = lambda **kw: {p: not Path(p).stem.endswith("_ret") for p in kw["filepaths"]}
            with self.assertRaises(RuntimeError):
                preview.run_preview(self.cfg, self.ident, self.root, "normal", 1)
            upload.side_effect = lambda **kw: {p: True for p in kw["filepaths"]}
            preview.run_preview(self.cfg, self.ident, self.root, "normal", 1)
            self.assertEqual(len(upload.call_args.kwargs["filepaths"]), 1)
            self.assertEqual(stitched.call_count, 4)

    @patch.object(preview, "slack_notifications_enabled", return_value=False)
    def test_outputs_in_slice_folder_named_by_mosaic(self, slack):
        self.create_tiles()
        def stitch(cfg, files, modality, output, batch_id):
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"nifti")
            output.with_suffix(".jpg").write_bytes(b"jpeg")
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
                self.assertIn(f"{prefix}_{m}.nii", names)
                self.assertIn(f"{prefix}_{m}.jpg", names)
            self.assertIn(f"{prefix}_progress.json", names)
        self.assertEqual(len(names), 3 * (2 * len(preview.MODALITIES) + 1))

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
        worker = EnfacePreviewWatcher(self.cfg, self.ident, self.root, "normal")
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
        self.create_tiles()
        files = preview.expected_tiles(self.cfg, self.ident, self.root, "normal")
        for modality in ("aip", "ori"):
            output = self.root / "stitched" / f"{modality}.nii"
            jpeg = preview.stitch_preview_modality.fn(self.cfg, files[modality], modality, output)
            self.assertTrue(jpeg.is_file())
            result = nib.load(output).get_fdata()
            self.assertEqual(result.shape, (16, 12))
            self.assertAlmostEqual(result[3, 3], 1, places=4)
            self.assertAlmostEqual(result[11, 9], 4, places=4)


if __name__ == "__main__":
    unittest.main()
