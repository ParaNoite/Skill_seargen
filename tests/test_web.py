import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from skill_gather.web import WebApp, create_server


CONFIG = {
    "providers": {
        "newapi": {
            "base_url": "https://api.example.test/v1",
            "api_key_env": "SKILL_GATHER_TEST_API_KEY",
            "vision_model": "vision",
            "asr_model": "faster-whisper:base",
            "distiller_model": "distiller",
            "judge_model": "judge",
        }
    },
    "defaults": {"provider": "newapi", "output_dir": "./skills", "run_dir": "./runs"},
}


class WebAppTests(unittest.TestCase):
    def test_llm_plan_can_be_interrupted_without_late_response_overwrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            app = WebApp(config=str(config_path), runs=str(root / "runs"), out=str(root / "skills"))
            release = threading.Event()

            class Client:
                def build_search_intent(self, topic, mode, model):
                    release.wait(timeout=2)
                    return {"goal": "迟到响应", "facets": [], "exclusions": [], "queries": []}

            with patch("skill_gather.web.NewApiClient.from_config", return_value=Client()):
                created = app.create_topic("教程")
                interrupted = app.interrupt_plan(created["run_id"])
                release.set()
                for _ in range(50):
                    if not app.get_plan(created["run_id"])["job"].get("active"):
                        break
                    time.sleep(0.01)

            final_plan = app.get_plan(created["run_id"])["plan"]
            self.assertEqual(interrupted["plan"]["warning"], "plan_interrupted")
            self.assertEqual(final_plan["warning"], "plan_interrupted")
            self.assertNotEqual(final_plan["goal"], "迟到响应")

    def test_topic_detail_exposes_fusion_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            app = WebApp(config=str(config_path), runs=str(root / "runs"), out=str(root / "skills"))
            created = app.create_topic("融合主题", mode="technical")
            task = app.topic_store.load(created["run_id"])
            task.artifacts["fusion"] = "topic_package/fusion.json"
            fusion_path = app.topic_store.run_path(task.run_id) / task.artifacts["fusion"]
            fusion_path.write_text(
                json.dumps({"conclusions": [{"conclusion_id": "C1"}], "conflicts": [], "evidence_gaps": []}),
                encoding="utf-8",
            )
            app.topic_store.save(task)

            detail = app.get_topic(task.run_id)

            self.assertEqual(detail["fusion"]["conclusions"][0]["conclusion_id"], "C1")

    def test_failed_topic_can_resume_for_processing_retry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            app = WebApp(config=str(config_path), runs=str(root / "runs"), out=str(root / "skills"))
            task = app.create_topic("Godot 工具包", mode="technical")
            state = app.topic_store.load(task["run_id"])
            state.status = "failed"
            state.current_stage = "processing_sources"
            state.failure_stage = "processing_sources"
            state.failure_reason = "GitHub API 速率限制"
            app.topic_store.save(state)

            resumed = app.resume_topic(task["run_id"])

            self.assertEqual(resumed["status"], "processing_sources")

    def test_topic_process_starts_background_job_and_exposes_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            app = WebApp(config=str(config_path), runs=str(root / "runs"), out=str(root / "skills"))
            task = app.create_topic("网页主题", mode="normal")
            store = app.topic_store
            state = store.load(task["run_id"])
            state.status = "processing_sources"
            state.current_stage = "processing_sources"
            store.save(state)

            def fake_process(args, stdout, stderr):
                stdout.write(json.dumps({"run_id": args.run_id, "status": "completed"}, ensure_ascii=False))
                return 0

            with patch("skill_gather.web.handle_topic_process", side_effect=fake_process):
                queued = app.process_topic(task["run_id"], vision_mode="off", vision_frame_limit=1)
                self.assertEqual(queued["status"], "queued")
                for _ in range(50):
                    detail = app.get_topic(task["run_id"])
                    if not detail.get("job", {}).get("active"):
                        break
                    time.sleep(0.01)

            detail = app.get_topic(task["run_id"])
            self.assertEqual(detail["job"]["status"], "finished")
            self.assertEqual(detail["job"]["result"]["status"], "completed")

    def test_topic_search_and_selection_use_shared_service(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            app = WebApp(config=str(config_path), runs=str(root / "runs"), out=str(root / "skills"))
            created = app.create_topic("Godot 导航", mode="technical")
            searched = app.search_topic(created["run_id"], use_fake=True)
            self.assertEqual(searched["status"], "awaiting_selection")
            selected = app.select_topic(searched["run_id"], [searched["candidates"][0]["candidate_id"]])
            self.assertEqual(selected["status"], "processing_sources")
            self.assertEqual(len(selected["selected_sources"]), 1)
            self.assertTrue((root / "runs" / created["run_id"] / "topic_package" / "sources.json").exists())

    def test_v11_plan_operations_and_unified_queries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            app = WebApp(config=str(config_path), runs=str(root / "runs"), out=str(root / "skills"))
            created = app.create_topic("教程", execution_mode="manual")
            self.assertEqual(created["status"], "awaiting_plan_confirmation")
            plan = app.get_plan(created["run_id"])["plan"]
            confirmed = app.confirm_plan(created["run_id"], plan["recommended_option_id"], {"goal": "聚焦可复现步骤"})
            self.assertEqual(confirmed["plan"]["goal"], "聚焦可复现步骤")
            paused = app.pause_topic(created["run_id"])
            self.assertEqual(paused["status"], "paused")
            self.assertEqual(app.retry_topic(created["run_id"])["status"], "created")
            self.assertEqual(app.get_work_item(created["run_id"])["kind"], "topic")
            self.assertEqual(app.list_work_items()[0]["execution_mode"], "manual")
            self.assertIn("model_availability", app.metrics())

    def test_result_browser_reads_skill_content_and_filters_normal_topics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            app = WebApp(config=str(config_path), runs=str(root / "runs"), out=str(root / "skills"))
            technical = app.create_topic("Godot NavigationAgent3D", mode="technical")
            task = app.topic_store.load(technical["run_id"])
            task.status = "completed"
            task.current_stage = "completed"
            task.artifacts.update({"skill": "topic_package/SKILL.md", "score": "topic_package/score.json"})
            run_root = app.topic_store.run_path(task.run_id)
            (run_root / "topic_package/SKILL.md").write_text("# Skill content", encoding="utf-8")
            (run_root / "topic_package/score.json").write_text(json.dumps({"final_score": 88, "final_status": "passed"}), encoding="utf-8")
            app.topic_store.save(task)
            normal = app.create_topic("Godot NavigationAgent2D", mode="normal")
            normal_task = app.topic_store.load(normal["run_id"])
            normal_task.status = "completed"
            normal_task.current_stage = "completed"
            app.topic_store.save(normal_task)

            skills = app.results(result_type="skill", status="passed")
            detail = app.get_result(task.run_id)

            self.assertEqual([item["run_id"] for item in skills], [task.run_id])
            self.assertEqual(detail["skill"], "# Skill content")

    def test_lists_and_reads_existing_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "runs" / "bilibili-BVdemo"
            run_dir.mkdir(parents=True)
            (run_dir / "run_state.json").write_text(
                json.dumps(
                    {
                        "run_id": "bilibili-BVdemo",
                        "source_id": "BVdemo",
                        "status": "completed",
                        "current_stage": "package",
                        "completed_stages": ["manifest", "package"],
                        "artifacts": {},
                        "failure_reason": None,
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "source": "bilibili",
                        "source_id": "BVdemo",
                        "url": "https://www.bilibili.com/video/BVdemo/",
                        "title": "Demo video",
                        "author": "Teacher",
                        "risk_flags": [],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "human_review.json").write_text(
                json.dumps({"label": "usable", "expected_status": "passed", "notes": "可复用"}),
                encoding="utf-8",
            )
            (run_dir / "prefilter.json").write_text(
                json.dumps({"status": "accepted", "reason_code": "basic_metadata_passed"}),
                encoding="utf-8",
            )
            app = WebApp(config=str(root / "config.json"), runs=str(root / "runs"), out=str(root / "skills"))

            runs = app.list_runs()
            detail = app.get_run("bilibili-BVdemo")

            self.assertEqual(runs[0]["title"], "Demo video")
            self.assertEqual(detail["status"], "completed")
            self.assertEqual(detail["progress"], 22)
            self.assertEqual(detail["human_review"]["label"], "usable")
            self.assertEqual(detail["prefilter"]["status"], "accepted")

    def test_rejects_path_like_run_id(self):
        app = WebApp(config="config.json", runs="runs", out="skills")

        with self.assertRaises(FileNotFoundError):
            app.get_run("../config")


class WebHttpTests(unittest.TestCase):
    def setUp(self):
        self.old_api_key = os.environ.get("SKILL_GATHER_TEST_API_KEY")
        os.environ.pop("SKILL_GATHER_TEST_API_KEY", None)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config_path = self.root / "config.json"
        self.config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
        self.server = create_server(
            host="127.0.0.1",
            port=0,
            config=str(self.config_path),
            runs=str(self.root / "runs"),
            out=str(self.root / "skills"),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()
        if self.old_api_key is None:
            os.environ.pop("SKILL_GATHER_TEST_API_KEY", None)
        else:
            os.environ["SKILL_GATHER_TEST_API_KEY"] = self.old_api_key

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, response.getheader("Content-Type"), payload

    def test_serves_frontend_and_empty_run_list(self):
        page_status, page_type, page = self.request("GET", "/")
        react_status, react_type, react_bundle = self.request("GET", "/react-nav.js")
        api_status, api_type, payload = self.request("GET", "/api/runs")

        self.assertEqual(page_status, 200)
        self.assertIn("text/html", page_type)
        self.assertIn("skill_seargen", page.decode("utf-8"))
        self.assertEqual(react_status, 200)
        self.assertIn("text/javascript", react_type)
        self.assertGreater(len(react_bundle), 1000)
        self.assertEqual(api_status, 200)
        self.assertIn("application/json", api_type)
        self.assertEqual(json.loads(payload), {"runs": []})

    def test_rejects_empty_video_url(self):
        status, _, payload = self.request(
            "POST",
            "/api/runs",
            body=json.dumps({"url": "", "api_key": "secret-key"}),
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(payload))
        self.assertNotIn("SKILL_GATHER_TEST_API_KEY", os.environ)

    def test_topic_endpoints_create_search_and_select_fake_candidates(self):
        status, _, payload = self.request(
            "POST",
            "/api/topics",
            body=json.dumps({"topic": "Godot 导航", "mode": "technical"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 201)
        run_id = json.loads(payload)["run_id"]

        status, _, payload = self.request(
            "POST",
            f"/api/topics/{run_id}/search",
            body=json.dumps({"fake": True}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        searched = json.loads(payload)
        self.assertEqual(searched["status"], "awaiting_selection")
        candidate_id = searched["candidates"][0]["candidate_id"]

        status, _, payload = self.request(
            "POST",
            f"/api/topics/{run_id}/select",
            body=json.dumps({"candidate_ids": [candidate_id]}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["status"], "processing_sources")

    def test_v11_http_plan_pause_retry_and_unified_endpoints(self):
        headers = {"Content-Type": "application/json"}
        status, _, payload = self.request(
            "POST", "/api/topics", body=json.dumps({"topic": "教程", "execution_mode": "manual"}), headers=headers
        )
        self.assertEqual(status, 201)
        created = json.loads(payload)
        run_id = created["run_id"]
        self.assertEqual(created["status"], "awaiting_plan_confirmation")

        status, _, payload = self.request("GET", f"/api/topics/{run_id}/plan")
        plan = json.loads(payload)["plan"]
        self.assertEqual(status, 200)
        status, _, payload = self.request(
            "POST",
            f"/api/topics/{run_id}/plan/confirm",
            body=json.dumps({"option_id": plan["recommended_option_id"], "edited": {"goal": "HTTP 确认目标"}}),
            headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["plan"]["goal"], "HTTP 确认目标")
        self.assertEqual(self.request("POST", f"/api/topics/{run_id}/pause", body="{}", headers=headers)[0], 200)
        self.assertEqual(self.request("POST", f"/api/topics/{run_id}/retry", body="{}", headers=headers)[0], 200)
        self.assertEqual(self.request("GET", "/api/work-items")[0], 200)
        self.assertEqual(self.request("GET", f"/api/work-items/{run_id}")[0], 200)
        self.assertEqual(self.request("GET", "/api/metrics")[0], 200)
        self.assertEqual(self.request("GET", "/api/results?type=topic&status=needs_review")[0], 200)

    def test_accepts_api_key_for_current_process_without_echoing_it(self):
        status, _, payload = self.request(
            "POST",
            "/api/mvp-check",
            body=json.dumps({"api_key": "secret-key"}),
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(os.environ["SKILL_GATHER_TEST_API_KEY"], "secret-key")
        self.assertNotIn("secret-key", payload.decode("utf-8"))

    def test_lists_models_with_api_key_without_echoing_it(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps(
                    {
                        "data": [
                            {"id": "qwen-vl-plus"},
                            {"id": "deepseek-chat"},
                            {"id": "whisper-large-v3"},
                        ]
                    }
                ).encode("utf-8")

        with patch("skill_gather.web.urllib.request.urlopen", return_value=Response()):
            status, _, payload = self.request(
                "POST",
                "/api/models",
                body=json.dumps({"api_key": "secret-key"}),
                headers={"Content-Type": "application/json"},
            )

        body = payload.decode("utf-8")
        parsed = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIn("whisper-large-v3", parsed["suggestions"]["asr"])
        self.assertIn("qwen-vl-plus", parsed["suggestions"]["vision"])
        self.assertIn("deepseek-chat", parsed["suggestions"]["text"])
        self.assertTrue(parsed["catalog_only"])
        self.assertNotIn("secret-key", body)

    def test_lists_models_with_literal_config_api_key(self):
        literal_config = {
            **CONFIG,
            "providers": {
                "newapi": {
                    **CONFIG["providers"]["newapi"],
                    "api_key_env": "sk-test-literal-key",
                }
            },
        }
        self.config_path.write_text(json.dumps(literal_config), encoding="utf-8")

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return json.dumps({"data": [{"id": "deepseek-chat"}]}).encode("utf-8")

        with patch.dict(os.environ, {}, clear=True):
            with patch("skill_gather.web.urllib.request.urlopen", return_value=Response()) as urlopen:
                status, _, payload = self.request(
                    "POST",
                    "/api/models",
                    body=json.dumps({}),
                    headers={"Content-Type": "application/json"},
                )

        request = urlopen.call_args.args[0]
        body = payload.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertEqual(request.headers["Authorization"], "Bearer sk-test-literal-key")
        self.assertNotIn("sk-test-literal-key", body)

    def test_deletes_run_artifacts_by_id(self):
        run_dir = self.root / "runs" / "bilibili-BVdemo"
        run_dir.mkdir(parents=True)
        (run_dir / "run_state.json").write_text(
            json.dumps(
                {
                    "run_id": "bilibili-BVdemo",
                    "source_id": "BVdemo",
                    "status": "failed",
                    "current_stage": "package",
                    "completed_stages": ["manifest", "package"],
                    "artifacts": {},
                    "failure_reason": "failed test run",
                }
            ),
            encoding="utf-8",
        )
        (run_dir / "artifact.txt").write_text("temporary artifact", encoding="utf-8")

        status, content_type, payload = self.request("DELETE", "/api/runs/bilibili-BVdemo")
        list_status, _, list_payload = self.request("GET", "/api/runs")

        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)
        self.assertEqual(json.loads(payload)["status"], "deleted")
        self.assertFalse(run_dir.exists())
        self.assertEqual(list_status, 200)
        self.assertEqual(json.loads(list_payload), {"runs": []})


if __name__ == "__main__":
    unittest.main()

