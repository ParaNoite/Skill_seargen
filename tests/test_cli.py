import io
import json
import tempfile
import unittest
from pathlib import Path

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

    def test_video_creates_resumable_run_shell(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "config.json"
            runs_dir = temp_path / "runs"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            stdout = io.StringIO()

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
            self.assertTrue((runs_dir / payload["run_id"] / "run_state.json").exists())


if __name__ == "__main__":
    unittest.main()
