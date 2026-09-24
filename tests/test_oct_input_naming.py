import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from opticstream.config.psoct_scan_config import PSOCTAcquisitionParams
from opticstream.utils.oct_input_naming import parse_input_name
from opticstream.flows.psoct.tile_file_reference import build_tile_file_reference_list
from opticstream.cli.oct.watch import OCTWatcherService


PATTERN = "sub-{subject}_sample-slice{slice}_chunk-{image}_acq-{acquisition}_{modality}{extension}"


class InputNamingTests(unittest.TestCase):
    def acquisition(self, **kwargs):
        return PSOCTAcquisitionParams(grid_size_x_normal=50, grid_size_x_tilted=50,
                                     grid_size_y=1, **kwargs)

    def test_examples_and_downstream(self):
        acq = self.acquisition(filename_pattern=PATTERN)
        config = SimpleNamespace(acquisition=acq, mosaics_per_slice=2)
        context = SimpleNamespace(grid_size_x=lambda cfg: 50)
        for tile, label, mosaic in [(41, "normal0deg", 73), (50, "normal0deg", 73), (50, "tilted15deg", 74)]:
            path = Path(f"sub-MF283_sample-slice037_chunk-{tile:04d}_acq-{label}_spectral.nii")
            parsed = parse_input_name(path.name, acq, 2)
            self.assertEqual((parsed.source_mosaic_id, parsed.image_index), (mosaic, tile))
            refs = build_tile_file_reference_list([path], config=config, mosaic_context=context)
            self.assertEqual(refs[tile].spectral_file_path, path)

    def test_custom_suffix_and_angle(self):
        acq = self.acquisition(filename_pattern=PATTERN, acquisition_mosaic_map={"normal7deg": 1},
                               filename_modality_map={"rawscan": "spectral"})
        parsed = parse_input_name("sub-X_sample-slice001_chunk-0001_acq-normal7deg_rawscan.nii.gz", acq, 2)
        self.assertEqual(parsed.modality, "spectral")
        self.assertIsNone(parse_input_name("unrelated.txt", acq, 2))

    def test_literal_suffix(self):
        acq = self.acquisition(filename_pattern="s{slice}_i{image}_{acquisition}.nii")
        self.assertEqual(parse_input_name("s1_i2_normal0deg.nii", acq, 2).modality, "spectral")

    def test_validation_and_legacy_default(self):
        self.assertIsNone(self.acquisition().filename_pattern)
        self.assertEqual(self.acquisition(filename_pattern="spectral_{image}.nii").filename_pattern, "spectral_{image}.nii")
        for pattern in ("{slice}_{acquisition}.nii", "{slice}_{image}_{acquisition}_{unknown}", "{slice}_{image}_{image}_{acquisition}"):
            with self.assertRaises(ValueError):
                self.acquisition(filename_pattern=pattern)

    def test_watcher_discovery(self):
        for pattern, name, mosaic in [(PATTERN, "sub-X_sample-slice037_chunk-0041_acq-normal0deg_spectral.nii", 73),
                                      (None, "mosaic_001_image_0041_spectral_0041.nii", 1)]:
            with TemporaryDirectory() as folder:
                path = Path(folder) / name
                path.write_bytes(b"\0" * (100 * 1024))
                watcher = OCTWatcherService(project_name="test", folder_path=Path(folder),
                    project_base_path=folder, mosaic_ranges=[(1, 100)], slice_offset=0,
                    batch_size=1, scan_config=SimpleNamespace(acquisition=self.acquisition(filename_pattern=pattern), mosaics_per_slice=2),
                    direct=True, force_resend=False)
                files = watcher._discover_two_mosaic_files()
                self.assertEqual(len(files), 1)
                self.assertEqual((files[0].source_mosaic_id, files[0].image_index), (mosaic, 41))
                candidates = watcher.discover_candidates()
                self.assertEqual(len(candidates), 1)
                self.assertEqual(candidates[0].source_mosaic_id, mosaic)

    def test_image_only_pattern_uses_supplied_slice_and_acquisition(self):
        acq = self.acquisition(filename_pattern="spectral_{image}.nii")
        parsed = parse_input_name("spectral_0041.nii", acq, 2)
        self.assertEqual((parsed.source_mosaic_id, parsed.image_index, parsed.modality), (None, 41, "spectral"))
        parsed = parse_input_name("spectral_0041.nii", acq, 2, slice_id=37, mosaic_slot=2)
        self.assertEqual((parsed.source_mosaic_id, parsed.image_index), (74, 41))
        config = SimpleNamespace(acquisition=acq, mosaics_per_slice=2)
        context = SimpleNamespace(grid_size_x=lambda cfg: 50)
        path = Path("spectral_0041.nii")
        refs = build_tile_file_reference_list([path], config=config, mosaic_context=context)
        self.assertEqual(refs[41].spectral_file_path, path)

    def test_supplied_values_filter_full_pattern(self):
        acq = self.acquisition(filename_pattern=PATTERN)
        name = "sub-X_sample-slice037_chunk-0041_acq-normal0deg_spectral.nii"
        self.assertEqual(parse_input_name(name, acq, 2, slice_id=37, mosaic_slot=1).source_mosaic_id, 73)
        self.assertIsNone(parse_input_name(name, acq, 2, slice_id=38))
        self.assertIsNone(parse_input_name(name, acq, 2, mosaic_slot=2))

    def test_resolve_fixed_mosaic(self):
        from opticstream.cli.oct.watch import resolve_fixed_mosaic
        cfg = SimpleNamespace(acquisition=self.acquisition(), mosaics_per_slice=2)
        self.assertEqual(resolve_fixed_mosaic(cfg), (None, None))
        self.assertEqual(resolve_fixed_mosaic(cfg, slice_id=37, acquisition="tilted15deg"), (37, 2))
        # --mosaic is the position within the slice (1 = normal, 2 = tilted).
        self.assertEqual(resolve_fixed_mosaic(cfg, mosaic=2), (None, 2))
        self.assertEqual(resolve_fixed_mosaic(cfg, slice_id=37, mosaic=1), (37, 1))
        self.assertEqual(resolve_fixed_mosaic(cfg, slice_id=37, acquisition="tilted15deg", mosaic=2), (37, 2))
        for kwargs in ({"acquisition": "unknown"}, {"mosaic": 3}, {"mosaic": 0},
                       {"acquisition": "normal0deg", "mosaic": 2}, {"slice_id": 0}):
            with self.assertRaises(ValueError):
                resolve_fixed_mosaic(cfg, **kwargs)

    def _watcher(self, folder, pattern, **fixed):
        return OCTWatcherService(project_name="test", folder_path=Path(folder),
            project_base_path=folder, mosaic_ranges=[(1, 100)], slice_offset=0,
            batch_size=1, scan_config=SimpleNamespace(acquisition=self.acquisition(filename_pattern=pattern), mosaics_per_slice=2),
            direct=True, force_resend=False, **fixed)

    def test_watcher_image_only_names(self):
        with TemporaryDirectory() as folder:
            (Path(folder) / "spectral_0041.nii").write_bytes(b"\0" * (100 * 1024))
            self.assertEqual(self._watcher(folder, "spectral_{image}.nii")._discover_two_mosaic_files(), [])
            watcher = self._watcher(folder, "spectral_{image}.nii", fixed_slice_id=37, fixed_mosaic_slot=1)
            candidates = watcher.discover_candidates()
            self.assertEqual(len(candidates), 1)
            self.assertEqual((candidates[0].source_mosaic_id, candidates[0].logical_batch), (73, 41))

    def test_watcher_filters_legacy_names(self):
        with TemporaryDirectory() as folder:
            for mosaic in (1, 2):
                (Path(folder) / f"mosaic_{mosaic:03d}_image_0041_spectral_0041.nii").write_bytes(b"\0" * (100 * 1024))
            files = self._watcher(folder, None, fixed_slice_id=1, fixed_mosaic_slot=2)._discover_two_mosaic_files()
            self.assertEqual([f.source_mosaic_id for f in files], [2])


if __name__ == "__main__":
    unittest.main()
