import json
import unittest
from unittest.mock import Mock, patch

from skill_gather.integrations.yt_dlp import YtDlpClient, YtDlpError, sanitize_command_output


class YtDlpClientTests(unittest.TestCase):
    def test_probe_metadata_parses_dump_json(self):
        completed = Mock(
            returncode=0,
            stdout=json.dumps({"id": "BV1xx411c7mD", "title": "Demo"}),
            stderr="",
        )

        with patch("skill_gather.integrations.yt_dlp.subprocess.run", return_value=completed) as run:
            result = YtDlpClient(binary="yt-dlp").probe_metadata("https://example.test/video")

        self.assertEqual(result["title"], "Demo")
        run.assert_called_once()
        args = run.call_args.args[0]
        self.assertEqual(args[:3], ["yt-dlp", "--dump-json", "--skip-download"])
        self.assertEqual(args[-1], "https://example.test/video")

    def test_probe_metadata_reports_missing_binary(self):
        with patch("skill_gather.integrations.yt_dlp.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(YtDlpError) as context:
                YtDlpClient(binary="missing-yt-dlp").probe_metadata("https://example.test/video")

        self.assertIn("not found", str(context.exception))

    def test_probe_metadata_reports_command_failure_without_credentials(self):
        completed = Mock(returncode=1, stdout="", stderr="private video https://example.test/tmp")

        with patch("skill_gather.integrations.yt_dlp.subprocess.run", return_value=completed):
            with self.assertRaises(YtDlpError) as context:
                YtDlpClient(binary="yt-dlp").probe_metadata("https://example.test/video")

        self.assertIn("private video", str(context.exception))
        self.assertEqual(context.exception.returncode, 1)
        self.assertIn("[redacted-url]", context.exception.safe_summary)
        self.assertNotIn("https://example.test/tmp", context.exception.safe_summary)

    def test_sanitize_command_output_redacts_urls_and_secret_hints(self):
        summary = sanitize_command_output(
            "failed url=https://example.test/a?token=secret cookie=session"
        )

        self.assertIn("[redacted-url]", summary)
        self.assertIn("[redacted-secret]", summary)
        self.assertNotIn("token=secret", summary)
        self.assertNotIn("cookie=session", summary)


if __name__ == "__main__":
    unittest.main()
