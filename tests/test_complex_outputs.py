"""Optional saving, archiving and upload of complex tiles from spectral input."""
import gzip
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from niizarr import ZarrConfig

from opticstream.config.pipeline_opts_builder import build_pipeline_opts
from opticstream.config.psoct_scan_config import (
    PSOCTAcquisitionParams,
    PSOCTProcessingParams,
    PSOCTScanConfigModel,
)
from opticstream.flows.psoct import tile_batch_archive_flow as archive_flow
from opticstream.flows.psoct import tile_batch_process_flow as process_flow
from opticstream.state.oct_project_state import OCTBatchId


def scan_config(root="/tmp", archive=True, **processing):
    return PSOCTScanConfigModel(
        project_name="sub-T",
        project_base_path=Path(root) / "base",
        archive_path=Path(root) / "archive" if archive else None,
        acquisition=PSOCTAcquisitionParams(grid_size_x_normal=2, grid_size_x_tilted=2,
                                           grid_size_y=2),
        processing=PSOCTProcessingParams(**processing),
        zarr_config=ZarrConfig(),
    )


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = scan_config()
        self.assertFalse(cfg.processing.save_complex_outputs)
        self.assertEqual(
            cfg.complex_tile_name_format,
            "{project_name}_sample-slice{slice_id:02d}_chunk-{tile_id:04d}_acq-{acq}"
            "_desc-complex_OCT.nii.gz",
        )

    def test_matlab_opts_carry_complex_dir_only_when_set(self):
        cfg = scan_config()
        with_dir = build_pipeline_opts(cfg, complex_output_dir="/data/slice-01/complex")
        self.assertEqual(with_dir.output.to_matlab_struct()["ComplexOutputDir"],
                         "/data/slice-01/complex")
        self.assertNotIn("ComplexOutputDir", build_pipeline_opts(cfg).output.to_matlab_struct())


class BatchDecisionTests(unittest.TestCase):
    def decide(self, cfg, mode="spectral"):
        with patch.object(process_flow, "get_run_logger", return_value=Mock()):
            return process_flow.complex_output_dir_for_batch(cfg, mode, 3)

    def test_enabled_spectral_uses_slice_complex_folder(self):
        with TemporaryDirectory() as root:
            cfg = scan_config(root, save_complex_outputs=True)
            self.assertEqual(self.decide(cfg), Path(root) / "base" / "slice-03" / "complex")

    def test_off_by_default_and_needs_spectral_input_and_archive(self):
        with TemporaryDirectory() as root:
            self.assertIsNone(self.decide(scan_config(root)))
            self.assertIsNone(self.decide(scan_config(root, save_complex_outputs=True), "complex"))
            self.assertIsNone(self.decide(scan_config(root, archive=False, save_complex_outputs=True)))


class ArchiveComplexTests(unittest.TestCase):
    def test_archives_gzipped_with_name_format_and_removes_uncompressed_copies(self):
        with TemporaryDirectory() as root:
            root = Path(root)
            complex_dir = root / "complex"
            complex_dir.mkdir()
            refs = [SimpleNamespace(tile_number=n) for n in (5, 6)]
            for n in (5, 6):
                (complex_dir / f"mosaic_003_image_{n:04d}_complex.nii").write_bytes(b"jones%d" % n)
            batch = OCTBatchId(project_name="sub-T", slice_id=2, mosaic_id=3, batch_id=3)

            def submit(local, out):
                return SimpleNamespace(result=lambda: archive_flow.archive_file.fn(local, out))

            with patch.object(archive_flow, "get_run_logger", return_value=Mock()), \
                 patch("opticstream.tasks.archive_file.get_run_logger", return_value=Mock()), \
                 patch.object(archive_flow.archive_file, "submit", side_effect=submit), \
                 patch.object(archive_flow, "check_archive_result", return_value=[]):
                archived = archive_flow.archive_complex_tiles.fn(
                    batch, refs, acquisition_label="normal0deg", archive_path=root / "archive",
                    complex_tile_name_format="{project_name}_sample-slice{slice_id:02d}_chunk-"
                                             "{tile_id:04d}_acq-{acq}_desc-complex_OCT.nii.gz",
                    complex_dir=complex_dir)

            self.assertEqual([Path(p).name for p in archived], [
                "sub-T_sample-slice02_chunk-0005_acq-normal0deg_desc-complex_OCT.nii.gz",
                "sub-T_sample-slice02_chunk-0006_acq-normal0deg_desc-complex_OCT.nii.gz",
            ])
            with gzip.open(archived[0]) as f:
                self.assertEqual(f.read(), b"jones5")
            self.assertEqual(list(complex_dir.iterdir()), [])
            self.assertTrue(all(Path(p).is_file() for p in archived))

    def test_failed_validation_keeps_uncompressed_copies(self):
        with TemporaryDirectory() as root:
            root = Path(root)
            local = root / "mosaic_001_image_0001_complex.nii"
            local.write_bytes(b"jones")
            batch = OCTBatchId(project_name="sub-T", slice_id=1, mosaic_id=1, batch_id=1)
            with patch.object(archive_flow, "get_run_logger", return_value=Mock()), \
                 patch.object(archive_flow.archive_file, "submit",
                              return_value=SimpleNamespace(result=lambda: root / "a.nii.gz")), \
                 patch.object(archive_flow, "check_archive_result", return_value=["a.nii.gz (small)"]):
                with self.assertRaises(RuntimeError):
                    archive_flow.archive_complex_tiles.fn(
                        batch, [SimpleNamespace(tile_number=1)], acquisition_label="normal0deg",
                        archive_path=root, complex_tile_name_format="{tile_id}.nii.gz",
                        complex_dir=root)
            self.assertTrue(local.is_file())

    def test_missing_complex_output_fails_before_archiving(self):
        with TemporaryDirectory() as root:
            batch = OCTBatchId(project_name="sub-T", slice_id=1, mosaic_id=1, batch_id=1)
            with patch.object(archive_flow, "get_run_logger", return_value=Mock()), \
                 patch.object(archive_flow.archive_file, "submit") as submit:
                with self.assertRaises(FileNotFoundError):
                    archive_flow.archive_complex_tiles.fn(
                        batch, [SimpleNamespace(tile_number=1)], acquisition_label="normal0deg",
                        archive_path=Path(root), complex_tile_name_format="{tile_id}.nii.gz",
                        complex_dir=Path(root))
            submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
