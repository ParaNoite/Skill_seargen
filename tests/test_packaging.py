import json
import tempfile
import unittest
from pathlib import Path

from skill_gather.models import (
    EvidenceItem,
    EvidenceTimeline,
    FrameManifest,
    RunState,
    ScoreResult,
    SkillPackageMetadata,
)
from skill_gather.packaging import write_audit_package, write_candidate_package


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

    def test_write_candidate_package_includes_human_review_summary_and_evidence_trace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            metadata = SkillPackageMetadata(
                source="bilibili",
                source_id="BV1xx411c7mD",
                source_url="https://www.bilibili.com/video/BV1xx411c7mD/",
                title="Editable Install Demo",
                author="Teacher",
                package_status="needs_review",
                risk_flags=["no_subtitle", "single_channel_evidence"],
                scores=ScoreResult(
                    rule_score=82,
                    llm_judge_score=86,
                    final_score=82,
                    final_status="needs_review",
                ),
            )
            timeline = EvidenceTimeline(
                video_duration_sec=120,
                frame_budget=20,
                sampling_strategy="ffmpeg_interval_10s",
                items=[
                    EvidenceItem(
                        timestamp="00:00:03",
                        type="asr",
                        claim="讲解 editable install 的适用场景",
                        confidence=0.76,
                    ),
                    EvidenceItem(
                        timestamp="00:00:10",
                        type="frame_ocr",
                        claim="画面展示 python -m pip install -e .",
                        confidence=0.91,
                    ),
                ],
            )
            distillation = {
                "candidate_title": "Install editable Python package",
                "summary": "A reusable local development install workflow.",
                "ria": {
                    "recall": "The video shows an editable install command.",
                    "interpret": "Use editable installs during local package development.",
                    "apply": ["Run python -m pip install -e ."],
                    "boundary": "Only for Python packages with packaging metadata.",
                    "test": ["Import the package after install."],
                },
            }

            target = write_candidate_package(
                Path(temp_dir) / "skills",
                metadata,
                distillation,
                evidence_timeline=timeline,
            )

            readme = (target / "README.md").read_text(encoding="utf-8")
            skill = (target / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("## Review Summary", readme)
            self.assertIn("- source_url: https://www.bilibili.com/video/BV1xx411c7mD/", readme)
            self.assertIn("- package_status: needs_review", readme)
            self.assertIn("- final_score: 82", readme)
            self.assertIn("- risk_flags: no_subtitle, single_channel_evidence", readme)
            self.assertIn("## Evidence Summary", readme)
            self.assertIn("00:00:10 [frame_ocr] 画面展示 python -m pip install -e .", readme)
            self.assertIn("## Review Checklist", readme)
            self.assertIn("## Evidence Trace", skill)
            self.assertIn("00:00:03 [asr] 讲解 editable install 的适用场景", skill)
            self.assertTrue((target / "metadata.json").exists())
            self.assertTrue((target / "evidence_timeline.json").exists())


if __name__ == "__main__":
    unittest.main()
