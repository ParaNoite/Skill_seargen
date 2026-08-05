import tempfile
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from skill_gather.config import parse_config
from skill_gather.cli import main
from skill_gather.models import EvidenceItem, EvidenceTimeline, TopicSourceCandidate, TopicVideoRun, VideoSourceManifest
from skill_gather.runs import RunStore, write_json
from skill_gather.topic_videos import process_topic_videos
from skill_gather.topics import TopicRunStore


CONFIG = {
    "providers": {
        "newapi": {
            "base_url": "https://api.example.test/v1",
            "api_key_env": "TOPIC_VIDEO_TEST_KEY",
            "vision_model": "vision",
            "asr_model": "faster-whisper:base",
            "distiller_model": "distiller",
            "judge_model": "judge",
        }
    },
    "defaults": {"provider": "newapi", "output_dir": "./skills", "run_dir": "./runs"},
}


class TopicVideoTests(unittest.TestCase):
    def test_store_normalizes_optional_knowledge_and_hydrates_legacy_vision_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("历史主题", mode="normal")
            task.package.knowledge = "topic_package/knowledge.md"
            task.video_runs = [
                TopicVideoRun(
                    parent_run_id=task.run_id,
                    candidate_id="cand-video",
                    child_run_id="child-video",
                    source_url="https://www.bilibili.com/video/BV1xx411c7mD/",
                    status="completed",
                )
            ]
            store.save(task)
            vision_path = store.run_path(task.run_id) / "video_runs" / "child-video" / "vision_ocr.json"
            vision_path.parent.mkdir(parents=True, exist_ok=True)
            write_json(vision_path, {"status": "skipped", "reason": "newapi API key is not configured"})

            loaded = store.load(task.run_id)
            self.assertIsNone(loaded.package.knowledge)
            self.assertEqual(loaded.video_runs[0].vision_status, "skipped")
            self.assertIn("API key", loaded.video_runs[0].vision_reason)

    def test_topic_process_cli_reports_video_evidence_completion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "config.json"
            config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
            store = TopicRunStore(root / "runs")
            task = store.start_or_resume("Godot 导航", mode="normal")
            candidate = TopicSourceCandidate(
                url="https://www.bilibili.com/video/BV1xx411c7mD/",
                candidate_id="cand-video-one",
                source_type="video",
            )
            task.candidates = [candidate]
            task.selected_sources = [candidate]
            task.status = "processing_sources"
            task.current_stage = "processing_sources"
            store.save(task)

            def fake_pipeline(*, store, state, manifest, pre_media_extract, **_kwargs):
                updated = VideoSourceManifest(
                    source=manifest.source,
                    source_id=manifest.source_id,
                    url=manifest.url,
                    title="导航教程",
                    duration_sec=60,
                )
                store.save_manifest(state.run_id, updated)
                self.assertIsNone(pre_media_extract(updated))
                store.save_evidence_timeline(
                    state.run_id,
                    EvidenceTimeline(
                        video_duration_sec=60,
                        frame_budget=2,
                        sampling_strategy="fixture",
                        items=[EvidenceItem("00:00:10", "asr", "第一步", confidence=0.7)],
                    ),
                )
                state.status = "completed"
                store.save(state)
                return state

            stdout = io.StringIO()
            with patch("skill_gather.topic_videos.run_video_pipeline", side_effect=fake_pipeline):
                exit_code = main(
                    ["topic", "process", task.run_id, "--runs", str(store.root), "--config", str(config_path)],
                    stdout=stdout,
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["successful_video_sources"], 1)
            self.assertIsNone(payload["knowledge"])

            saved = store.load(task.run_id)
            self.assertIsNone(saved.package.knowledge)

    def test_successful_and_failed_video_runs_are_isolated_and_auditable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("Godot 导航", mode="normal")
            first = TopicSourceCandidate(
                url="https://www.bilibili.com/video/BV1xx411c7mD/",
                candidate_id="cand-video-one",
                source_type="video",
            )
            second = TopicSourceCandidate(
                url="https://www.bilibili.com/video/BV1yy411c7mD/",
                candidate_id="cand-video-two",
                source_type="video",
            )
            task.candidates = [first, second]
            task.selected_sources = [first, second]

            def fake_pipeline(*, store, state, manifest, pre_media_extract, **_kwargs):
                updated = VideoSourceManifest(
                    source=manifest.source,
                    source_id=manifest.source_id,
                    url=manifest.url,
                    title=f"{manifest.source_id} 教程",
                    duration_sec=60,
                )
                store.save_manifest(state.run_id, updated)
                rejection = pre_media_extract(updated)
                if rejection:
                    state.status = "failed"
                    state.failure_reason = rejection
                    store.save(state)
                    return state
                if manifest.source_id.startswith("BV1yy"):
                    state.status = "failed"
                    state.failure_reason = "模拟下载失败"
                    store.save(state)
                    return state
                store.save_evidence_timeline(
                    state.run_id,
                    EvidenceTimeline(
                        video_duration_sec=60,
                        frame_budget=2,
                        sampling_strategy="fixture",
                        items=[EvidenceItem("00:00:10", "asr", "第一步", confidence=0.7)],
                    ),
                )
                state.status = "completed"
                store.save(state)
                return state

            with patch("skill_gather.topic_videos.run_video_pipeline", side_effect=fake_pipeline):
                result = process_topic_videos(task, store.run_path(task.run_id), parse_config(CONFIG))

            self.assertEqual(len(result.successful), 1)
            self.assertEqual(len(result.failed), 1)
            self.assertEqual(task.usage.processed_video_duration_sec, 120)
            self.assertEqual(task.video_runs[0].parent_run_id, task.run_id)
            self.assertTrue((store.run_path(task.run_id) / task.video_runs[0].evidence_path).exists())
            self.assertTrue((store.run_path(task.run_id) / "video_processing_audit.json").exists())

    def test_duration_budget_rejects_before_media_processing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("Godot 导航", mode="normal")
            task.budget.max_video_duration_sec = 30
            candidate = TopicSourceCandidate(
                url="https://www.bilibili.com/video/BV1xx411c7mD/",
                candidate_id="cand-video-one",
                source_type="video",
            )
            task.selected_sources = [candidate]

            def fake_pipeline(*, store, state, manifest, pre_media_extract, **_kwargs):
                updated = VideoSourceManifest(
                    source=manifest.source,
                    source_id=manifest.source_id,
                    url=manifest.url,
                    duration_sec=60,
                )
                store.save_manifest(state.run_id, updated)
                state.failure_reason = pre_media_extract(updated)
                state.status = "failed"
                store.save(state)
                return state

            with patch("skill_gather.topic_videos.run_video_pipeline", side_effect=fake_pipeline):
                result = process_topic_videos(task, store.run_path(task.run_id), parse_config(CONFIG))

            self.assertFalse(result.successful)
            self.assertIn("时长预算超限", result.failed[0].failure_reason)


if __name__ == "__main__":
    unittest.main()
