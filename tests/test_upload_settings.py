import unittest
from unittest.mock import Mock, patch

from opticstream.utils.upload_settings import upload_flow_enabled, uploads_enabled


class UploadSettingsTests(unittest.TestCase):
    @patch("opticstream.utils.upload_settings.Variable.get")
    def test_destinations_and_default(self, get):
        get.return_value = True
        for instance in ("linc", "dandi"):
            self.assertTrue(uploads_enabled(instance))
            get.assert_called_with(f"{instance}-uploads-enabled", default=True)

    @patch("opticstream.utils.upload_settings.Variable.get", return_value="false")
    def test_reject_string(self, get):
        with self.assertRaises(ValueError):
            uploads_enabled("dandi")

    @patch("opticstream.utils.upload_settings.Variable.get")
    def test_live_toggle_skips_wrapped_milestone(self, get):
        milestone = Mock()

        @upload_flow_enabled
        def flow(dandi_instance="linc"):
            milestone()
            return "uploaded"

        get.return_value = False
        self.assertIsNone(flow())
        milestone.assert_not_called()
        get.return_value = True
        self.assertEqual(flow("dandi"), "uploaded")
        milestone.assert_called_once()
        get.assert_called_with("dandi-uploads-enabled", default=True)


if __name__ == "__main__":
    unittest.main()
