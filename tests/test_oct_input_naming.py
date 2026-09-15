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
        for pattern in ("{image}", "{slice}_{image}_{acquisition}_{unknown}", "{slice}_{image}_{image}_{acquisition}"):
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


if __name__ == "__main__":
    unittest.main()
