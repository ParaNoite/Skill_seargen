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


if __name__ == "__main__":
    unittest.main()
