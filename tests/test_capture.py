import tempfile
import unittest
from pathlib import Path

from skill_gather.capture import (
    CaptureIndex,
    contains_sensitive,
    load_capture_index,
    redact_capture_metadata,
    register_capture,
    render_offline_showcase,
    select_showcase_frames,
)


class CaptureTests(unittest.TestCase):
    def _artifact(self, root: Path, relative: str, content: str = "证据产物") -> str:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return relative

    def _register(self, root: Path, *, event: str = "source_selection", stage: str = "search", score: int = 90):
        screenshot = self._artifact(root, f"captures/theme-001/{stage}/screen.png", "png-placeholder")
        trace = self._artifact(root, f"captures/theme-001/{stage}/flow.trace.zip", "trace-placeholder")
        return register_capture(
            root,
            supervision_id="demo",
            theme_id="theme-001",
            topic="AI Agent 学习系统",
            stage=stage,
            event=event,
            screenshot_path=screenshot,
            trace_path=trace,
            page_url="https://example.test/research?token=hidden",
            narrative_beats=[stage],
            showcase_reason="对应 TED 演讲节点",
            narrative_score=min(40, score),
            information_score=min(25, max(0, score - 40)),
            visual_score=min(20, max(0, score - 65)),
            evidence_score=min(15, max(0, score - 85)),
            artifact_paths=[self._artifact(root, "topic_package/COURSE.md", "# 课程\n内容")],
        )

    def test_registers_relative_paths_trace_and_artifact_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = self._register(root)

            self.assertEqual(record.trace_path, "captures/theme-001/search/flow.trace.zip")
            self.assertEqual(record.page_url, "https://example.test/research")
            self.assertTrue(record.artifact_preview_path.endswith("artifact-preview.html"))
            self.assertTrue((root / record.artifact_preview_path).is_file())
            self.assertEqual(load_capture_index(root).records[0].capture_id, record.capture_id)

    def test_rejects_paths_outside_supervision_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                register_capture(
                    root,
                    supervision_id="demo",
                    theme_id="theme-001",
                    topic="主题",
                    stage="search",
                    event="source_selection",
                    screenshot_path="../outside.png",
                )

    def test_normal_theme_does_not_select_showcase_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._register(root)

            index = select_showcase_frames(root, ted_relevance_score=69, threshold=70)

            self.assertFalse(any(record.selected_for_showcase for record in index.records))

    def test_low_scoring_frame_does_not_enter_showcase(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._register(root, score=69)

            index = select_showcase_frames(root, ted_relevance_score=90, threshold=70)

            self.assertFalse(any(record.selected_for_showcase for record in index.records))

    def test_selection_caps_at_five_and_deduplicates_same_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number in range(7):
                self._register(root, stage=f"stage-{number}", score=100 - number)
            duplicate = self._register(root, stage="stage-0", score=100)

            index = select_showcase_frames(root, ted_relevance_score=90, threshold=70, max_frames=5, theme_id="theme-001")

            self.assertEqual(len(index.records), 7)
            self.assertEqual(duplicate.capture_id, "capture-0001")
            self.assertEqual(sum(record.selected_for_showcase for record in index.records), 5)

    def test_failure_events_never_enter_markdown_or_html_showcase(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._register(root, event="bug_reproduction", stage="broken", score=100)
            self._register(root, event="source_selection", stage="fixed", score=90)
            index = select_showcase_frames(root, ted_relevance_score=90, threshold=70)
            markdown, html = render_offline_showcase(root, index)

            self.assertFalse(index.records[0].selected_for_showcase)
            self.assertNotIn("bug_reproduction", markdown.read_text(encoding="utf-8"))
            self.assertNotIn("bug_reproduction", html.read_text(encoding="utf-8"))

    def test_strict_redaction_blocks_secret_page_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            screenshot = self._artifact(root, "captures/theme-001/search/screen.png")
            trace = self._artifact(root, "captures/theme-001/search/flow.trace.zip")
            record = register_capture(
                root,
                supervision_id="demo",
                theme_id="theme-001",
                topic="主题",
                stage="search",
                event="source_selection",
                screenshot_path=screenshot,
                trace_path=trace,
                page_text="Authorization: Bearer secret-token-value",
            )

            self.assertTrue(record.redaction_blocked)
            self.assertTrue(contains_sensitive("api_key=abc"))
            self.assertIn("[已隐藏]", redact_capture_metadata("https://example.test/?token=abc"))

    def test_offline_html_uses_relative_paths_and_no_script(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._register(root, stage="result", score=90)
            index = select_showcase_frames(root, ted_relevance_score=90, threshold=70)
            _, output = render_offline_showcase(root, index)
            content = output.read_text(encoding="utf-8")

            self.assertIn('src="captures/theme-001/result/screen.png"', content)
            self.assertNotIn("<script", content)
            self.assertNotIn("?token=", content)

    def test_empty_index_can_render_offline_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            markdown, html = render_offline_showcase(root, CaptureIndex(supervision_id="empty"))

            self.assertTrue(markdown.is_file())
            self.assertTrue(html.is_file())
