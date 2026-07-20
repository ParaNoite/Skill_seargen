import json
import tempfile
import unittest
from pathlib import Path

from skill_gather.models import (
    EvidenceItem,
    EvidenceTimeline,
    FrameManifest,
    RunState,
    SkillPackageMetadata,
)
from skill_gather.packaging import write_audit_package


class PackagingTests(unittest.TestCase):
    def test_write_audit_package_outputs_structured_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            metadata = SkillPackageMetadata(
                source="bilibili",
                source_id="BV1xx411c7mD",
                source_url="https://www.bilibili.com/video/BV1xx411c7mD/",
                package_status="failed",
                risk_flags=["insufficient_evidence"],
            )
            run_state = RunState(
                run_id="bilibili-BV1xx411c7mD",
                source_id="BV1xx411c7mD",
                status="failed",
                failure_reason="insufficient evidence",
            )
            timeline = EvidenceTimeline(
                video_duration_sec=120,
                frame_budget=20,
                sampling_strategy="adaptive",
                items=[EvidenceItem(timestamp="00:00:01", type="asr", claim="intro")],
            )
            frames = [
                FrameManifest(
                    timestamp="00:00:01",
                    frame_path="runs/demo/frame-001.jpg",
                    reason="opening_frame",
                    visual_type="title",
                )
            ]

            target = write_audit_package(
                Path(temp_dir) / "audit",
                metadata,
                run_state=run_state,
                evidence_timeline=timeline,
                frame_index=frames,
                failure_reason="insufficient evidence",
            )

            self.assertTrue((target / "metadata.json").exists())
            self.assertTrue((target / "run_state.json").exists())
            self.assertTrue((target / "evidence_timeline.json").exists())
            self.assertTrue((target / "frame_index.json").exists())
            self.assertTrue((target / "failure_report.md").exists())
            metadata_payload = json.loads((target / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata_payload["package_status"], "failed")


if __name__ == "__main__":
    unittest.main()
