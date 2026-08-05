import unittest

from skill_gather.config import ConfigError, parse_config


VALID_CONFIG = {
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


class ConfigTests(unittest.TestCase):
    def test_parses_required_newapi_models(self):
        config = parse_config(VALID_CONFIG)

        self.assertEqual(config.provider, "newapi")
        self.assertEqual(config.newapi.vision_model, "vision")
        self.assertEqual(config.newapi.asr_model, "faster-whisper:base")

    def test_rejects_missing_asr_model(self):
        raw = {
            **VALID_CONFIG,
            "providers": {
                "newapi": {
                    key: value
                    for key, value in VALID_CONFIG["providers"]["newapi"].items()
                    if key != "asr_model"
                }
            },
        }

        with self.assertRaises(ConfigError):
            parse_config(raw)

    def test_rejects_non_faster_whisper_asr_model(self):
        raw = {
            **VALID_CONFIG,
            "providers": {
                "newapi": {
                    **VALID_CONFIG["providers"]["newapi"],
                    "asr_model": "whisper-1",
                }
            },
        }

        with self.assertRaises(ConfigError) as context:
            parse_config(raw)

        self.assertIn("faster-whisper", str(context.exception))

    def test_rejects_missing_model_name(self):
        raw = dict(VALID_CONFIG)
        raw["providers"] = {
            "newapi": {
                "base_url": "https://api.renice.cc/v1",
                "api_key_env": "SKILL_GATHER_TEST_NEWAPI_API_KEY",
            }
        }

        with self.assertRaises(ConfigError):
            parse_config(raw)

    def test_parses_topic_budget_defaults_and_cache_policy(self):
        raw = {
            **VALID_CONFIG,
            "topic_defaults": {
                "max_candidates": 12,
                "max_selected_sources": 3,
                "max_video_duration_sec": 900,
                "max_model_calls": 20,
                "max_estimated_cost_usd": 4.5,
                "max_runtime_sec": 600,
                "reuse_cache": True,
                "refresh_cache": False,
                "judge_difficulty": "strict",
            },
        }

        config = parse_config(raw)

        self.assertEqual(config.topic_defaults.budget.max_candidates, 12)
        self.assertTrue(config.topic_defaults.cache.reuse_cache)
        self.assertEqual(config.topic_defaults.judge_difficulty, "strict")

    def test_parses_search_defaults_and_provider_overrides(self):
        raw = {
            **VALID_CONFIG,
            "search": {
                "searxng_base_url": "http://127.0.0.1:8080",
                "github_token_env": "TEST_GITHUB_TOKEN",
                "cache_ttl_sec": 120,
            },
        }
        config = parse_config(raw)
        self.assertEqual(config.search.searxng_base_url, "http://127.0.0.1:8080")
        self.assertEqual(config.search.bilibili_web_search_url, "https://search.bilibili.com/all")
        self.assertEqual(config.search.github_token_env, "TEST_GITHUB_TOKEN")
        self.assertEqual(config.search.cache_ttl_sec, 120)

    def test_rejects_invalid_search_cache_ttl(self):
        with self.assertRaises(ConfigError):
            parse_config({**VALID_CONFIG, "search": {"cache_ttl_sec": 0}})

    def test_rejects_invalid_topic_budget(self):
        raw = {**VALID_CONFIG, "topic_defaults": {"max_candidates": 0}}

        with self.assertRaises(ConfigError):
            parse_config(raw)


if __name__ == "__main__":
    unittest.main()
