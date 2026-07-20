import tempfile
import unittest
from pathlib import Path

from skill_gather.models import EvidenceTimeline, VideoSourceManifest
from skill_gather.runs import RunStore


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


if __name__ == "__main__":
    unittest.main()
