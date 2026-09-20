from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from niizarr import ZarrConfig
from psoct_toolbox.matlab_bridge import build_spectral2processed_batch_indexed_command, build_complex2processed_batch_indexed_command

from opticstream.config.pipeline_opts_builder import build_pipeline_opts
from opticstream.config.psoct_scan_config import PSOCTScanConfigModel, PSOCTProcessingParams
from opticstream.flows.psoct.tile_batch_processed_validation import validate_processed_batch_outputs
from opticstream.flows.psoct.tile_file_reference import TileFileReference
from opticstream.state.oct_project_state import OCTMosaicId


def config(tmp_path, enabled):
    return PSOCTScanConfigModel(project_name="test", project_base_path=tmp_path,
        acquisition=dict(grid_size_x_normal=1, grid_size_x_tilted=1, grid_size_y=1),
        processing=dict(save_volume_outputs=enabled), enface_modalities=["aip"], zarr_config=ZarrConfig())


def _check_command_flag_and_enface_validation(tmp_path, enabled):
    cfg = config(tmp_path, enabled)
    opts = build_pipeline_opts(cfg)
    for builder in (build_spectral2processed_batch_indexed_command, build_complex2processed_batch_indexed_command):
        command = builder(["input.nii"], output_dir=str(tmp_path), mosaic_id=1, tile_indices=[1], pipeline_opts=opts)
        assert f"'SaveVolumeOutputs', {str(enabled).lower()}" in command
    (tmp_path / "mosaic_001_image_0001_aip.nii").write_bytes(b"enface")
    args = dict(processed_dir=tmp_path, file_reference_list=[TileFileReference(tile_number=1)],
                mosaic_id=1, config=cfg, min_enface_file_size_bytes=1)
    if enabled:
        with unittest.TestCase().assertRaisesRegex(RuntimeError, "volume"):
            validate_processed_batch_outputs(**args)
    else:
        validate_processed_batch_outputs(**args)
        (tmp_path / "mosaic_001_image_0001_aip.nii").unlink()
        with unittest.TestCase().assertRaisesRegex(RuntimeError, "enface"):
            validate_processed_batch_outputs(**args)


def _check_existing_blocks_default_enabled():
    assert PSOCTProcessingParams.model_validate({}).save_volume_outputs is True


def _check_disabled_upload_skips_all_milestones(tmp_path):
    from opticstream.flows.psoct.mosaic_volume_upload_flow import upload_mosaic_volume_to_dandi_flow
    cfg = config(tmp_path, False)
    with patch("opticstream.utils.volume_settings.get_psoct_scan_config", return_value=cfg), \
         patch("opticstream.state.milestone_wrappers_psoct.OCT_STATE_SERVICE") as state, \
         patch("opticstream.flows.psoct.mosaic_volume_upload_flow.upload_to_dandi_batch") as upload:
        result = upload_mosaic_volume_to_dandi_flow.fn(
            mosaic_ident=OCTMosaicId(project_name="test", slice_id=1, mosaic_id=1), volume_outputs={"dBI": "old.nii"})
        assert result == {"uploaded": 0}
        assert not state.mock_calls
        upload.assert_not_called()


def _check_disabled_stitch_skips_before_volume_milestone(tmp_path):
    from opticstream.flows.psoct import mosaic_volume_stitch_flow as module
    @contextmanager
    def opened(**kwargs):
        yield Mock()
    with patch.object(module, "get_run_logger", return_value=Mock()), \
         patch.object(module.OCT_STATE_SERVICE, "open_mosaic", side_effect=opened), \
         patch.object(module, "enter_milestone_stage") as enter, \
         patch.object(module, "emit_mosaic_psoct_event") as emit:
        result = module.stitch_volume_flow.fn(
            mosaic_ident=OCTMosaicId(project_name="test", slice_id=1, mosaic_id=1), config=config(tmp_path, False))
        assert result == {}
        enter.assert_not_called()
        emit.assert_not_called()


class VolumeOutputTests(unittest.TestCase):
    def test_commands_and_validation(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled), TemporaryDirectory() as folder:
                _check_command_flag_and_enface_validation(Path(folder), enabled)

    def test_default(self):
        _check_existing_blocks_default_enabled()

    def test_upload(self):
        with TemporaryDirectory() as folder:
            _check_disabled_upload_skips_all_milestones(Path(folder))

    def test_stitch(self):
        with TemporaryDirectory() as folder:
            _check_disabled_stitch_skips_before_volume_milestone(Path(folder))
