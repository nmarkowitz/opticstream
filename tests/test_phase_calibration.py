import unittest
from niizarr import ZarrConfig
from opticstream.config.psoct_scan_config import PSOCTScanConfigModel
from opticstream.config.pipeline_opts_builder import build_pipeline_opts
from psoct_toolbox.matlab_bridge import build_spectral2processed_batch_indexed_command


class PhaseCalibrationTests(unittest.TestCase):
    def config(self, **processing):
        return PSOCTScanConfigModel(project_name="test", project_base_path="D:/test",
            acquisition=dict(grid_size_x_normal=1, grid_size_x_tilted=1, grid_size_y=1),
            processing=processing, zarr_config=ZarrConfig())

    def test_legacy_defaults(self):
        opts = build_pipeline_opts(self.config()).spectral.to_matlab_struct()
        self.assertEqual(opts["interpolationMethod"], "wavelength")
        self.assertTrue(opts["flipChannel2Spectra"])
        self.assertFalse(opts["phaseCalibrationDispersion"])
        self.assertNotIn("dispCompFile", opts)  # MATLAB default, not literal 'None'

    def test_bridge(self):
        cfg = self.config(interpolation_method="phase_calibration", interpolation_path="D:/calibration",
                          phase_calibration_dispersion=True)
        opts = build_pipeline_opts(cfg)
        values = opts.spectral.to_matlab_struct()
        self.assertFalse(values["flipChannel2Spectra"])
        command = build_spectral2processed_batch_indexed_command(["input.nii"], output_dir="output",
            mosaic_id=1, tile_indices=[1], pipeline_opts=opts)
        for token in ("'interpolationMethod', 'phase_calibration'", "'phaseCalibrationDispersion', true",
                      "'flipChannel2Spectra', false", "'interpolationPath'"):
            self.assertIn(token, command)

    def test_invalid_options(self):
        for opts in (dict(interpolation_method="phase_calibration"), dict(phase_calibration_dispersion=True)):
            with self.assertRaises(ValueError):
                build_pipeline_opts(self.config(**opts))

    def test_individual_files(self):
        cfg = self.config(interpolation_method="phase_calibration",
            dphase_file="D:/cal/a.mat", lin_phase_file="D:/other/b.mat",
            dsp_phase_file="D:/third/c.mat", phase_calibration_dispersion=True)
        cfg = PSOCTScanConfigModel.model_validate(cfg.model_dump())
        opts = build_pipeline_opts(cfg)
        values = opts.spectral.to_matlab_struct()
        self.assertNotIn("interpolationPath", values)
        for key in ("dphaseFile", "linPhaseFile", "dspPhaseFile"):
            self.assertIn(key, values)
        command = build_spectral2processed_batch_indexed_command(["input.nii"], output_dir="output",
            mosaic_id=1, tile_indices=[1], pipeline_opts=opts)
        for key in ("dphaseFile", "linPhaseFile", "dspPhaseFile"):
            self.assertIn(key, command)
        cfg.processing.dsp_phase_file = None
        with self.assertRaises(ValueError):
            build_pipeline_opts(cfg)
        cfg.processing.phase_calibration_dispersion = False
        build_pipeline_opts(cfg)
        cfg.processing.lin_phase_file = None
        with self.assertRaises(ValueError):
            build_pipeline_opts(cfg)
        cfg.processing.interpolation_path = "D:/fallback"
        build_pipeline_opts(cfg)

    def test_flip_override_and_round_trip(self):
        cfg = self.config(interpolation_method="phase_calibration", interpolation_path="D:/calibration", flip_channel2_spectra=True)
        reloaded = PSOCTScanConfigModel.model_validate(cfg.model_dump())
        self.assertTrue(build_pipeline_opts(reloaded).spectral.to_matlab_struct()["flipChannel2Spectra"])
