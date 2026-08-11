import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from huggingface_hub.errors import LocalEntryNotFoundError

from skill_gather.integrations.faster_whisper import (
    FasterWhisperClient,
    _configure_hugging_face_environment,
    is_faster_whisper_model,
)
from skill_gather.integrations.newapi import NewApiError


class FasterWhisperTests(unittest.TestCase):
    def test_configures_project_hugging_face_cache_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            cache_dir = _configure_hugging_face_environment()

            self.assertEqual(cache_dir, Path(os.environ["HF_HOME"]) / "hub")
            self.assertEqual(os.environ["HF_ENDPOINT"], "https://hf-mirror.com")
            self.assertEqual(os.environ["HF_HUB_DISABLE_XET"], "1")
            self.assertTrue((Path(os.environ["HF_HOME"]).parent / "pyproject.toml").is_file())

    def test_preserves_explicit_hugging_face_environment(self):
        environment = {
            "HF_HOME": "D:/custom/hf-home",
            "HF_HUB_CACHE": "D:/custom/hub-cache",
            "HF_ENDPOINT": "https://example.invalid",
            "HF_HUB_DISABLE_XET": "0",
        }
        with patch.dict(os.environ, environment, clear=True):
            cache_dir = _configure_hugging_face_environment()

            self.assertEqual(cache_dir, Path("D:/custom/hub-cache"))
            self.assertEqual(os.environ["HF_HOME"], environment["HF_HOME"])
            self.assertEqual(os.environ["HF_ENDPOINT"], environment["HF_ENDPOINT"])
            self.assertEqual(os.environ["HF_HUB_DISABLE_XET"], environment["HF_HUB_DISABLE_XET"])

    def test_detects_faster_whisper_model_reference(self):
        self.assertTrue(is_faster_whisper_model("faster-whisper:base"))
        self.assertTrue(is_faster_whisper_model("faster_whisper:small"))
        self.assertFalse(is_faster_whisper_model("whisper-1"))

    def test_reports_missing_dependency_clearly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "audio.wav"
            audio.write_text("placeholder", encoding="utf-8")
            client = FasterWhisperClient.from_model("faster-whisper:base")

            with patch.dict("sys.modules", {"faster_whisper": None}):
                with self.assertRaises(NewApiError) as context:
                    client.transcribe_audio(audio, "faster-whisper:base")

        self.assertEqual(context.exception.code, "faster_whisper_missing")
        self.assertIn("pip install -e .", str(context.exception))

    def test_reports_model_download_or_load_failure_clearly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "audio.wav"
            audio.write_text("placeholder", encoding="utf-8")
            client = FasterWhisperClient.from_model("faster-whisper:base")

            with patch("faster_whisper.WhisperModel", side_effect=RuntimeError("connect timeout")):
                with self.assertRaises(NewApiError) as context:
                    client.transcribe_audio(audio, "faster-whisper:base")

        self.assertEqual(context.exception.code, "faster_whisper_failed")
        self.assertIn("HF_ENDPOINT=https://hf-mirror.com", str(context.exception))
        self.assertIn("faster-whisper:<local model path>", str(context.exception))

    def test_uses_configured_hugging_face_hub_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "audio.wav"
            audio.write_text("placeholder", encoding="utf-8")
            client = FasterWhisperClient.from_model("faster-whisper:large-v3-turbo")
            info = type("Info", (), {"language": "zh"})()

            with patch.dict(os.environ, {"HF_HOME": temp_dir}, clear=True):
                with patch("faster_whisper.WhisperModel") as whisper_model:
                    whisper_model.return_value.transcribe.return_value = ([], info)
                    client.transcribe_audio(audio, "faster-whisper:large-v3-turbo")

            whisper_model.assert_called_once_with(
                "large-v3-turbo",
                device=client.device,
                compute_type=client.compute_type,
                download_root=str(Path(temp_dir) / "hub"),
                local_files_only=True,
            )

    def test_downloads_only_after_local_cache_miss(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "audio.wav"
            audio.write_text("placeholder", encoding="utf-8")
            client = FasterWhisperClient.from_model("faster-whisper:base")
            loaded_model = MagicMock()
            loaded_model.transcribe.return_value = ([], type("Info", (), {"language": "en"})())

            with patch.dict(os.environ, {"HF_HOME": temp_dir}, clear=True):
                with patch(
                    "faster_whisper.WhisperModel",
                    side_effect=[LocalEntryNotFoundError("cache miss"), loaded_model],
                ) as whisper_model:
                    client.transcribe_audio(audio, "faster-whisper:base")

            options = {
                "device": client.device,
                "compute_type": client.compute_type,
                "download_root": str(Path(temp_dir) / "hub"),
            }
            self.assertEqual(
                whisper_model.call_args_list,
                [call("base", local_files_only=True, **options), call("base", **options)],
            )


if __name__ == "__main__":
    unittest.main()
