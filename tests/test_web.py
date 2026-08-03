import http.client
import json
import os
import tempfile
import threading
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
        api_status, api_type, payload = self.request("GET", "/api/runs")

        self.assertEqual(page_status, 200)
        self.assertIn("text/html", page_type)
        self.assertIn("Video Skill Gather", page.decode("utf-8"))
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

