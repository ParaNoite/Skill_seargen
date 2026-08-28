import os
import unittest
from unittest.mock import patch

from skill_gather.config import parse_config
from skill_gather.readiness_check import run_readiness_check


CONFIG = {
    "providers": {
        "newapi": {
            "base_url": "https://api.example.test/v1",
            "api_key_env": "SKILL_GATHER_READINESS_KEY",
            "vision_model": "vision",
            "asr_model": "faster-whisper:base",
            "distiller_model": "distiller",
            "judge_model": "judge",
        }
    },
    "defaults": {"provider": "newapi"},
    "search": {
        "searxng_base_url": "http://127.0.0.1:8080",
    },
}


class ReadinessCheckTests(unittest.TestCase):
    def test_emits_progress_for_each_probe(self):
        config = parse_config(CONFIG)
        events = []

        with (
            patch("skill_gather.readiness_check._search_provider", return_value={"status": "passed", "summary": "ok"}),
            patch("skill_gather.readiness_check._command_available", return_value={"status": "passed", "summary": "ok"}),
            patch("skill_gather.readiness_check._model_probe", return_value={"status": "passed", "summary": "ok"}),
        ):
            result = run_readiness_check(config, load_asr_model=False, on_progress=events.append)

        self.assertEqual(result["summary"]["total"], 10)
        self.assertEqual(events[0]["event"], "probe_started")
        self.assertEqual(events[-1]["event"], "finished")
        self.assertEqual(events[-1]["index"], 10)
        self.assertEqual(len([event for event in events if event["event"] == "probe_finished"]), 10)

    def test_reports_missing_key_without_echoing_secret(self):
        config = parse_config(CONFIG)
        with patch.dict(os.environ, {}, clear=True):
            result = run_readiness_check(config, load_asr_model=False)

        model_checks = [check for check in result["checks"] if check["group"] == "model"]
        self.assertEqual(result["status"], "failed")
        self.assertTrue(all(check["status"] == "failed" or check["name"] == "local_asr" for check in model_checks))
        self.assertNotIn("SKILL_GATHER_READINESS_KEY", str(result))

    def test_aggregates_real_probe_results(self):
        config = parse_config(CONFIG)

        def fake_search(provider, queries):
            return {"status": "passed", "summary": "请求成功", "result_count": 1, "engine": provider.name}

        with (
            patch.dict(os.environ, {"SKILL_GATHER_READINESS_KEY": "secret-key"}, clear=True),
            patch("skill_gather.readiness_check._search_provider", side_effect=fake_search),
            patch(
                "skill_gather.readiness_check._command_available",
                return_value={"status": "passed", "summary": "可执行文件已找到"},
            ),
            patch(
                "skill_gather.readiness_check._model_probe",
                return_value={"status": "passed", "summary": "真实最小请求通过", "model": "safe-model"},
            ),
        ):
            result = run_readiness_check(config, load_asr_model=False)

        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(result["summary"]["failed"], 0)
        self.assertGreaterEqual(result["summary"]["warning"], 1)
        self.assertNotIn("secret-key", str(result))
