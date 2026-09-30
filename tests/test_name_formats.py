"""Default output filename formats pad slice and tile/strip numbers to 4 digits."""
import unittest
from string import Formatter

from opticstream.config.lsm_scan_config import LSMScanConfigModel
from opticstream.config.psoct_scan_config import PSOCTScanConfigModel

NUMBERED = {"slice_id", "tile_id", "strip_id"}


def format_defaults(model):
    return {name: field.default for name, field in model.model_fields.items()
            if name.endswith("_format") and isinstance(field.default, str)}


class NameFormatDefaultsTests(unittest.TestCase):
    def test_numbers_are_zero_padded_to_four(self):
        formats = {**format_defaults(PSOCTScanConfigModel), **format_defaults(LSMScanConfigModel)}
        self.assertGreaterEqual(len(formats), 8)
        for name, template in formats.items():
            specs = {field: spec for _, field, spec, _ in Formatter().parse(template)
                     if field in NUMBERED}
            with self.subTest(format=name):
                self.assertIn("slice_id", specs)
                self.assertTrue(all(spec == "04d" for spec in specs.values()), specs)

    def test_rendered_names(self):
        psoct = format_defaults(PSOCTScanConfigModel)
        self.assertEqual(
            psoct["archive_tile_name_format"].format(project_name="p", slice_id=3, tile_id=41, acq="normal"),
            "p_slice-0003_tile-0041_acq-normal_OCT.nii.gz")
        self.assertEqual(
            psoct["mosaic_enface_format"].format(project_name="p", slice_id=3, acq="normal", modality="aip"),
            "p_sample-slice0003_acq-normal_proc-aip_OCT.nii.gz")


if __name__ == "__main__":
    unittest.main()
