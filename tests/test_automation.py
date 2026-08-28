from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skill_gather.automation import choose_auto_sources, evaluate_release_gate, persist_video_release_gate
from skill_gather.runs import read_json
from skill_gather.models import TopicSourceCandidate
from skill_gather.topics import TopicRunStore


class AutomationTests(unittest.TestCase):
    def test_single_video_release_gate_downgrades_passed_score(self):
        with tempfile.TemporaryDirectory() as directory:
            decision = persist_video_release_gate(Path(directory), {"final_score": 90, "final_status": "passed"})
            self.assertEqual(decision.status, "needs_review")
            self.assertEqual(read_json(Path(directory) / "score.json")["final_status"], "needs_review")

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

    def test_auto_selection_keeps_relevant_cautionary_source_and_rejects_topic_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            task = TopicRunStore(directory).start_or_resume("HTML Canvas game", mode="technical", execution_mode="auto")
            task.candidates = [
                TopicSourceCandidate(
                    url="https://www.bilibili.com/video/BVgame",
                    candidate_id="relevant-video",
                    source_type="video",
                    quality_score=68,
                    risk_flags=["video source", "no summary", "features unverified"],
                ),
                TopicSourceCandidate(
                    url="https://github.com/example/html2canvas",
                    candidate_id="wrong-repo",
                    source_type="github",
                    quality_score=70,
                    risk_flags=["topic mismatch", "not game-focused"],
                ),
                TopicSourceCandidate(
                    url="https://developer.mozilla.org/canvas",
                    candidate_id="docs",
                    source_type="web",
                    quality_score=65,
                ),
            ]

            selected = choose_auto_sources(task)

            self.assertEqual(selected, ["docs", "relevant-video"])
            self.assertNotIn("wrong-repo", selected)

    def test_auto_selection_skips_broad_webgl_repos_on_complex_browser_verification_topic(self):
        with tempfile.TemporaryDirectory() as directory:
            task = TopicRunStore(directory).start_or_resume(
                "Playwright 浏览器验收：Canvas WebGL 交互、移动视口、Trace 与产物复核",
                mode="technical",
                execution_mode="auto",
            )
            task.candidates = [
                TopicSourceCandidate(
                    url="https://github.com/managedcode/playwright_stealth",
                    candidate_id="stealth",
                    source_type="github",
                    quality_score=95,
                    matched_facets=["Playwright", "Canvas", "WebGL"],
                    risk_flags=["主题偏向隐身规避"],
                ),
                TopicSourceCandidate(
                    url="https://cloud.tencent.com/developer/article/2541463",
                    candidate_id="docs",
                    source_type="web",
                    quality_score=72,
                    matched_facets=["Playwright", "浏览器验收", "Trace"],
                ),
                TopicSourceCandidate(
                    url="https://www.bilibili.com/video/BV1oNR7B5ECs/",
                    candidate_id="video",
                    source_type="video",
                    quality_score=68,
                    matched_facets=["Playwright", "浏览器验收"],
                ),
                TopicSourceCandidate(
                    url="https://github.com/regl-project/regl",
                    candidate_id="generic-webgl",
                    source_type="github",
                    quality_score=100,
                    matched_facets=["WebGL"],
                ),
                TopicSourceCandidate(
                    url="https://github.com/jagenjo/webglstudio.js",
                    candidate_id="not-testing-material",
                    source_type="github",
                    quality_score=90,
                    matched_facets=["WebGL", "交互"],
                    risk_flags=["缺少Playwright", "并非测试资料"],
                ),
            ]

            selected = choose_auto_sources(task)

            self.assertEqual(selected, ["docs", "video"])
            self.assertNotIn("stealth", selected)
            self.assertNotIn("generic-webgl", selected)
            self.assertNotIn("not-testing-material", selected)

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
