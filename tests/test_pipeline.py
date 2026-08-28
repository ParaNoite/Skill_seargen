import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skill_gather.adapters.bilibili import build_initial_manifest
from skill_gather.config import parse_config
from skill_gather.integrations import FfmpegError, NewApiError, YtDlpError
from skill_gather.models import PIPELINE_STAGES
from skill_gather.pipeline import run_video_pipeline
from skill_gather.pipeline.runner import resolve_media_file
from skill_gather.runs import RunStore, read_json, write_json
from skill_gather.source import infer_source


CONFIG = {
    "providers": {
        "newapi": {
            "base_url": "https://api.example.test/v1",
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


class FakeMetadataProbe:
    def __init__(self, metadata=None, error: Exception | None = None):
        self.metadata = metadata or {}
        self.error = error

    def probe_metadata(self, url):
        if self.error is not None:
            raise self.error
        return self.metadata


class FakeMediaDownloader:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result or {
            "status": "downloaded",
            "target_dir": "",
            "output_template": "%(id)s.%(ext)s",
            "returncode": 0,
        }
        self.error = error
        self.calls = 0

    def download_media(self, url, target_dir):
        self.calls += 1
        if self.error is not None:
            raise self.error
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        media_file = target / "BV1xx411c7mD.mp4"
        media_file.write_text("placeholder", encoding="utf-8")
        result = dict(self.result)
        result["target_dir"] = str(target_dir)
        if "media_files" not in result:
            result["media_files"] = [str(media_file)]
        return result


class FakeMediaProcessor:
    def __init__(self, audio_error: Exception | None = None, frame_error: Exception | None = None):
        self.audio_error = audio_error
        self.frame_error = frame_error

    def extract_audio(self, media_file, target_path):
        if self.audio_error is not None:
            raise self.audio_error
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("audio placeholder", encoding="utf-8")
        return {
            "status": "extracted",
            "audio_path": str(target),
            "source_media": str(media_file),
            "returncode": 0,
        }

    def extract_frames(self, media_file, target_dir, *, interval_sec=10):
        if self.frame_error is not None:
            raise self.frame_error
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        frames = [target / "frame-000001.jpg", target / "frame-000002.jpg"]
        for frame in frames:
            frame.write_text("frame placeholder", encoding="utf-8")
        return {
            "status": "extracted",
            "frame_dir": str(target),
            "frame_pattern": "frame-%06d.jpg",
            "frame_paths": [str(frame) for frame in frames],
            "interval_sec": interval_sec,
            "source_media": str(media_file),
            "returncode": 0,
        }


class FakeAsrClient:
    def __init__(self, error: Exception | None = None):
        self.error = error

    def transcribe_audio(self, audio_file, model):
        if self.error is not None:
            raise self.error
        return {
            "status": "transcribed",
            "model": model,
            "audio_path": str(audio_file),
            "text": "first step",
            "language": "zh",
            "segments": [{"start": 10.0, "end": 12.0, "text": "first step"}],
            "returncode": 0,
        }


class FakeVisionClient:
    def __init__(self, error: Exception | None = None, fail_on_call: int | None = None):
        self.error = error
        self.fail_on_call = fail_on_call
        self.calls = 0

    def analyze_frame(self, frame_file, model):
        self.calls += 1
        if self.error is not None and (self.fail_on_call is None or self.calls == self.fail_on_call):
            raise self.error
        return {
            "status": "analyzed",
            "model": model,
            "frame_path": str(frame_file),
            "observations": [
                {
                    "type": "frame_ocr",
                    "claim": "The frame shows a pip install command.",
                    "raw_excerpt": "python -m pip install -e .",
                    "confidence": 0.93,
                }
            ],
            "returncode": 0,
        }


class FakeDistillerClient:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls = 0

    def distill_skill(self, evidence_timeline, manifest, model):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {
            "status": "distilled",
            "model": model,
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
            "returncode": 0,
        }


class RetryingDistillerClient(FakeDistillerClient):
    def distill_skill(self, evidence_timeline, manifest, model):
        self.calls += 1
        if self.calls == 1:
            raise NewApiError(
                "invalid response https://example.test/tmp token=secret",
                code="invalid_distillation_json",
            )
        return super().distill_skill(evidence_timeline, manifest, model)


class FakeJudgeClient:
    def __init__(self, score: int = 86, error: Exception | None = None, risk_flags=None):
        self.score = score
        self.error = error
        self.risk_flags = risk_flags or []
        self.calls = 0

    def judge_skill(self, distillation, evidence_timeline, manifest, model):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {
            "status": "judged",
            "model": model,
            "score": self.score,
            "rationale": "The draft is actionable and evidence backed.",
            "risk_flags": list(self.risk_flags),
            "returncode": 0,
        }


class PipelineTests(unittest.TestCase):
    def test_evidence_only_pipeline_stops_after_timeline_merge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            state = store.start_or_resume(source.source, source.source_id)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=build_initial_manifest(url, source),
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "视频", "duration": 60}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
                vision_client=FakeVisionClient(),
                evidence_only=True,
            )

            self.assertEqual(result.status, "completed")
            self.assertIn("timeline_merge", result.completed_stages)
            self.assertNotIn("distill", result.completed_stages)
            self.assertFalse((store.run_path(result.run_id) / "distillation.json").exists())
            self.assertTrue(store.evidence_timeline_path(result.run_id).exists())

    def test_evidence_only_pipeline_fails_when_only_metadata_evidence_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            state = store.start_or_resume(source.source, source.source_id)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=build_initial_manifest(url, source),
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "只有标题", "duration": 60}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(
                    NewApiError("ASR unavailable", code="transcription_failed", status_code=503)
                ),
                vision_client=FakeVisionClient(),
                vision_mode="off",
                evidence_only=True,
            )

            self.assertEqual(result.status, "failed")
            self.assertIn("实质证据", result.failure_reason)
            timeline = read_json(store.evidence_timeline_path(result.run_id))
            self.assertEqual({item["type"] for item in timeline["items"]}, {"metadata_title"})

    def test_run_video_pipeline_writes_successful_vision_ocr_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Skill Demo"}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
                vision_client=FakeVisionClient(),
            )

            vision = read_json(store.run_path(result.run_id) / "vision_ocr.json")
            self.assertEqual(vision["status"], "analyzed")
            self.assertEqual(vision["model"], "vision")
            self.assertEqual(len(vision["items"]), 2)
            self.assertEqual(vision["items"][0]["timestamp"], "00:00:00")
            self.assertEqual(vision["items"][0]["observations"][0]["type"], "frame_ocr")
            self.assertEqual(vision["errors"], [])

    def test_run_video_pipeline_keeps_partial_vision_failures_auditable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Skill Demo"}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
                vision_client=FakeVisionClient(
                    NewApiError(
                        "vision failed https://example.test/tmp token=x",
                        code="vision_failed",
                        status_code=500,
                    ),
                    fail_on_call=1,
                ),
            )

            run_dir = store.run_path(result.run_id)
            vision = read_json(run_dir / "vision_ocr.json")
            saved_manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(vision["status"], "partial")
            self.assertEqual(len(vision["items"]), 1)
            self.assertEqual(len(vision["errors"]), 1)
            self.assertEqual(vision["errors"][0]["reason_code"], "vision_failed")
            self.assertEqual(vision["errors"][0]["returncode"], 500)
            self.assertIn("[redacted-url]", vision["errors"][0]["summary"])
            self.assertNotIn("token=x", vision["errors"][0]["summary"])
            self.assertIn("vision_ocr_partial", saved_manifest["risk_flags"])

    def test_run_video_pipeline_skips_vision_when_api_key_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            with patch.dict(os.environ, {}, clear=True):
                result = run_video_pipeline(
                    config=config,
                    store=store,
                    state=state,
                    manifest=manifest,
                    out_dir=Path(temp_dir) / "skills",
                    metadata_probe=FakeMetadataProbe({"title": "Skill Demo"}),
                    media_downloader=FakeMediaDownloader(),
                    media_processor=FakeMediaProcessor(),
                    asr_client=FakeAsrClient(),
                )

            run_dir = store.run_path(result.run_id)
            vision = read_json(run_dir / "vision_ocr.json")
            saved_manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(vision["status"], "skipped")
            self.assertEqual(vision["reason"], "newapi API key is not configured")
            self.assertEqual(vision["model"], "vision")
            self.assertIn("vision_ocr_skipped", saved_manifest["risk_flags"])

    def test_run_video_pipeline_uses_faster_whisper_for_asr_model_reference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            raw_config = {
                **CONFIG,
                "providers": {
                    "newapi": {
                        **CONFIG["providers"]["newapi"],
                        "asr_model": "faster-whisper:base",
                    }
                },
            }
            config = parse_config(raw_config)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            with patch("skill_gather.pipeline.runner.FasterWhisperClient") as client_class:
                client_class.from_model.return_value = FakeAsrClient()
                result = run_video_pipeline(
                    config=config,
                    store=store,
                    state=state,
                    manifest=manifest,
                    out_dir=Path(temp_dir) / "skills",
                    metadata_probe=FakeMetadataProbe({"title": "Skill Demo"}),
                    media_downloader=FakeMediaDownloader(),
                    media_processor=FakeMediaProcessor(),
                    vision_client=FakeVisionClient(),
                    distiller_client=FakeDistillerClient(),
                    judge_client=FakeJudgeClient(),
                )

            asr = read_json(store.run_path(result.run_id) / "asr.json")
            client_class.from_model.assert_called_once_with("faster-whisper:base")
            self.assertEqual(asr["status"], "transcribed")
            self.assertEqual(asr["model"], "faster-whisper:base")

    def test_run_video_pipeline_does_not_overwrite_existing_vision_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)
            run_dir = store.run_path(state.run_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            write_json(
                run_dir / "vision_ocr.json",
                {
                    "status": "analyzed",
                    "model": "previous-vision",
                    "items": [{"timestamp": "00:00:00", "observations": []}],
                    "errors": [],
                },
            )
            vision_client = FakeVisionClient()

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Skill Demo"}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
                vision_client=vision_client,
            )

            vision = read_json(store.run_path(result.run_id) / "vision_ocr.json")
            self.assertEqual(vision["model"], "previous-vision")
            self.assertEqual(vision_client.calls, 0)

    def test_run_video_pipeline_merges_asr_and_visual_evidence_timeline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Skill Demo", "duration": 120}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
                vision_client=FakeVisionClient(),
            )

            timeline = read_json(store.evidence_timeline_path(result.run_id))
            self.assertEqual(
                [item["timestamp"] for item in timeline["items"]],
                ["00:00:00", "00:00:00", "00:00:10", "00:00:10"],
            )
            self.assertEqual(timeline["items"][0]["type"], "metadata_title")
            self.assertEqual(timeline["items"][1]["type"], "frame_ocr")
            self.assertEqual(timeline["items"][1]["claim"], "The frame shows a pip install command.")
            self.assertEqual(timeline["items"][2]["type"], "asr")
            self.assertEqual(timeline["items"][3]["raw_excerpt"], "python -m pip install -e .")
            distillation = read_json(store.run_path(result.run_id) / "distillation.json")
            self.assertEqual(distillation["reason"], "newapi API key is not configured")

    def test_run_video_pipeline_can_limit_vision_frames(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)
            vision_client = FakeVisionClient()

            with patch.dict(os.environ, {"SKILL_GATHER_VISION_FRAME_LIMIT": "1"}):
                run_video_pipeline(
                    config=config,
                    store=store,
                    state=state,
                    manifest=manifest,
                    out_dir=Path(temp_dir) / "skills",
                    metadata_probe=FakeMetadataProbe({"title": "Skill Demo", "duration": 120}),
                    media_downloader=FakeMediaDownloader(),
                    media_processor=FakeMediaProcessor(),
                    asr_client=FakeAsrClient(),
                    vision_client=vision_client,
                )

            vision = read_json(store.run_path(state.run_id) / "vision_ocr.json")
            saved_manifest = read_json(store.manifest_path(state.run_id))
            self.assertEqual(vision_client.calls, 1)
            self.assertEqual(vision["frame_count"], 2)
            self.assertEqual(vision["analyzed_frame_count"], 1)
            self.assertIn("vision_frame_limit_applied", saved_manifest["risk_flags"])

    def test_run_video_pipeline_can_disable_remote_vision_and_records_cost_metrics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)
            vision_client = FakeVisionClient()

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Skill Demo", "duration": 120}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
                vision_client=vision_client,
                vision_mode="off",
            )

            vision = read_json(store.run_path(result.run_id) / "vision_ocr.json")
            self.assertEqual(vision_client.calls, 0)
            self.assertEqual(vision["status"], "skipped")
            self.assertEqual(vision["strategy"], "off")
            self.assertEqual(vision["source_frame_count"], 2)
            self.assertEqual(vision["remote_call_count"], 0)

    def test_run_video_pipeline_prefilter_rejects_obviously_too_short_video_before_download(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)
            downloader = FakeMediaDownloader()

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "", "duration": 8}),
                media_downloader=downloader,
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
                vision_client=FakeVisionClient(),
                distiller_client=FakeDistillerClient(),
                judge_client=FakeJudgeClient(),
            )

            prefilter = read_json(store.run_path(result.run_id) / "prefilter.json")
            media = read_json(store.run_path(result.run_id) / "media_extract.json")
            self.assertEqual(result.status, "failed")
            self.assertEqual(prefilter["status"], "rejected")
            self.assertEqual(prefilter["reason_code"], "duration_too_short")
            self.assertEqual(media["status"], "skipped")
            self.assertEqual(downloader.calls, 0)

    def test_run_video_pipeline_writes_successful_distillation_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Skill Demo", "duration": 120}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
                vision_client=FakeVisionClient(),
                distiller_client=FakeDistillerClient(),
            )

            distillation = read_json(store.run_path(result.run_id) / "distillation.json")
            self.assertEqual(distillation["status"], "distilled")
            self.assertEqual(distillation["model"], "distiller")
            self.assertEqual(distillation["candidate_title"], "Install editable Python package")
            self.assertEqual(distillation["ria"]["apply"], ["Run python -m pip install -e ."])
            self.assertEqual(distillation["evidence_refs"][0]["timestamp"], "00:00:10")
            self.assertEqual(
                result.failure_reason,
                "insufficient evidence: newapi API key is not configured",
            )

    def test_run_video_pipeline_writes_llm_judge_score(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Skill Demo", "duration": 120}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
                vision_client=FakeVisionClient(),
                distiller_client=FakeDistillerClient(),
                judge_client=FakeJudgeClient(),
            )

            score = read_json(store.run_path(result.run_id) / "score.json")
            self.assertEqual(score["rule_score"], 90)
            self.assertEqual(score["llm_judge_score"], 86)
            self.assertEqual(score["final_score"], 86)
            self.assertEqual(score["final_status"], "passed")
            self.assertEqual(result.status, "completed")
            self.assertIsNone(result.failure_reason)

    def test_run_video_pipeline_writes_successful_candidate_package_after_judge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Skill Demo", "duration": 120}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
                vision_client=FakeVisionClient(),
                distiller_client=FakeDistillerClient(),
                judge_client=FakeJudgeClient(score=86),
            )

            run_dir = store.run_path(result.run_id)
            score = read_json(run_dir / "score.json")
            metadata = read_json(run_dir / "metadata.json")
            package_path = Path(result.artifacts["package"])
            self.assertEqual(result.status, "completed")
            self.assertEqual(score["llm_judge_score"], 86)
            self.assertEqual(score["rule"]["dimensions"]["boundary"], 10)
            self.assertEqual(score["rule"]["dimensions"]["test"], 10)
            self.assertEqual(score["final_status"], "passed")
            self.assertEqual(metadata["package_status"], "passed")
            self.assertTrue((package_path / "SKILL.md").exists())
            self.assertTrue((package_path / "README.md").exists())
            self.assertTrue((package_path / "metadata.json").exists())
            self.assertEqual(read_json(package_path / "metadata.json")["package_status"], "passed")

    def test_run_video_pipeline_can_package_candidate_with_judge_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)
            judge = FakeJudgeClient(score=10)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Skill Demo", "duration": 120}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
                vision_client=FakeVisionClient(),
                distiller_client=FakeDistillerClient(),
                judge_client=judge,
                judge_difficulty="off",
            )

            run_dir = store.run_path(result.run_id)
            score = read_json(run_dir / "score.json")
            package_path = Path(result.artifacts["package"])
            self.assertEqual(result.status, "completed")
            self.assertEqual(judge.calls, 0)
            self.assertEqual(score["judge"]["status"], "disabled")
            self.assertEqual(score["final_status"], "needs_review")
            self.assertEqual(score["conflict_policy"], "judge_disabled")
            self.assertTrue((package_path / "SKILL.md").exists())

    def test_run_video_pipeline_records_judge_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Skill Demo", "duration": 120}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
                vision_client=FakeVisionClient(),
                distiller_client=FakeDistillerClient(),
                judge_client=FakeJudgeClient(
                    error=NewApiError(
                        "judge failed https://example.test/tmp token=x",
                        code="judge_failed",
                        status_code=502,
                    )
                ),
            )

            run_dir = store.run_path(result.run_id)
            score = read_json(run_dir / "score.json")
            saved_manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(result.status, "failed")
            self.assertEqual(score["judge"]["status"], "failed")
            self.assertEqual(score["judge"]["reason_code"], "judge_failed")
            self.assertEqual(score["judge"]["returncode"], 502)
            self.assertIn("[redacted-url]", score["judge"]["summary"])
            self.assertNotIn("token=x", score["judge"]["summary"])
            self.assertIn("judge_failed", saved_manifest["risk_flags"])

    def test_run_video_pipeline_records_distillation_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Skill Demo", "duration": 120}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
                vision_client=FakeVisionClient(),
                distiller_client=FakeDistillerClient(
                    NewApiError(
                        "distillation failed https://example.test/tmp token=x",
                        code="distillation_failed",
                        status_code=500,
                    )
                ),
            )

            run_dir = store.run_path(result.run_id)
            distillation = read_json(run_dir / "distillation.json")
            saved_manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(distillation["status"], "failed")
            self.assertEqual(distillation["reason_code"], "distillation_failed")
            self.assertEqual(distillation["returncode"], 500)
            self.assertIn("[redacted-url]", distillation["summary"])
            self.assertNotIn("token=x", distillation["summary"])
            self.assertIn("distillation_failed", saved_manifest["risk_flags"])

    def test_distillation_model_failure_is_exposed_in_run_failure_reason(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source("https://www.bilibili.com/video/BV1xx411c7mD/")
            state = store.start_or_resume(source.source, source.source_id)
            manifest = build_initial_manifest("https://www.bilibili.com/video/BV1xx411c7mD/", source)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Skill Demo", "duration": 120}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
                vision_client=FakeVisionClient(),
                distiller_client=FakeDistillerClient(NewApiError("unknown model", code="model_error", status_code=404)),
            )

            self.assertEqual(result.status, "failed")
            self.assertIn("distillation failed: model_error", result.failure_reason or "")
            self.assertIn("model:", result.failure_reason or "")
            self.assertTrue((store.run_path(result.run_id) / "failure_report.md").exists())

    def test_run_video_pipeline_retries_recoverable_distillation_and_audits_attempts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)
            distiller = RetryingDistillerClient()

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Skill Demo", "duration": 120}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
                vision_client=FakeVisionClient(),
                distiller_client=distiller,
                judge_client=FakeJudgeClient(),
            )

            run_dir = store.run_path(result.run_id)
            audit = read_json(run_dir / "model_audit.json")
            self.assertEqual(result.status, "completed")
            self.assertEqual(distiller.calls, 3)
            self.assertEqual(audit["distillation"]["attempt_count"], 2)
            self.assertEqual(audit["distillation"]["attempts"][0]["reason_code"], "invalid_distillation_json")
            self.assertIn("[redacted-url]", audit["distillation"]["attempts"][0]["summary"])
            self.assertNotIn("token=secret", json.dumps(audit))

    def test_run_video_pipeline_does_not_overwrite_existing_distillation_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)
            run_dir = store.run_path(state.run_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            write_json(
                run_dir / "distillation.json",
                {
                    "status": "distilled",
                    "model": "previous-distiller",
                    "candidate_title": "Previous candidate",
                    "ria": {},
                },
            )
            distiller_client = FakeDistillerClient()

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Skill Demo", "duration": 120}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
                vision_client=FakeVisionClient(),
                distiller_client=distiller_client,
            )

            distillation = read_json(store.run_path(result.run_id) / "distillation.json")
            self.assertEqual(distillation["model"], "previous-distiller")
            self.assertEqual(distiller_client.calls, 0)

    def test_run_video_pipeline_writes_failure_audit_chain(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe(
                    {
                        "title": "Skill Demo",
                        "uploader": "Teacher",
                        "duration": 120,
                        "subtitles": {"zh-CN": [{"url": "https://example.test/subtitle.json"}]},
                    }
                ),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
            )

            run_dir = store.run_path(result.run_id)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.completed_stages, PIPELINE_STAGES)
            saved_manifest = read_json(run_dir / "manifest.json")
            metadata = read_json(run_dir / "metadata.json")
            media_probe = read_json(run_dir / "media_probe.json")
            media_extract = read_json(run_dir / "media_extract.json")
            audio = read_json(run_dir / "audio.json")
            frame_extract = read_json(run_dir / "frame_extract.json")
            frame_index = read_json(run_dir / "frame_index.json")
            asr = read_json(run_dir / "asr.json")
            timeline = read_json(run_dir / "evidence_timeline.json")
            self.assertEqual(saved_manifest["title"], "Skill Demo")
            self.assertEqual(saved_manifest["duration_sec"], 120)
            self.assertEqual(metadata["title"], "Skill Demo")
            self.assertEqual(metadata["author"], "Teacher")
            self.assertEqual(media_probe["status"], "metadata_available")
            self.assertEqual(media_extract["status"], "downloaded")
            self.assertIn("media", media_extract["target_dir"])
            self.assertIn("BV1xx411c7mD.mp4", media_extract["media_files"][0])
            self.assertEqual(audio["status"], "extracted")
            self.assertEqual(frame_extract["status"], "extracted")
            self.assertEqual(frame_extract["frame_count"], 2)
            self.assertEqual(frame_index[1]["timestamp"], "00:00:10")
            self.assertEqual(asr["status"], "transcribed")
            self.assertIn("metadata_title", [item["type"] for item in timeline["items"]])
            self.assertIn(
                {"timestamp": "00:00:10", "type": "asr", "claim": "first step", "raw_excerpt": "first step", "confidence": 0.7},
                timeline["items"],
            )
            self.assertTrue((run_dir / "media_extract.json").exists())
            self.assertTrue((run_dir / "frame_index.json").exists())
            self.assertTrue((run_dir / "asr.json").exists())
            self.assertTrue((run_dir / "vision_ocr.json").exists())
            self.assertTrue((run_dir / "evidence_timeline.json").exists())
            self.assertTrue((run_dir / "distillation.json").exists())
            self.assertTrue((run_dir / "score.json").exists())
            self.assertTrue((run_dir / "metadata.json").exists())
            self.assertTrue((run_dir / "failure_report.md").exists())

    def test_run_video_pipeline_records_metadata_probe_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe(
                    error=YtDlpError(
                        "failed https://example.test/tmp cookie=session",
                        code="metadata_probe_failed",
                        returncode=1,
                    )
                ),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
            )

            run_dir = store.run_path(result.run_id)
            saved_manifest = read_json(run_dir / "manifest.json")
            media_probe = read_json(run_dir / "media_probe.json")
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.failure_reason, "metadata probe failed: metadata_probe_failed")
            self.assertIn("metadata_probe_failed", saved_manifest["risk_flags"])
            self.assertEqual(media_probe["status"], "failed")
            self.assertEqual(media_probe["returncode"], 1)
            self.assertIn("[redacted-url]", media_probe["summary"])
            self.assertNotIn("https://example.test/tmp", media_probe["summary"])
            self.assertNotIn("cookie=session", media_probe["summary"])

    def test_run_video_pipeline_records_media_download_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe(
                    {
                        "title": "Skill Demo",
                        "uploader": "Teacher",
                        "duration": 120,
                    }
                ),
                media_downloader=FakeMediaDownloader(
                    error=YtDlpError(
                        "download failed https://example.test/tmp token=x",
                        code="media_download_failed",
                        returncode=2,
                    )
                ),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
            )

            run_dir = store.run_path(result.run_id)
            saved_manifest = read_json(run_dir / "manifest.json")
            media_extract = read_json(run_dir / "media_extract.json")
            self.assertEqual(result.failure_reason, "media extraction failed: media_download_failed")
            self.assertIn("media_download_failed", saved_manifest["risk_flags"])
            self.assertEqual(media_extract["status"], "failed")
            self.assertEqual(media_extract["returncode"], 2)
            self.assertIn("[redacted-url]", media_extract["summary"])
            self.assertNotIn("token=x", media_extract["summary"])

    def test_run_video_pipeline_downloads_with_enriched_manifest_on_resume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            manifest.title = "Already fetched"
            manifest.risk_flags = []
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)
            store.run_path(state.run_id).mkdir(parents=True, exist_ok=True)
            write_json(
                store.run_path(state.run_id) / "media_extract.json",
                {"status": "skipped", "reason": "old placeholder"},
            )

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Should not be needed"}),
                media_downloader=FakeMediaDownloader(
                    {
                        "status": "downloaded",
                        "target_dir": "ignored",
                        "output_template": "%(id)s.%(ext)s",
                        "media_files": ["safe.mp4"],
                        "stderr": "must not be persisted",
                        "returncode": 0,
                    }
                ),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
            )

            run_dir = store.run_path(result.run_id)
            media_probe = read_json(run_dir / "media_probe.json")
            media_extract = read_json(run_dir / "media_extract.json")
            self.assertEqual(media_probe["status"], "metadata_available")
            self.assertEqual(media_extract["status"], "downloaded")
            self.assertEqual(media_extract["media_files"], ["safe.mp4"])
            self.assertNotIn("stderr", media_extract)

    def test_run_video_pipeline_records_audio_extract_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Skill Demo"}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(
                    audio_error=FfmpegError(
                        "ffmpeg failed https://example.test/tmp token=x",
                        code="audio_extract_failed",
                        returncode=3,
                    )
                ),
                asr_client=FakeAsrClient(),
            )

            run_dir = store.run_path(result.run_id)
            audio = read_json(run_dir / "audio.json")
            frame_extract = read_json(run_dir / "frame_extract.json")
            self.assertEqual(audio["status"], "failed")
            self.assertEqual(frame_extract["status"], "failed")
            self.assertEqual(frame_extract["reason_code"], "audio_extract_failed")
            self.assertNotIn("token=x", frame_extract["summary"])

    def test_run_video_pipeline_records_asr_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Skill Demo"}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(
                    NewApiError(
                        "newapi failed https://example.test/tmp token=x",
                        code="transcription_failed",
                        status_code=500,
                    )
                ),
            )

            run_dir = store.run_path(result.run_id)
            saved_manifest = read_json(run_dir / "manifest.json")
            asr = read_json(run_dir / "asr.json")
            self.assertEqual(asr["status"], "failed")
            self.assertEqual(asr["reason_code"], "transcription_failed")
            self.assertEqual(asr["returncode"], 500)
            self.assertIn("asr_failed", saved_manifest["risk_flags"])
            self.assertNotIn("token=x", asr["summary"])

    def test_resolve_media_file_accepts_cwd_relative_download_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "runs" / "demo" / "media"
            media_dir.mkdir(parents=True)
            media_file = media_dir / "BV1xx411c7mD.mp4"
            media_file.write_text("placeholder", encoding="utf-8")
            raw_path = media_file.relative_to(root)
            original_cwd = Path.cwd()

            try:
                os.chdir(root)
                resolved = resolve_media_file(
                    raw_path,
                    {"target_dir": str(media_dir.relative_to(root))},
                    root / "runs" / "demo",
                )
            finally:
                os.chdir(original_cwd)

            self.assertEqual(resolved, raw_path)

    def test_run_video_pipeline_records_stage_timings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Skill Demo", "duration": 120}),
                media_downloader=FakeMediaDownloader(),
                media_processor=FakeMediaProcessor(),
                asr_client=FakeAsrClient(),
                vision_client=FakeVisionClient(),
                distiller_client=FakeDistillerClient(),
                judge_client=FakeJudgeClient(),
            )

            timings_path = store.run_path(result.run_id) / "stage_timings.json"
            timings = read_json(timings_path)
            self.assertEqual([item["stage"] for item in timings["stages"]], PIPELINE_STAGES)
            self.assertGreaterEqual(timings["total_duration_ms"], 0)
            self.assertEqual(result.artifacts["stage_timings"], str(timings_path))
            for item in timings["stages"]:
                self.assertEqual(item["status"], "completed")
                self.assertIn("started_at", item)
                self.assertIn("finished_at", item)
                self.assertGreaterEqual(item["duration_ms"], 0)

    def test_run_video_pipeline_rejects_parameter_changes_when_resuming_completed_stages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            state = store.start_or_resume("bilibili", "BV1xx411c7mD")
            state.completed_stages = ["manifest"]
            state.judge_difficulty = "standard"
            state.vision_mode = "full"
            state.vision_frame_limit = 12
            store.save(state)
            manifest = build_initial_manifest(
                "https://www.bilibili.com/video/BV1xx411c7mD/",
                infer_source("https://www.bilibili.com/video/BV1xx411c7mD/"),
            )

            with self.assertRaisesRegex(ValueError, "run-variant"):
                run_video_pipeline(
                    config=config,
                    store=store,
                    state=state,
                    manifest=manifest,
                    out_dir=Path(temp_dir) / "skills",
                    judge_difficulty="strict",
                )


if __name__ == "__main__":
    unittest.main()
