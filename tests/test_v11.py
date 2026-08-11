from __future__ import annotations

import tempfile
import unittest

from skill_gather.automation import choose_auto_sources, evaluate_release_gate
from skill_gather.models import TopicSourceCandidate
from skill_gather.planning import assess_ambiguity
from skill_gather.topics import TopicRunStore


class V11PlanningTests(unittest.TestCase):
    def test_ambiguous_topic_waits_for_semantic_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TopicRunStore(directory)
            task = store.start_or_resume("教程")
            planned = store.create_plan(task.run_id)

            self.assertTrue(assess_ambiguity("教程").ambiguous)
            self.assertEqual(planned.status, "awaiting_plan_confirmation")
            self.assertGreaterEqual(len(planned.plan.options), 2)

            confirmed = store.confirm_plan(task.run_id, planned.plan.recommended_option_id)
            self.assertEqual(confirmed.status, "created")
            self.assertEqual(confirmed.plan.audit_status, "confirmed")

    def test_plan_interruption_persists_conservative_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TopicRunStore(directory)
            task = store.start_or_resume("AI", execution_mode="auto")
            interrupted = store.interrupt_plan(task.run_id)

            self.assertEqual(interrupted.plan.warning, "plan_interrupted")
            self.assertEqual(interrupted.plan.generation_method, "deterministic")
            self.assertEqual(interrupted.plan_audit[-1]["event"], "plan_interrupted")


class V11AutomationTests(unittest.TestCase):
    def test_auto_selection_prefers_diverse_high_quality_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TopicRunStore(directory)
            task = store.start_or_resume("Godot NavigationAgent3D", mode="technical", execution_mode="auto")
            task.candidates = [
                TopicSourceCandidate(url="https://docs.example.test", candidate_id="web", source_type="web", quality_score=90),
                TopicSourceCandidate(url="https://github.com/example/repo", candidate_id="repo", source_type="github", quality_score=85),
                TopicSourceCandidate(url="https://www.bilibili.com/video/BVdemo", candidate_id="video", source_type="video", quality_score=80),
                TopicSourceCandidate(url="https://spam.example.test", candidate_id="risky", source_type="web", quality_score=99, risk_flags=["spam"]),
            ]
            self.assertEqual(choose_auto_sources(task)[:3], ["web", "repo", "video"])

    def test_release_gate_downgrades_single_source_and_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TopicRunStore(directory)
            task = store.start_or_resume("Godot NavigationAgent3D", execution_mode="auto")
            task.selected_sources = [TopicSourceCandidate(url="https://www.bilibili.com/video/BVdemo", candidate_id="only", source_type="video")]
            decision = evaluate_release_gate(task, {"conflicts": [{"claim": "x"}]}, {"final_score": 90, "final_status": "passed"})
            self.assertEqual(decision.status, "needs_review")
            self.assertIn("缺少交叉来源", decision.reasons)
            self.assertIn("存在来源冲突", decision.reasons)


if __name__ == "__main__":
    unittest.main()
