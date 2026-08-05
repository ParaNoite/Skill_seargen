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
    def test_search_intent_and_candidate_assessment_are_schema_limited(self):
        client = NewApiClient(base_url="https://api.example.test/v1", api_key="secret-key")
        with patch.object(
            NewApiClient,
            "_post_chat_completion_content",
            side_effect=[
                json.dumps({"goal": "Godot 导航", "facets": ["NavigationAgent"], "exclusions": ["付费课程"], "queries": ["Godot NavigationAgent 教程"]}),
                json.dumps({"assessments": [{"candidate_id": "cand-1", "relevance": 120, "matched_facets": ["NavigationAgent"], "reason": "标题直接匹配", "risk_flags": []}]}),
            ],
        ):
            intent = client.build_search_intent("Godot 导航", "technical", "text-model")
            assessments = client.assess_search_candidates(intent, [{"candidate_id": "cand-1", "title": "Godot NavigationAgent", "summary": "", "source_type": "video"}], "text-model")

        self.assertEqual(intent["queries"], ["Godot NavigationAgent 教程"])
        self.assertEqual(assessments["cand-1"]["relevance"], 100)

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

    def test_from_config_accepts_literal_api_key_for_local_testing(self):
        config = NewApiConfig(
            base_url="https://api.example.test/v1",
            api_key_env="sk-test-literal-key",
            vision_model="vision",
            asr_model="asr",
            distiller_model="distiller",
            judge_model="judge",
        )

        with patch.dict(os.environ, {}, clear=True):
            client = NewApiClient.from_config(config)

        self.assertIsNotNone(client)
        self.assertEqual(client.api_key, "sk-test-literal-key")

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

    def test_analyze_frame_accepts_markdown_json_content(self):
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
                                    "content": (
                                        "```json\n"
                                        '{"observations":[{"type":"frame_ocr","claim":"menu","raw_excerpt":"menu","confidence":0.8}]}\n'
                                        "```"
                                    )
                                }
                            }
                        ]
                    }
                ),
            ):
                result = NewApiClient(
                    base_url="https://api.example.test/v1/",
                    api_key="secret-key",
                ).analyze_frame(frame, "vision-model")

        self.assertEqual(result["observations"][0]["claim"], "menu")

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

    def test_distill_skill_posts_evidence_and_returns_ria_draft(self):
        with patch(
            "skill_gather.integrations.newapi.urllib.request.urlopen",
            return_value=FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "candidate_title": "Install editable Python package",
                                        "summary": "A reusable workflow for local package setup.",
                                        "ria": {
                                            "recall": "00:00:10 shows the command.",
                                            "interpret": "Use editable installs during local development.",
                                            "apply": ["Run python -m pip install -e ."],
                                            "boundary": "Only for Python packages with packaging metadata.",
                                            "test": ["Import the package after install."],
                                        },
                                        "evidence_refs": [
                                            {
                                                "timestamp": "00:00:10",
                                                "type": "frame_ocr",
                                                "claim": "The frame shows a pip install command.",
                                            }
                                        ],
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
            ).distill_skill(
                {
                    "items": [
                        {
                            "timestamp": "00:00:10",
                            "type": "frame_ocr",
                            "claim": "The frame shows a pip install command.",
                            "raw_excerpt": "python -m pip install -e .",
                            "confidence": 0.93,
                        }
                    ]
                },
                {"title": "Skill Demo", "author": "Teacher"},
                "distiller-model",
            )

        self.assertEqual(result["status"], "distilled")
        self.assertEqual(result["candidate_title"], "Install editable Python package")
        self.assertEqual(result["ria"]["apply"], ["Run python -m pip install -e ."])
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.example.test/v1/chat/completions")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "distiller-model")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        prompt_text = payload["messages"][0]["content"]
        self.assertIn("Skill Demo", prompt_text)
        self.assertIn("00:00:10", prompt_text)

    def test_distill_skill_compacts_large_evidence_payload(self):
        evidence = {
            "items": [
                {
                    "timestamp": f"00:00:{index:02d}",
                    "type": "asr",
                    "claim": "x" * 500,
                    "raw_excerpt": "y" * 500,
                    "confidence": 0.7,
                }
                for index in range(4)
            ]
        }

        with patch.dict(os.environ, {"SKILL_GATHER_DISTILL_EVIDENCE_LIMIT": "2", "SKILL_GATHER_DISTILL_CLAIM_CHARS": "12"}):
            with patch(
                "skill_gather.integrations.newapi.urllib.request.urlopen",
                return_value=FakeResponse(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {
                                            "candidate_title": "Compact evidence",
                                            "summary": "summary",
                                            "ria": {
                                                "recall": ["r"],
                                                "interpret": ["i"],
                                                "apply": ["a"],
                                                "boundary": ["b"],
                                                "test": ["t"],
                                            },
                                            "evidence_refs": [],
                                        }
                                    )
                                }
                            }
                        ]
                    }
                ),
            ) as urlopen:
                NewApiClient(
                    base_url="https://api.example.test/v1/",
                    api_key="secret-key",
                ).distill_skill(evidence, {"title": "Demo"}, "distiller-model")

        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        prompt = body["messages"][0]["content"]
        self.assertIn('"omitted_item_count": 2', prompt)
        self.assertNotIn("x" * 500, prompt)

    def test_distill_skill_omits_manifest_risk_flags_from_prompt(self):
        with patch(
            "skill_gather.integrations.newapi.urllib.request.urlopen",
            return_value=FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "candidate_title": "Clean prompt",
                                        "summary": "summary",
                                        "ria": {
                                            "recall": ["r"],
                                            "interpret": ["i"],
                                            "apply": ["a"],
                                            "boundary": ["b"],
                                            "test": ["t"],
                                        },
                                        "evidence_refs": [],
                                    }
                                )
                            }
                        }
                    ]
                }
            ),
        ) as urlopen:
            NewApiClient(
                base_url="https://api.example.test/v1/",
                api_key="secret-key",
            ).distill_skill(
                {"items": [{"timestamp": "00:00:01", "type": "asr", "claim": "step"}]},
                {"title": "Demo", "risk_flags": ["distillation_failed"]},
                "distiller-model",
            )

        request = urlopen.call_args.args[0]
        prompt = json.loads(request.data.decode("utf-8"))["messages"][0]["content"]
        self.assertIn("Output only valid JSON", prompt)
        self.assertNotIn("distillation_failed", prompt)

    def test_distill_skill_normalizes_dict_ria_items(self):
        with patch(
            "skill_gather.integrations.newapi.urllib.request.urlopen",
            return_value=FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "candidate_title": "Dict RIA",
                                        "summary": "summary",
                                        "ria": {
                                            "recall": [{"claim": "first", "timestamp": "00:00:01"}],
                                            "interpret": ["i"],
                                            "apply": ["a"],
                                            "boundary": ["b"],
                                            "test": ["t"],
                                        },
                                        "evidence_refs": [],
                                    }
                                )
                            }
                        }
                    ]
                }
            ),
        ):
            result = NewApiClient(
                base_url="https://api.example.test/v1/",
                api_key="secret-key",
            ).distill_skill(
                {"items": [{"timestamp": "00:00:01", "type": "asr", "claim": "step"}]},
                {"title": "Demo"},
                "distiller-model",
            )

        self.assertEqual(result["ria"]["recall"], ["first — 00:00:01"])

    def test_judge_skill_posts_draft_and_returns_score(self):
        with patch(
            "skill_gather.integrations.newapi.urllib.request.urlopen",
            return_value=FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "score": 86,
                                        "rationale": "The draft is actionable and evidence backed.",
                                        "risk_flags": ["single_channel_evidence"],
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
            ).judge_skill(
                {
                    "candidate_title": "Install editable Python package",
                    "ria": {
                        "recall": "00:00:10 shows the command.",
                        "interpret": "Use editable installs during local development.",
                        "apply": ["Run python -m pip install -e ."],
                        "boundary": "Only for Python packages with packaging metadata.",
                        "test": ["Import the package after install."],
                    },
                },
                {
                    "items": [
                        {
                            "timestamp": "00:00:10",
                            "type": "frame_ocr",
                            "claim": "The frame shows a pip install command.",
                        }
                    ]
                },
                {"title": "Skill Demo"},
                "judge-model",
            )

        self.assertEqual(result["status"], "judged")
        self.assertEqual(result["score"], 86)
        self.assertEqual(result["risk_flags"], ["single_channel_evidence"])
        self.assertEqual(result["_audit"]["response_length"] > 0, True)
        self.assertIn("response_sha256", result["_audit"])
        self.assertEqual(result["_audit"]["response_shape"]["type"], "object")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.example.test/v1/chat/completions")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "judge-model")
        prompt_text = payload["messages"][0]["content"]
        self.assertIn("Install editable Python package", prompt_text)
        self.assertIn("Skill Demo", prompt_text)

    def test_distill_skill_reports_invalid_ria_shape(self):
        with patch(
            "skill_gather.integrations.newapi.urllib.request.urlopen",
            return_value=FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "candidate_title": "Install editable Python package",
                                        "ria": {
                                            "recall": "00:00:10 shows the command.",
                                            "interpret": "Use editable installs during local development.",
                                            "apply": "",
                                            "boundary": "Only for Python packages with packaging metadata.",
                                            "test": [],
                                        },
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
                    base_url="https://api.example.test/v1/",
                    api_key="secret-key",
                ).distill_skill(
                    {"items": [{"timestamp": "00:00:10", "type": "asr", "claim": "Use editable installs."}]},
                    {"title": "Skill Demo"},
                    "distiller-model",
                )

        self.assertEqual(context.exception.code, "invalid_distillation_shape")

    def test_distill_skill_audits_parse_failure_without_persisting_response_content(self):
        with patch(
            "skill_gather.integrations.newapi.urllib.request.urlopen",
            return_value=FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "not-json token=secret https://example.test/private"
                            }
                        }
                    ]
                }
            ),
        ):
            with self.assertRaises(NewApiError) as context:
                NewApiClient(
                    base_url="https://api.example.test/v1/",
                    api_key="secret-key",
                ).distill_skill(
                    {"items": [{"timestamp": "00:00:10", "type": "asr", "claim": "Use editable installs."}]},
                    {"title": "Skill Demo"},
                    "distiller-model",
                )

        audit = context.exception.response_audit
        self.assertEqual(audit["format"], "unparsed")
        self.assertIn("response_sha256", audit)
        self.assertNotIn("token=secret", json.dumps(audit))
        self.assertNotIn("example.test", json.dumps(audit))


if __name__ == "__main__":
    unittest.main()
