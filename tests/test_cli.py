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
            "api_key_env": "SKILL_GATHER_TEST_NEWAPI_API_KEY",
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

    def test_mvp_check_outputs_json_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            stdout = io.StringIO()

            exit_code = main(["mvp-check", "--config", str(config_path)], stdout=stdout)

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["checks"]["candidate_pipeline"]["status"], "passed")
            self.assertEqual(payload["checks"]["failure_audit_pipeline"]["status"], "passed")

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
            self.assertIn("失败审计包", payload["message"])
            self.assertEqual(payload["package"], str(runs_dir / payload["run_id"]))
            self.assertTrue((runs_dir / payload["run_id"] / "run_state.json").exists())
            self.assertTrue((runs_dir / payload["run_id"] / "manifest.json").exists())
            self.assertTrue((runs_dir / payload["run_id"] / "metadata.json").exists())
            self.assertTrue((runs_dir / payload["run_id"] / "failure_report.md").exists())

    def test_video_outputs_candidate_package_message_when_pipeline_completes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "config.json"
            runs_dir = temp_path / "runs"
            out_dir = temp_path / "skills"
            package_dir = out_dir / "demo-skill"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            stdout = io.StringIO()

            def complete_pipeline(**kwargs):
                state = kwargs["state"]
                state.status = "completed"
                state.current_stage = "package"
                state.artifacts["package"] = str(package_dir)
                return state

            with patch("skill_gather.cli.run_video_pipeline", side_effect=complete_pipeline):
                exit_code = main(
                    [
                        "video",
                        "https://www.bilibili.com/video/BV1xx411c7mD/",
                        "--config",
                        str(config_path),
                        "--runs",
                        str(runs_dir),
                        "--out",
                        str(out_dir),
                    ],
                    stdout=stdout,
                )

            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["package"], str(package_dir))
            self.assertIn("候选 skill 包", payload["message"])

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

    def test_inspect_displays_review_evidence_score_judge_and_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_dir = Path(temp_dir) / "runs"
            run_id = "bilibili-BVreview"
            run_dir = runs_dir / run_id
            run_dir.mkdir(parents=True)
            package_dir = Path(temp_dir) / "skills" / "demo-skill"
            package_dir.mkdir(parents=True)
            (run_dir / "run_state.json").write_text(
                json.dumps(
                    {
                        "run_id": run_id,
                        "source_id": "BVreview",
                        "status": "completed",
                        "current_stage": "package",
                        "completed_stages": ["manifest", "timeline_merge", "score", "package"],
                        "artifacts": {
                            "evidence_timeline": str(run_dir / "evidence_timeline.json"),
                            "score": str(run_dir / "score.json"),
                            "package": str(package_dir),
                        },
                        "failure_reason": None,
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "source": "bilibili",
                        "source_id": "BVreview",
                        "url": "https://www.bilibili.com/video/BVreview/",
                        "title": "Review Demo",
                        "author": "Teacher",
                        "duration_sec": 90,
                        "subtitle_available": False,
                        "media_access": "public",
                        "risk_flags": ["no_subtitle"],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "evidence_timeline.json").write_text(
                json.dumps(
                    {
                        "video_duration_sec": 90,
                        "frame_budget": 2,
                        "sampling_strategy": "ffmpeg_interval_10s",
                        "items": [
                            {
                                "timestamp": "00:00:03",
                                "type": "asr",
                                "claim": "讲解本地 editable install 的适用场景",
                                "raw_excerpt": "editable install",
                                "confidence": 0.76,
                            },
                            {
                                "timestamp": "00:00:10",
                                "type": "frame_ocr",
                                "claim": "画面展示 python -m pip install -e .",
                                "raw_excerpt": "python -m pip install -e .",
                                "confidence": 0.91,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "score.json").write_text(
                json.dumps(
                    {
                        "rule_score": 84,
                        "llm_judge_score": 88,
                        "final_score": 84,
                        "final_status": "needs_review",
                        "conflict_policy": "conservative",
                        "single_channel_evidence": False,
                        "judge": {
                            "status": "judged",
                            "score": 88,
                            "rationale": "证据可执行，但边界仍需人工确认。",
                            "risk_flags": ["boundary_needs_review"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "source": "bilibili",
                        "source_id": "BVreview",
                        "source_url": "https://www.bilibili.com/video/BVreview/",
                        "title": "Review Demo",
                        "author": "Teacher",
                        "package_status": "needs_review",
                        "evidence": [],
                        "risk_flags": ["no_subtitle", "boundary_needs_review"],
                        "scores": {
                            "rule_score": 84,
                            "llm_judge_score": 88,
                            "final_score": 84,
                            "final_status": "needs_review",
                            "conflict_policy": "conservative",
                        },
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            exit_code = main(["inspect", run_id, "--runs", str(runs_dir)], stdout=stdout)

            self.assertEqual(exit_code, 0)
            output = stdout.getvalue()
            self.assertIn("证据摘要: 2 条", output)
            self.assertIn("00:00:03 [asr] 讲解本地 editable install 的适用场景", output)
            self.assertIn("00:00:10 [frame_ocr] 画面展示 python -m pip install -e .", output)
            self.assertIn("风险: no_subtitle, boundary_needs_review", output)
            self.assertIn("评分细节: rule=84, judge=88, policy=conservative", output)
            self.assertIn("LLM judge: judged score=88; 证据可执行，但边界仍需人工确认。", output)
            self.assertIn(f"package: {package_dir}", output)

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
