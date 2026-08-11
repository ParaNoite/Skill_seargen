import tempfile
import unittest
from pathlib import Path

from skill_gather.models import EvidenceTimeline, TopicTask, TopicUsage, VideoSourceManifest
from skill_gather.runs import RunStore
from skill_gather.topics import TopicRunStore


class RunStoreTests(unittest.TestCase):
    def test_start_or_resume_reuses_source_id_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RunStore(temp_dir)

            first = store.start_or_resume("bilibili", "BV1xx411c7mD")
            second = store.start_or_resume("bilibili", "BV1xx411c7mD")

            self.assertEqual(first.run_id, second.run_id)
            self.assertTrue((Path(temp_dir) / first.run_id / "run_state.json").exists())

    def test_writes_manifest_timeline_and_failure_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RunStore(temp_dir)
            state = store.start_or_resume("bilibili", "BV1xx411c7mD")
            manifest = VideoSourceManifest(
                source="bilibili",
                source_id="BV1xx411c7mD",
                url="https://www.bilibili.com/video/BV1xx411c7mD/",
            )
            timeline = EvidenceTimeline(
                video_duration_sec=60,
                frame_budget=10,
                sampling_strategy="adaptive",
            )

            manifest_path = store.save_manifest(state.run_id, manifest)
            timeline_path = store.save_evidence_timeline(state.run_id, timeline)
            report_path = store.save_failure_report(
                state.run_id,
                ["# Failure Report", "", "insufficient evidence"],
            )

            self.assertTrue(manifest_path.exists())
            self.assertTrue(timeline_path.exists())
            self.assertTrue(report_path.exists())

    def test_topic_run_persists_progress_failure_and_package_layout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(temp_dir)
            task = store.start_or_resume("Godot 导航", mode="technical")

            self.assertTrue((store.run_path(task.run_id) / "topic_package" / "sources.json").exists())
            self.assertTrue((store.run_path(task.run_id) / "topic_package" / "references").is_dir())

            searching = store.advance(task.run_id, "searching")
            failed = store.fail(task.run_id, "预算已耗尽")
            restored = store.load(task.run_id)
            resumed = store.resume(task.run_id)

            self.assertEqual(searching.status, "searching")
            self.assertEqual(failed.status, "failed")
            self.assertEqual(restored.failure_reason, "预算已耗尽")
            self.assertEqual(restored.failure_stage, "searching")
            self.assertEqual(resumed.status, "searching")
            self.assertEqual(resumed.failure_reason, "预算已耗尽")
            self.assertIsInstance(restored, TopicTask)

    def test_search_can_retry_after_resume_from_searching(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(temp_dir)
            task = store.start_or_resume("重试搜索")
            store.advance(task.run_id, "searching")
            store.fail(task.run_id, "provider timeout")
            resumed = store.resume(task.run_id)
            retried = store.begin_search(resumed.run_id)
            self.assertEqual(retried.status, "searching")

    def test_topic_run_distinguishes_modes_for_the_same_topic(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(temp_dir)

            normal = store.start_or_resume("Godot 导航", mode="normal")
            technical = store.start_or_resume("Godot 导航", mode="technical")

            self.assertNotEqual(normal.run_id, technical.run_id)

    def test_completed_topic_can_rerun_from_generation_with_audit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(temp_dir)
            task = store.start_or_resume("Godot 导航", mode="technical")
            task.status = "completed"
            task.current_stage = "completed"
            store.save(task)

            rerun = store.rerun(task.run_id, "generating")

            self.assertEqual(rerun.status, "generating")
            self.assertEqual(rerun.artifacts["rerun_audit"], "rerun_audit.json")
            self.assertTrue((store.run_path(task.run_id) / "rerun_audit.json").exists())

    def test_topic_run_fails_with_an_explanation_when_usage_exceeds_budget(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(temp_dir)
            task = store.start_or_resume("Godot 导航")

            exceeded = store.record_usage(task.run_id, TopicUsage(candidate_count=21))

            self.assertEqual(exceeded.status, "failed")
            self.assertEqual(exceeded.failure_stage, "created")
            self.assertIn("candidate_count", exceeded.failure_reason)


if __name__ == "__main__":
    unittest.main()
