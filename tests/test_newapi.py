import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from skill_gather.config import NewApiConfig
from skill_gather.integrations.newapi import NewApiClient, NewApiError


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class NewApiClientTests(unittest.TestCase):
    def test_from_config_returns_none_when_api_key_is_missing(self):
        config = NewApiConfig(
            base_url="https://api.example.test/v1",
            api_key_env="SKILL_GATHER_TEST_NEWAPI_API_KEY",
            vision_model="vision",
            asr_model="asr",
            distiller_model="distiller",
            judge_model="judge",
        )

        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(NewApiClient.from_config(config))

    def test_transcribe_audio_posts_multipart_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "audio.wav"
            audio.write_bytes(b"wav")
            with patch(
                "skill_gather.integrations.newapi.urllib.request.urlopen",
                return_value=FakeResponse(
                    {
                        "text": "hello",
                        "language": "zh",
                        "segments": [{"start": 0, "end": 1, "text": "hello"}],
                    }
                ),
            ) as urlopen:
                result = NewApiClient(
                    base_url="https://api.example.test/v1/",
                    api_key="secret-key",
                ).transcribe_audio(audio, "asr-model")

        self.assertEqual(result["status"], "transcribed")
        self.assertEqual(result["text"], "hello")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.example.test/v1/audio/transcriptions")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.headers["Authorization"], "Bearer secret-key")

    def test_transcribe_audio_reports_invalid_json(self):
        response = Mock()
        response.status = 200
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b"not-json"

        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "audio.wav"
            audio.write_bytes(b"wav")
            with patch("skill_gather.integrations.newapi.urllib.request.urlopen", return_value=response):
                with self.assertRaises(NewApiError) as context:
                    NewApiClient(
                        base_url="https://api.example.test/v1",
                        api_key="secret-key",
                    ).transcribe_audio(audio, "asr-model")

        self.assertEqual(context.exception.code, "invalid_transcription_json")


if __name__ == "__main__":
    unittest.main()
