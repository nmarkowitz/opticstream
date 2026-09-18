import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from opticstream.config.psoct_scan_config import PSOCTScanConfigModel
from opticstream.utils.matlab_settings import matlab_flow_enabled


class MatlabSettingsTests(unittest.TestCase):
    def test_default_and_schema(self):
        field = PSOCTScanConfigModel.model_fields["matlab_processing_enabled"]
        self.assertIs(field.default, True)
        self.assertIs(field.annotation, bool)

    @patch("opticstream.utils.matlab_settings.get_run_logger")
    def test_block_isolation_and_enabled_execution(self, logger):
        body = Mock(return_value="processed")

        @matlab_flow_enabled
        def run(config, *, force_rerun=False):
            return body()

        disabled = SimpleNamespace(matlab_processing_enabled=False)
        enabled = SimpleNamespace(matlab_processing_enabled=True)
        self.assertEqual(run(disabled, force_rerun=True).name, "MatlabDisabled")
        body.assert_not_called()
        self.assertEqual(run(enabled), "processed")
        body.assert_called_once()

    @patch("opticstream.utils.matlab_settings.get_run_logger")
    @patch("opticstream.utils.matlab_settings.get_psoct_scan_config")
    def test_actual_tile_flow_skips_milestone_and_archive(self, load, logger):
        from opticstream.flows.psoct.tile_batch_process_flow import process_tile_batch

        with patch("opticstream.flows.psoct.tile_batch_process_flow.OCT_STATE_SERVICE") as state, \
             patch("opticstream.flows.psoct.tile_batch_process_flow.archive_tile_batch") as archive, \
             patch("opticstream.flows.psoct.tile_batch_process_flow.run_matlab_batch_command_or_cli") as matlab:
            result = process_tile_batch.fn(
                batch_id=None, config=SimpleNamespace(matlab_processing_enabled=False),
                file_list=[], force_rerun=True,
            )
            self.assertEqual(result.name, "MatlabDisabled")
            self.assertEqual(state.mock_calls, [])
            archive.submit.assert_not_called()
            matlab.assert_not_called()
            load.assert_not_called()

    @patch("opticstream.utils.matlab_settings.get_run_logger")
    @patch("opticstream.utils.matlab_settings.get_psoct_scan_config")
    def test_actual_registration_loads_block_before_io(self, load, logger):
        from opticstream.flows.psoct.slice_process_flow import register_slice_flow

        load.return_value = SimpleNamespace(matlab_processing_enabled=False)
        with patch("opticstream.flows.psoct.slice_process_flow.thruplane_from_files_task") as matlab:
            result = register_slice_flow.fn("disabled-project", "missing", 1, 1, 2)
            self.assertEqual(result.name, "MatlabDisabled")
            load.assert_called_once_with("disabled-project")
            matlab.assert_not_called()

    def test_disabled_completion_does_not_emit_mosaic_ready(self):
        from opticstream.hooks.check_mosaic_ready_hook import check_mosaic_ready_hook

        with patch("opticstream.hooks.check_mosaic_ready_hook.emit_mosaic_psoct_event") as emit:
            check_mosaic_ready_hook(None, None, SimpleNamespace(name="MatlabDisabled"))
            emit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
