import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skill_gather.integrations.faster_whisper import FasterWhisperClient, is_faster_whisper_model
from skill_gather.integrations.newapi import NewApiError


class FasterWhisperTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
