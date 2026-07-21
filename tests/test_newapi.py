import json
import os
import tempfile
import urllib.error
import unittest
from base64 import b64encode
from io import BytesIO
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

    def test_analyze_frame_posts_multimodal_request_and_returns_observations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            frame = Path(temp_dir) / "frame.jpg"
            frame.write_bytes(b"jpeg-bytes")
            with patch(
                "skill_gather.integrations.newapi.urllib.request.urlopen",
                return_value=FakeResponse(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {
                                            "observations": [
                                                {
                                                    "type": "frame_ocr",
                                                    "claim": "The frame shows a pip install command.",
                                                    "raw_excerpt": "python -m pip install -e .",
                                                    "confidence": 0.93,
                                                }
                                            ]
                                        }
                                    )
                                }
                            }
                        ]
                    }
                ),
            ) as urlopen:
                result = NewApiClient(
                    base_url="https://api.example.test/v1/",
                    api_key="secret-key",
                ).analyze_frame(frame, "vision-model")

        self.assertEqual(result["status"], "analyzed")
        self.assertEqual(result["observations"][0]["type"], "frame_ocr")
        self.assertEqual(result["observations"][0]["confidence"], 0.93)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.example.test/v1/chat/completions")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.headers["Authorization"], "Bearer secret-key")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "vision-model")
        image_part = payload["messages"][0]["content"][1]
        self.assertEqual(
            image_part["image_url"]["url"],
            f"data:image/jpeg;base64,{b64encode(b'jpeg-bytes').decode('ascii')}",
        )

    def test_analyze_frame_reports_missing_file(self):
        with self.assertRaises(NewApiError) as context:
            NewApiClient(
                base_url="https://api.example.test/v1",
                api_key="secret-key",
            ).analyze_frame("missing.jpg", "vision-model")

        self.assertEqual(context.exception.code, "frame_file_missing")

    def test_analyze_frame_reports_invalid_model_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            frame = Path(temp_dir) / "frame.jpg"
            frame.write_bytes(b"jpeg-bytes")
            with patch(
                "skill_gather.integrations.newapi.urllib.request.urlopen",
                return_value=FakeResponse(
                    {"choices": [{"message": {"content": "not-json"}}]}
                ),
            ):
                with self.assertRaises(NewApiError) as context:
                    NewApiClient(
                        base_url="https://api.example.test/v1",
                        api_key="secret-key",
                    ).analyze_frame(frame, "vision-model")

        self.assertEqual(context.exception.code, "invalid_vision_json")

    def test_analyze_frame_reports_invalid_chat_completion_shape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            frame = Path(temp_dir) / "frame.jpg"
            frame.write_bytes(b"jpeg-bytes")
            with patch(
                "skill_gather.integrations.newapi.urllib.request.urlopen",
                return_value=FakeResponse({"choices": []}),
            ):
                with self.assertRaises(NewApiError) as context:
                    NewApiClient(
                        base_url="https://api.example.test/v1",
                        api_key="secret-key",
                    ).analyze_frame(frame, "vision-model")

        self.assertEqual(context.exception.code, "invalid_vision_response_shape")

    def test_analyze_frame_reports_invalid_observation_shape(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            frame = Path(temp_dir) / "frame.jpg"
            frame.write_bytes(b"jpeg-bytes")
            with patch(
                "skill_gather.integrations.newapi.urllib.request.urlopen",
                return_value=FakeResponse(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {"observations": "not-a-list"}
                                    )
                                }
                            }
                        ]
                    }
                ),
            ):
                with self.assertRaises(NewApiError) as context:
                    NewApiClient(
                        base_url="https://api.example.test/v1",
                        api_key="secret-key",
                    ).analyze_frame(frame, "vision-model")

        self.assertEqual(context.exception.code, "invalid_vision_shape")

    def test_analyze_frame_reports_invalid_observation_item(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            frame = Path(temp_dir) / "frame.jpg"
            frame.write_bytes(b"jpeg-bytes")
            with patch(
                "skill_gather.integrations.newapi.urllib.request.urlopen",
                return_value=FakeResponse(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {
                                            "observations": [
                                                {
                                                    "type": "frame_ocr",
                                                    "claim": "",
                                                }
                                            ]
                                        }
                                    )
                                }
                            }
                        ]
                    }
                ),
            ):
                with self.assertRaises(NewApiError) as context:
                    NewApiClient(
                        base_url="https://api.example.test/v1",
                        api_key="secret-key",
                    ).analyze_frame(frame, "vision-model")

        self.assertEqual(context.exception.code, "invalid_vision_shape")

    def test_analyze_frame_reports_http_failure_with_safe_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            frame = Path(temp_dir) / "frame.jpg"
            frame.write_bytes(b"jpeg-bytes")
            error = urllib.error.HTTPError(
                url="https://api.example.test/v1/chat/completions",
                code=500,
                msg="server error",
                hdrs={},
                fp=BytesIO(b"failed https://example.test/tmp token=x"),
            )
            with patch(
                "skill_gather.integrations.newapi.urllib.request.urlopen",
                side_effect=error,
            ):
                with self.assertRaises(NewApiError) as context:
                    NewApiClient(
                        base_url="https://api.example.test/v1",
                        api_key="secret-key",
                    ).analyze_frame(frame, "vision-model")

        self.assertEqual(context.exception.code, "vision_failed")
        self.assertEqual(context.exception.status_code, 500)
        self.assertIn("[redacted-url]", context.exception.safe_summary)
        self.assertNotIn("token=x", context.exception.safe_summary)

    def test_analyze_frame_reports_url_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            frame = Path(temp_dir) / "frame.jpg"
            frame.write_bytes(b"jpeg-bytes")
            with patch(
                "skill_gather.integrations.newapi.urllib.request.urlopen",
                side_effect=urllib.error.URLError("temporary dns failure"),
            ):
                with self.assertRaises(NewApiError) as context:
                    NewApiClient(
                        base_url="https://api.example.test/v1",
                        api_key="secret-key",
                    ).analyze_frame(frame, "vision-model")

        self.assertEqual(context.exception.code, "vision_unreachable")
        self.assertIn("temporary dns failure", context.exception.safe_summary)


if __name__ == "__main__":
    unittest.main()
