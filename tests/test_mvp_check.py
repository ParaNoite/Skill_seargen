import unittest

from skill_gather.config import ConfigError, parse_config
from skill_gather.mvp_check import run_mvp_check


CONFIG = {
    "providers": {
        "newapi": {
            "base_url": "https://api.renice.cc/v1",
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


class MvpCheckTests(unittest.TestCase):
    def test_run_mvp_check_verifies_candidate_and_audit_branches(self):
        result = run_mvp_check(parse_config(CONFIG))

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["checks"]["candidate_pipeline"]["status"], "passed")
        self.assertEqual(result["checks"]["failure_audit_pipeline"]["status"], "passed")
        self.assertIn("SKILL.md", result["checks"]["candidate_pipeline"]["artifacts"])
        self.assertIn("failure_report.md", result["checks"]["failure_audit_pipeline"]["artifacts"])
        self.assertTrue(result["checks"]["candidate_pipeline"]["package_dir"])
        self.assertTrue(result["checks"]["failure_audit_pipeline"]["run_dir"])

    def test_config_rejects_disabled_asr_for_mvp_check(self):
        raw = {
            **CONFIG,
            "providers": {
                "newapi": {
                    **CONFIG["providers"]["newapi"],
                    "asr_model": "disabled",
                }
            },
        }

        with self.assertRaises(ConfigError):
            parse_config(raw)


if __name__ == "__main__":
    unittest.main()
