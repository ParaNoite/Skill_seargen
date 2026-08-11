from __future__ import annotations

import tempfile
import unittest

from skill_gather.automation import choose_auto_sources, evaluate_release_gate
from skill_gather.models import TopicSourceCandidate
from skill_gather.topics import TopicRunStore


class AutomationTests(unittest.TestCase):
    def test_auto_selection_prefers_diverse_high_quality_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            task = TopicRunStore(directory).start_or_resume("Godot NavigationAgent3D", mode="technical", execution_mode="auto")
            task.candidates = [
                TopicSourceCandidate(url="https://docs.example.test", candidate_id="web", source_type="web", quality_score=90),
                TopicSourceCandidate(url="https://github.com/example/repo", candidate_id="repo", source_type="github", quality_score=85),
                TopicSourceCandidate(url="https://www.bilibili.com/video/BVdemo", candidate_id="video", source_type="video", quality_score=80),
                TopicSourceCandidate(url="https://spam.example.test", candidate_id="risky", source_type="web", quality_score=99, risk_flags=["spam"]),
            ]
            self.assertEqual(choose_auto_sources(task)[:3], ["web", "repo", "video"])

    def test_release_gate_downgrades_single_source_conflicts_and_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            task = TopicRunStore(directory).start_or_resume("Godot NavigationAgent3D", execution_mode="auto")
            task.selected_sources = [TopicSourceCandidate(url="https://www.bilibili.com/video/BVdemo", candidate_id="only", source_type="video")]
            task.usage.model_calls = task.budget.max_model_calls + 1
            decision = evaluate_release_gate(task, {"conflicts": [{"claim": "x"}]}, {"final_score": 90, "final_status": "passed"})
            self.assertEqual(decision.status, "needs_review")
            self.assertIn("缺少交叉来源", decision.reasons)
            self.assertIn("存在来源冲突", decision.reasons)
            self.assertIn("预算超限", decision.reasons)


if __name__ == "__main__":
    unittest.main()
