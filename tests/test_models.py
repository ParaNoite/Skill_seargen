import unittest

from skill_gather.models import (
    EvidenceItem,
    EvidenceTimeline,
    FrameManifest,
    ScoreResult,
    SkillPackageMetadata,
    TopicBudget,
    TopicPackage,
    TopicTask,
)


class ModelRoundTripTests(unittest.TestCase):
    def test_evidence_timeline_round_trips_items(self):
        timeline = EvidenceTimeline(
            video_duration_sec=120,
            frame_budget=20,
            sampling_strategy="adaptive_interval_plus_scene_change",
            items=[
                EvidenceItem(
                    timestamp="00:00:12",
                    type="frame_ocr",
                    claim="demo command is shown",
                    raw_excerpt="skill-gather video",
                    confidence=0.91,
                )
            ],
        )

        restored = EvidenceTimeline.from_dict(timeline.to_dict())

        self.assertEqual(restored.items[0].type, "frame_ocr")
        self.assertEqual(restored.items[0].confidence, 0.91)

    def test_frame_manifest_round_trips_sampling_reason(self):
        frame = FrameManifest(
            timestamp="00:02:30",
            frame_path="runs/demo/frame-001.jpg",
            reason="code_screen_changed",
            visual_type="ide_code",
            importance=0.8,
            ocr_density="high",
        )

        restored = FrameManifest.from_dict(frame.to_dict())

        self.assertEqual(restored.reason, "code_screen_changed")
        self.assertEqual(restored.ocr_density, "high")

    def test_skill_package_metadata_round_trips_scores_and_evidence(self):
        metadata = SkillPackageMetadata(
            source="bilibili",
            source_id="BV1xx411c7mD",
            source_url="https://www.bilibili.com/video/BV1xx411c7mD/",
            package_status="needs_review",
            models={"judge": "judge-model"},
            evidence=[
                EvidenceItem(
                    timestamp="00:00:42",
                    type="asr",
                    claim="the workflow can be turned into steps",
                    confidence=0.75,
                )
            ],
            risk_flags=["single_channel_evidence"],
            scores=ScoreResult(
                rule_score=82,
                llm_judge_score=86,
                final_score=82,
                final_status="needs_review",
            ),
        )

        restored = SkillPackageMetadata.from_dict(metadata.to_dict())

        self.assertEqual(restored.package_status, "needs_review")
        self.assertEqual(restored.scores.final_score, 82)
        self.assertEqual(restored.evidence[0].type, "asr")

    def test_topic_task_round_trips_technical_mode_and_optional_package_files(self):
        task = TopicTask(
            run_id="topic-godot-12345678",
            topic="Godot 导航",
            mode="technical",
            output_language="zh-CN",
            budget=TopicBudget(max_candidates=12, max_selected_sources=3),
            package=TopicPackage(
                root="topic_package",
                sources="topic_package/sources.json",
                evidence="topic_package/evidence",
                references="topic_package/references",
            ),
        )

        restored = TopicTask.from_dict(task.to_dict())

        self.assertEqual(restored.mode, "technical")
        self.assertEqual(restored.budget.max_candidates, 12)
        self.assertIsNone(restored.package.skill)
        self.assertEqual(restored.package.references, "topic_package/references")

    def test_topic_task_round_trips_normal_mode_without_optional_package(self):
        task = TopicTask(run_id="topic-drawing-12345678", topic="绘画入门")

        restored = TopicTask.from_dict(task.to_dict())

        self.assertEqual(restored.mode, "normal")
        self.assertIsNone(restored.package)
        self.assertEqual(restored.cache.to_dict(), {"reuse_cache": True, "refresh_cache": False})


if __name__ == "__main__":
    unittest.main()
