import json
import os
import tempfile
import urllib.error
import unittest
from base64 import b64encode
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from skill_gather.config import NewApiConfig, NewApiRequestProfile
from skill_gather.integrations.newapi import NewApiClient, NewApiError, _compact_evidence_timeline


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
    def test_distill_course_keeps_only_existing_evidence_references(self):
        client = NewApiClient(base_url="https://api.example.test/v1", api_key="secret-key")
        response = {
            "title": "导航入门",
            "learning_outcomes": ["理解导航"],
            "overview": "从路径开始。",
            "lessons": [{"heading": "路径", "content": "先设置目标。", "evidence_refs": ["S1:sentence:1", "invented:ref"]}],
            "pitfalls": ["不要猜测 API"],
            "exercises": ["完成最小示例"],
            "next_steps": ["阅读官方文档"],
        }
        fusion = {"conclusions": [{"claim": "设置目标", "citations": [{"source_id": "S1", "locator": "sentence:1"}]}]}
        with patch.object(NewApiClient, "_post_chat_completion_content", return_value=json.dumps(response)):
            course = client.distill_course("Godot 导航", "technical", fusion, "text-model")

        self.assertEqual(course["lessons"][0]["evidence_refs"], ["S1:sentence:1"])

    def test_distill_course_prioritizes_actionable_github_evidence_over_video_chrome(self):
        client = NewApiClient(base_url="https://api.example.test/v1", api_key="secret-key")
        response = {
            "title": "Three.js 跑酷",
            "learning_outcomes": ["理解启动流程"],
            "overview": "从可执行证据开始。",
            "lessons": [{"heading": "验证", "content": "先运行测试。", "evidence_refs": ["G1:commands:README.md"]}],
            "pitfalls": [],
            "exercises": [],
            "next_steps": [],
        }
        video_items = [
            {
                "claim": f"The browser tab title shows Vite App and the system clock shows 10:{index:02d}.",
                "supporting_source_count": 1,
                "confidence": 0.99,
                "citations": [{"source_id": "V1", "locator": f"00:00:{index:02d}", "source_type": "video"}],
            }
            for index in range(20)
        ]
        github_item = {
            "claim": "Run `npm run test:run` to verify the Three.js runner before release.",
            "supporting_source_count": 1,
            "confidence": 0.8,
            "citations": [{"source_id": "G1", "locator": "commands:README.md", "source_type": "github"}],
        }
        with patch.object(NewApiClient, "_post_chat_completion_content", return_value=json.dumps(response)) as post:
            client.distill_course("Three.js 浏览器 3D 跑酷", "technical", {"conclusions": video_items + [github_item]}, "text-model")

        prompt = post.call_args.args[0]["messages"][0]["content"]
        self.assertIn("npm run test:run", prompt)

    def test_probe_model_marks_catalogued_but_unknown_model_unavailable(self):
        error = urllib.error.HTTPError(
            "https://api.example.test/v1/chat/completions",
            404,
            "Not Found",
            None,
            BytesIO(b'{"error":"unknown model"}'),
        )
        with patch("skill_gather.integrations.newapi.urllib.request.urlopen", side_effect=error):
            result = NewApiClient(
                base_url="https://api.example.test/v1",
                api_key="secret-key",
            ).probe_model("catalog-only-model", "vision")

        self.assertEqual(result["model"], "catalog-only-model")
        self.assertFalse(result["available"])
        self.assertEqual(result["error_code"], "model_probe_failed")
        self.assertEqual(result["status_code"], 404)

    def test_probe_model_uses_inline_image_for_vision(self):
        vision_response = json.dumps({
            "observations": [
                {"type": "dominant_color", "claim": "red", "raw_excerpt": "", "confidence": 1.0}
            ]
        })
        with patch(
            "skill_gather.integrations.newapi.urllib.request.urlopen",
            return_value=FakeResponse({"choices": [{"message": {"content": vision_response}}]}),
        ) as urlopen:
            result = NewApiClient(
                base_url="https://api.example.test/v1",
                api_key="secret-key",
            ).probe_model("vision-model", "vision")

        body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertTrue(result["available"])
        self.assertEqual(body["messages"][0]["content"][1]["type"], "image_url")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertGreaterEqual(body["max_tokens"], 64)

    def test_probe_model_rejects_text_only_response_to_vision_prompt(self):
        with patch(
            "skill_gather.integrations.newapi.urllib.request.urlopen",
            return_value=FakeResponse({"choices": [{"message": {"content": "OK"}}]}),
        ):
            result = NewApiClient(
                base_url="https://api.example.test/v1",
                api_key="secret-key",
            ).probe_model("text-model", "vision")

        self.assertFalse(result["available"])
        self.assertEqual(result["summary"], "model_probe_unexpected_response")
    def test_search_intent_and_candidate_assessment_are_schema_limited(self):
        client = NewApiClient(base_url="https://api.example.test/v1", api_key="secret-key")
        with patch.object(
            NewApiClient,
            "_post_chat_completion_content",
            side_effect=[
                json.dumps({"goal": ["Godot 导航"], "facets": ["NavigationAgent"], "exclusions": ["付费课程"], "queries": ["Godot NavigationAgent 教程"]}),
                json.dumps({"assessments": [{"candidate_id": "cand-1", "relevance": 120, "matched_facets": ["NavigationAgent"], "reason": "标题直接匹配", "risk_flags": []}]}),
            ],
        ):
            intent = client.build_search_intent("Godot 导航", "technical", "text-model")
            assessments = client.assess_search_candidates(intent, [{"candidate_id": "cand-1", "title": "Godot NavigationAgent", "summary": "", "source_type": "video"}], "text-model")

        self.assertEqual(intent["goal"], "Godot 导航")
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

    def test_from_config_rejects_literal_api_key(self):
        config = NewApiConfig(
            base_url="https://api.example.test/v1",
            api_key_env="sk_test_fixture",
            vision_model="vision",
            asr_model="asr",
            distiller_model="distiller",
            judge_model="judge",
            timeout_sec=240,
            request_profiles={
                "vision": NewApiRequestProfile(temperature=0.0, max_tokens=1200)
            },
        )

        with patch.dict(os.environ, {}, clear=True):
            client = NewApiClient.from_config(config)

        self.assertIsNone(client)

    def test_chat_completion_applies_stage_request_profile(self):
        with patch(
            "skill_gather.integrations.newapi.urllib.request.urlopen",
            return_value=FakeResponse(
                {"choices": [{"message": {"content": '{"observations":[]}'}}]}
            ),
        ) as urlopen:
            NewApiClient(
                base_url="https://api.example.test/v1/",
                api_key="secret-key",
                request_profiles={
                    "vision": NewApiRequestProfile(
                        temperature=0.0,
                        top_p=0.9,
                        max_tokens=1200,
                        reasoning_effort="medium",
                    )
                },
            )._post_chat_completion_content(
                {
                    "model": "vision-model",
                    "messages": [{"role": "user", "content": "json"}],
                    "max_tokens": 128,
                },
                operation="vision",
                http_error_code="vision_failed",
                unreachable_code="vision_unreachable",
                profile="vision",
            )

        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["temperature"], 0.0)
        self.assertEqual(payload["top_p"], 0.9)
        self.assertEqual(payload["max_tokens"], 1200)
        self.assertEqual(payload["reasoning_effort"], "medium")

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
        self.assertIn('"omitted_item_count": 3', prompt)
        self.assertNotIn("x" * 500, prompt)

    def test_compact_evidence_removes_near_duplicates_and_keeps_temporal_edges(self):
        evidence = {
            "video_duration_sec": 60,
            "frame_budget": 8,
            "sampling_strategy": "ffmpeg_interval_10s",
            "items": [
                {
                    "timestamp": "00:00:02",
                    "type": "asr",
                    "claim": "先打开项目设置。",
                    "raw_excerpt": "先打开项目设置。",
                    "confidence": 0.7,
                },
                {
                    "timestamp": "00:00:04",
                    "type": "asr",
                    "claim": "先打开项目设置。",
                    "raw_excerpt": "先打开项目设置。",
                    "confidence": 0.7,
                },
                {
                    "timestamp": "00:00:06",
                    "type": "frame_ocr",
                    "claim": "先打开项目设置。",
                    "raw_excerpt": "Project Settings",
                    "confidence": 0.8,
                },
                {
                    "timestamp": "00:00:52",
                    "type": "workflow_step",
                    "claim": "最后运行最小验证。",
                    "raw_excerpt": "Run the smoke test",
                    "confidence": 0.9,
                },
            ],
        }

        with patch.dict(os.environ, {"SKILL_GATHER_DISTILL_EVIDENCE_LIMIT": "3"}):
            compact = _compact_evidence_timeline(evidence)

        claims = [item["claim"] for item in compact["items"]]
        self.assertEqual(len(compact["items"]), 2)
        self.assertEqual(claims[0], "先打开项目设置。")
        self.assertEqual(claims[-1], "最后运行最小验证。")
        self.assertEqual(compact["omitted_item_count"], 2)

    def test_compact_evidence_prioritizes_visual_item_near_transcript_cue(self):
        evidence = {
            "video_duration_sec": 40,
            "items": [
                {
                    "timestamp": "00:00:05",
                    "type": "asr",
                    "claim": "注意这里，接下来执行安装命令。",
                    "raw_excerpt": "注意这里，接下来执行安装命令。",
                    "confidence": 0.7,
                },
                {
                    "timestamp": "00:00:08",
                    "type": "frame_ocr",
                    "claim": "终端显示安装命令。",
                    "raw_excerpt": "python -m pip install -e .",
                    "confidence": 0.9,
                },
                {
                    "timestamp": "00:00:30",
                    "type": "metadata_title",
                    "claim": "视频标题说明主题：安装教程",
                    "raw_excerpt": "安装教程",
                    "confidence": 0.45,
                },
                {
                    "timestamp": "00:00:36",
                    "type": "example",
                    "claim": "示例完成。",
                    "raw_excerpt": "done",
                    "confidence": 0.8,
                },
            ],
        }

        with patch.dict(os.environ, {"SKILL_GATHER_DISTILL_EVIDENCE_LIMIT": "3"}):
            compact = _compact_evidence_timeline(evidence)

        claims = [item["claim"] for item in compact["items"]]
        self.assertIn("注意这里，接下来执行安装命令。", claims)
        self.assertIn("终端显示安装命令。", claims)
        self.assertNotIn("视频标题说明主题：安装教程", claims)

    def test_compact_evidence_protects_cue_pair_when_budget_is_tight(self):
        evidence = {
            "video_duration_sec": 102,
            "items": [
                {
                    "timestamp": "00:00:00",
                    "type": "asr",
                    "claim": "Notice this command and run it next.",
                    "raw_excerpt": "Notice this command and run it next.",
                    "confidence": 0.7,
                },
                {
                    "timestamp": "00:00:01",
                    "type": "frame_ocr",
                    "claim": "The terminal shows the install command.",
                    "raw_excerpt": "python -m pip install -e .",
                    "confidence": 0.9,
                },
                {
                    "timestamp": "00:01:40",
                    "type": "workflow_step",
                    "claim": "The workflow reaches its final step.",
                    "raw_excerpt": "final step",
                    "confidence": 0.9,
                },
                {
                    "timestamp": "00:01:41",
                    "type": "example",
                    "claim": "The final example completes.",
                    "raw_excerpt": "done",
                    "confidence": 0.8,
                },
            ],
        }

        with patch.dict(os.environ, {"SKILL_GATHER_DISTILL_EVIDENCE_LIMIT": "2"}):
            compact = _compact_evidence_timeline(evidence)

        self.assertEqual(
            [item["type"] for item in compact["items"]],
            ["asr", "frame_ocr"],
        )

    def test_compact_evidence_keeps_distinct_commands_with_same_visual_claim(self):
        evidence = {
            "video_duration_sec": 20,
            "items": [
                {
                    "timestamp": "00:00:02",
                    "type": "code_command",
                    "claim": "The terminal displays the command.",
                    "raw_excerpt": "pip install foo",
                    "confidence": 0.9,
                },
                {
                    "timestamp": "00:00:06",
                    "type": "code_command",
                    "claim": "The terminal displays the command.",
                    "raw_excerpt": "pip install bar",
                    "confidence": 0.9,
                },
                {
                    "timestamp": "00:00:18",
                    "type": "workflow_step",
                    "claim": "Run the verification step.",
                    "raw_excerpt": "run test",
                    "confidence": 0.8,
                },
            ],
        }

        with patch.dict(os.environ, {"SKILL_GATHER_DISTILL_EVIDENCE_LIMIT": "3"}):
            compact = _compact_evidence_timeline(evidence)

        self.assertEqual(
            [item["raw_excerpt"] for item in compact["items"]],
            ["pip install foo", "pip install bar", "run test"],
        )

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
