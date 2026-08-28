import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skill_gather.integrations import YtDlpError
from skill_gather.cli import _complete_topic_generation, _create_web_server, _print_json, main
from skill_gather.topics import TopicRunStore


CONFIG = {
    "providers": {
        "newapi": {
            "base_url": "https://api.example.test/v1",
            "api_key_env": "SKILL_GATHER_TEST_NEWAPI_API_KEY",
            "vision_model": "vision",
            "asr_model": "faster-whisper:base",
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
    def test_supervise_run_creates_then_resumes_without_user_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "supervisor.json"
            config_path.write_text("{}", encoding="utf-8")
            lab = root / "lab"

            first = io.StringIO()
            self.assertEqual(main(["supervise", "run", "--config", str(config_path), "--lab", str(lab)], stdout=first), 0)
            second = io.StringIO()
            self.assertEqual(main(["supervise", "run", "--config", str(config_path), "--lab", str(lab)], stdout=second), 0)

            self.assertFalse(json.loads(first.getvalue())["resumed"])
            self.assertTrue(json.loads(second.getvalue())["resumed"])

    def test_supervise_commands_create_status_and_theme(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "supervisor.json"
            config_path.write_text(json.dumps({"supervisor": {"ted_critical_topics": ["opening-game"]}}), encoding="utf-8")
            lab = root / "lab"

            started = io.StringIO()
            self.assertEqual(
                main(["supervise", "start", "--config", str(config_path), "--lab", str(lab), "--id", "demo"], stdout=started),
                0,
            )
            self.assertEqual(json.loads(started.getvalue())["supervision_id"], "demo")
            self.assertTrue((lab / "demo" / "capture-index.json").is_file())
            self.assertTrue((lab / "demo" / "capture-index.md").is_file())
            self.assertTrue((lab / "demo" / "showcase.html").is_file())

            themed = io.StringIO()
            self.assertEqual(
                main(["supervise", "theme", "demo", "opening-game", "--reason", "TED 关键主题", "--utility-score", "90", "--lab", str(lab)], stdout=themed),
                0,
            )
            self.assertEqual(json.loads(themed.getvalue())["acceptance_level"], "ted_critical")

            status = io.StringIO()
            self.assertEqual(main(["supervise", "status", "demo", "--lab", str(lab)], stdout=status), 0)
            self.assertEqual(len(json.loads(status.getvalue())["theme_queue"]), 1)

    def test_supervise_capture_requires_trace_and_generates_offline_showcase(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "supervisor.json"
            config_path.write_text(json.dumps({"supervisor": {"capture": {"require_trace": True}}}), encoding="utf-8")
            lab = root / "lab"
            self.assertEqual(main(["supervise", "start", "--config", str(config_path), "--lab", str(lab), "--id", "demo"]), 0)
            theme = io.StringIO()
            self.assertEqual(
                main([
                    "supervise", "theme", "demo", "AI 课程生成", "--reason", "TED 核心", "--ted-relevance-score", "90",
                    "--beat", "course_skill", "--lab", str(lab),
                ], stdout=theme),
                0,
            )
            run_root = lab / "demo"
            screenshot = run_root / "captures/theme-001/result/screen.png"
            trace = run_root / "captures/theme-001/result/flow.trace.zip"
            screenshot.parent.mkdir(parents=True)
            screenshot.write_text("placeholder", encoding="utf-8")
            trace.write_text("trace", encoding="utf-8")

            missing_trace = io.StringIO()
            self.assertEqual(
                main([
                    "supervise", "capture", "demo", "--theme-id", "theme-001", "--topic", "AI 课程生成",
                    "--stage", "result", "--event", "course_skill_result", "--screenshot", str(screenshot), "--lab", str(lab),
                ], stderr=missing_trace),
                2,
            )
            self.assertIn("Trace", missing_trace.getvalue())

            captured = io.StringIO()
            self.assertEqual(
                main([
                    "supervise", "capture", "demo", "--theme-id", "theme-001", "--topic", "AI 课程生成",
                    "--stage", "result", "--event", "course_skill_result", "--screenshot", str(screenshot),
                    "--trace", str(trace), "--narrative-score", "40", "--information-score", "25",
                    "--visual-score", "20", "--evidence-score", "15", "--beat", "course_skill", "--lab", str(lab),
                ], stdout=captured),
                0,
            )
            output = io.StringIO()
            self.assertEqual(main(["supervise", "showcase", "demo", "--lab", str(lab)], stdout=output), 0)
            paths = json.loads(output.getvalue())
            self.assertTrue(Path(paths["markdown"]).is_file())
            self.assertTrue(Path(paths["html"]).is_file())

    def test_web_server_retries_with_system_port_after_windows_bind_error(self):
        error = OSError("permission denied")
        error.winerror = 10013
        server = object()
        create_server = unittest.mock.Mock(side_effect=[error, server])
        args = unittest.mock.Mock(host="127.0.0.1", port=8765, config="config.json", runs="runs", out="skills", assets_dir="school-assets")

        result = _create_web_server(create_server, args)

        self.assertIs(result, server)
        self.assertEqual(create_server.call_args_list[0].kwargs["port"], 8765)
        self.assertEqual(create_server.call_args_list[1].kwargs["port"], 0)
        self.assertEqual(create_server.call_args_list[0].kwargs["assets_dir"], "school-assets")

    def test_model_check_reports_real_probe_status_without_echoing_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            stdout = io.StringIO()
            with patch.dict("os.environ", {"SKILL_GATHER_TEST_NEWAPI_API_KEY": "secret-key"}, clear=True), patch(
                "skill_gather.cli.NewApiClient.probe_model",
                side_effect=[
                    {"model": "vision", "capability": "vision", "available": False, "error_code": "model_probe_failed", "status_code": 404, "summary": "unknown model"},
                    {"model": "distiller", "capability": "text", "available": True},
                    {"model": "judge", "capability": "text", "available": True},
                ],
            ):
                exit_code = main(["model-check", "--config", str(config_path)], stdout=stdout)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["probes"][0]["available"])
        self.assertNotIn("secret-key", stdout.getvalue())

    def test_scoring_rerun_does_not_regenerate_technical_skill(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("评分重跑", mode="technical")
            task.status = "scoring"
            task.current_stage = "scoring"
            skill_path = store.run_path(task.run_id) / "topic_package/SKILL.md"
            skill_path.write_text("existing skill", encoding="utf-8")
            store.save(task)
            fusion = {"conclusions": []}

            with patch("skill_gather.cli.generate_technical_skill") as generate, patch(
                "skill_gather.cli.rescore_technical_package",
                return_value=(store.run_path(task.run_id) / "topic_package/score.json", {"final_status": "needs_review"}),
            ):
                result = _complete_topic_generation(store, task, store.run_path(task.run_id), fusion)

        self.assertEqual(result.status, "completed")
        generate.assert_not_called()
    def test_json_output_falls_back_to_ascii_on_gbk_stream(self):
        buffer = io.BytesIO()
        stdout = io.TextIOWrapper(buffer, encoding="gbk")

        _print_json({"title": "Godot 4.6 🚀"}, stdout)
        stdout.flush()

        payload = json.loads(buffer.getvalue().decode("gbk"))
        self.assertEqual(payload["title"], "Godot 4.6 🚀")

    def test_topic_create_and_inspect_persist_a_theme_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_dir = Path(temp_dir) / "runs"
            create_stdout = io.StringIO()

            exit_code = main(
                [
                    "topic",
                    "create",
                    "Godot 导航",
                    "--mode",
                    "technical",
                    "--runs",
                    str(runs_dir),
                    "--max-candidates",
                    "8",
                ],
                stdout=create_stdout,
            )

            self.assertEqual(exit_code, 0)
            created = json.loads(create_stdout.getvalue())
            self.assertEqual(created["mode"], "technical")
            self.assertEqual(created["budget"]["max_candidates"], 8)

            inspect_stdout = io.StringIO()
            inspect_exit_code = main(
                ["topic", "inspect", created["run_id"], "--runs", str(runs_dir)],
                stdout=inspect_stdout,
            )

            self.assertEqual(inspect_exit_code, 0)
            inspected = json.loads(inspect_stdout.getvalue())
            self.assertEqual(inspected["topic"], "Godot 导航")
            self.assertEqual(inspected["status"], "created")

    def test_topic_fake_search_lists_and_selects_candidates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runs_dir = root / "runs"
            config_path = root / "config.json"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            create_stdout = io.StringIO()
            self.assertEqual(
                main(["topic", "create", "Godot 导航", "--mode", "technical", "--runs", str(runs_dir)], stdout=create_stdout),
                0,
            )
            run_id = json.loads(create_stdout.getvalue())["run_id"]

            search_stdout = io.StringIO()
            self.assertEqual(
                main(
                    ["topic", "search", run_id, "--runs", str(runs_dir), "--config", str(config_path), "--fake"],
                    stdout=search_stdout,
                ),
                0,
            )
            searched = json.loads(search_stdout.getvalue())
            self.assertEqual(searched["status"], "awaiting_selection")
            self.assertEqual(len(searched["candidates"]), 3)
            candidate_id = searched["candidates"][0]["candidate_id"]

            select_stdout = io.StringIO()
            self.assertEqual(
                main(["topic", "select", run_id, candidate_id, "--runs", str(runs_dir)], stdout=select_stdout),
                0,
            )
            selected = json.loads(select_stdout.getvalue())
            self.assertEqual(selected["status"], "processing_sources")
            self.assertEqual(selected["usage"]["selected_source_count"], 1)
            sources = json.loads((runs_dir / run_id / "topic_package" / "sources.json").read_text(encoding="utf-8"))
            self.assertEqual(len(sources["selected_sources"]), 1)

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

            self.assertEqual(exit_code, 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["source"], "bilibili")
            self.assertEqual(payload["status"], "failed")
            self.assertIn("失败审计包", payload["message"])
            self.assertEqual(payload["package"], str(runs_dir / payload["run_id"]))
            self.assertTrue((runs_dir / payload["run_id"] / "run_state.json").exists())
            self.assertTrue((runs_dir / payload["run_id"] / "manifest.json").exists())
            self.assertTrue((runs_dir / payload["run_id"] / "metadata.json").exists())
            self.assertTrue((runs_dir / payload["run_id"] / "failure_report.md").exists())
            self.assertEqual(
                json.loads((runs_dir / payload["run_id"] / "cli_result.json").read_text(encoding="utf-8"))["exit_code"],
                1,
            )

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

    def test_video_run_variant_creates_independent_experiment_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "config.json"
            runs_dir = temp_path / "runs"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            stdout = io.StringIO()

            with patch("skill_gather.cli.run_video_pipeline", side_effect=lambda **kwargs: kwargs["state"]):
                exit_code = main(
                    [
                        "video",
                        "https://www.bilibili.com/video/BV1xx411c7mD/",
                        "--config",
                        str(config_path),
                        "--runs",
                        str(runs_dir),
                        "--run-variant",
                        "sampled-12",
                    ],
                    stdout=stdout,
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["run_id"], "bilibili-BV1xx411c7mD--sampled-12")

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

            self.assertEqual(exit_code, 1)
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

    def test_review_records_human_result_separately_from_model_scores(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runs_dir = Path(temp_dir) / "runs"
            run_dir = runs_dir / "bilibili-BVreview"
            run_dir.mkdir(parents=True)
            (run_dir / "run_state.json").write_text(
                json.dumps(
                    {
                        "run_id": "bilibili-BVreview",
                        "source_id": "BVreview",
                        "status": "completed",
                        "current_stage": "package",
                        "completed_stages": [],
                        "artifacts": {},
                        "failure_reason": None,
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "score.json").write_text(
                json.dumps({"rule_score": 90, "llm_judge_score": 62, "final_status": "failed"}),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            exit_code = main(
                [
                    "review",
                    "bilibili-BVreview",
                    "--runs",
                    str(runs_dir),
                    "--label",
                    "needs_changes",
                    "--notes",
                    "边界说明不足",
                ],
                stdout=stdout,
            )

            self.assertEqual(exit_code, 0)
            review = json.loads((run_dir / "human_review.json").read_text(encoding="utf-8"))
            score = json.loads((run_dir / "score.json").read_text(encoding="utf-8"))
            self.assertEqual(review["label"], "needs_changes")
            self.assertEqual(review["expected_status"], "needs_review")
            self.assertEqual(review["notes"], "边界说明不足")
            self.assertNotIn("human_review", score)

    def test_calibrate_reports_rule_judge_and_final_confusion_matrices(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            dataset_path = Path(temp_dir) / "labels.json"
            dataset_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "a",
                            "human_label": "usable",
                            "rule_score": 92,
                            "judge_score": 88,
                            "difficulty": "standard",
                        },
                        {
                            "id": "b",
                            "human_label": "needs_changes",
                            "rule_score": 84,
                            "judge_score": 76,
                            "difficulty": "standard",
                        },
                        {
                            "id": "c",
                            "human_label": "unusable",
                            "rule_score": 90,
                            "judge_score": 40,
                            "difficulty": "standard",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()

            exit_code = main(["calibrate", str(dataset_path)], stdout=stdout)

            self.assertEqual(exit_code, 0)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["sample_count"], 3)
            self.assertEqual(report["rule"]["matrix"]["failed"]["passed"], 1)
            self.assertEqual(report["judge"]["accuracy"], 1.0)
            self.assertEqual(report["final"]["accuracy"], 1.0)
            self.assertEqual(
                report["judge_threshold_calibration"]["standard"]["recommended"],
                {"passed_threshold": 85, "review_threshold": 70, "accuracy": 1.0},
            )


if __name__ == "__main__":
    unittest.main()
