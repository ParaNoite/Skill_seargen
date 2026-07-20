import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skill_gather.integrations import YtDlpError
from skill_gather.cli import main


CONFIG = {
    "providers": {
        "newapi": {
            "base_url": "https://api.renice.cc/v1",
            "api_key_env": "NEWAPI_API_KEY",
            "vision_model": "vision",
            "asr_model": "asr",
            "distiller_model": "distiller",
            "judge_model": "judge",
        }
    },
    "defaults": {
        "provider": "newapi",
        "output_dir": "./skills",
        "run_dir": "./runs",
    },
}


class CliTests(unittest.TestCase):
    def test_score_outputs_json_when_metadata_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            skill_dir = Path(temp_dir) / "skill"
            skill_dir.mkdir()
            (skill_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "package_status": "needs_review",
                        "scores": {"final_status": "needs_review"},
                        "risk_flags": ["no_subtitle"],
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            exit_code = main(["score", str(skill_dir)], stdout=stdout)

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["scores"]["final_status"], "needs_review")

    def metadata_probe_success(self):
        return patch(
            "skill_gather.integrations.yt_dlp.YtDlpClient.probe_metadata",
            return_value={
                "title": "Skill Demo",
                "uploader": "Teacher",
                "duration": 120,
                "subtitles": {"zh-CN": [{"url": "https://example.test/subtitle.json"}]},
            },
        )

    def metadata_probe_failure(self):
        return patch(
            "skill_gather.integrations.yt_dlp.YtDlpClient.probe_metadata",
            side_effect=YtDlpError("yt-dlp unavailable"),
        )

    def media_download_success(self):
        return patch(
            "skill_gather.integrations.yt_dlp.YtDlpClient.download_media",
            return_value={
                "status": "downloaded",
                "target_dir": "runs/demo/media",
                "output_template": "%(id)s.%(ext)s",
                "returncode": 0,
            },
        )

    def test_video_runs_minimal_failure_audit_pipeline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "config.json"
            runs_dir = temp_path / "runs"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            stdout = io.StringIO()

            with self.metadata_probe_success(), self.media_download_success():
                exit_code = main(
                    [
                        "video",
                        "https://www.bilibili.com/video/BV1xx411c7mD/",
                        "--config",
                        str(config_path),
                        "--runs",
                        str(runs_dir),
                    ],
                    stdout=stdout,
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["source"], "bilibili")
            self.assertEqual(payload["status"], "failed")
            self.assertTrue((runs_dir / payload["run_id"] / "run_state.json").exists())
            self.assertTrue((runs_dir / payload["run_id"] / "manifest.json").exists())
            self.assertTrue((runs_dir / payload["run_id"] / "metadata.json").exists())
            self.assertTrue((runs_dir / payload["run_id"] / "failure_report.md").exists())

    def test_inspect_displays_manifest_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "config.json"
            runs_dir = temp_path / "runs"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            video_stdout = io.StringIO()

            with self.metadata_probe_failure(), self.media_download_success():
                main(
                    [
                        "video",
                        "https://www.bilibili.com/video/BV1xx411c7mD/",
                        "--config",
                        str(config_path),
                        "--runs",
                        str(runs_dir),
                    ],
                    stdout=video_stdout,
                )
            run_id = json.loads(video_stdout.getvalue())["run_id"]
            inspect_stdout = io.StringIO()

            exit_code = main(
                ["inspect", run_id, "--runs", str(runs_dir)],
                stdout=inspect_stdout,
            )

            self.assertEqual(exit_code, 0)
            output = inspect_stdout.getvalue()
            self.assertIn("manifest: bilibili BV1xx411c7mD", output)
            self.assertIn("metadata_probe_failed", output)
            self.assertIn("评分: 0 (failed)", output)

    def test_video_resume_does_not_overwrite_existing_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "config.json"
            runs_dir = temp_path / "runs"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            first_stdout = io.StringIO()

            with self.metadata_probe_failure(), self.media_download_success():
                main(
                    ["video", url, "--config", str(config_path), "--runs", str(runs_dir)],
                    stdout=first_stdout,
                )
            run_id = json.loads(first_stdout.getvalue())["run_id"]
            manifest_path = runs_dir / run_id / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["title"] = "already fetched"
            manifest["risk_flags"] = []
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            second_stdout = io.StringIO()
            with self.metadata_probe_success(), self.media_download_success():
                exit_code = main(
                    ["video", url, "--config", str(config_path), "--runs", str(runs_dir)],
                    stdout=second_stdout,
                )

            self.assertEqual(exit_code, 0)
            restored = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(restored["title"], "already fetched")
            self.assertEqual(restored["risk_flags"], [])

    def test_score_reads_video_audit_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "config.json"
            runs_dir = temp_path / "runs"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            video_stdout = io.StringIO()

            with self.metadata_probe_success(), self.media_download_success():
                main(
                    [
                        "video",
                        "https://www.bilibili.com/video/BV1xx411c7mD/",
                        "--config",
                        str(config_path),
                        "--runs",
                        str(runs_dir),
                    ],
                    stdout=video_stdout,
                )
            run_id = json.loads(video_stdout.getvalue())["run_id"]
            score_stdout = io.StringIO()

            exit_code = main(["score", str(runs_dir / run_id)], stdout=score_stdout)

            self.assertEqual(exit_code, 0)
            payload = json.loads(score_stdout.getvalue())
            self.assertEqual(payload["package_status"], "failed")
            self.assertEqual(payload["scores"]["final_status"], "failed")


if __name__ == "__main__":
    unittest.main()
