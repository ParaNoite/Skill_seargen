from pathlib import Path
import re
import unittest


class WebAssetRegressionTests(unittest.TestCase):
    def test_video_polling_only_refreshes_selected_active_run_and_serializes_requests(self):
        asset = _read_app_js()

        self.assertIn('const RUN_POLL_STATUSES = new Set(["created", "running"]);', asset)
        self.assertRegex(asset, re.compile(r"if \(!state\.selectedId\) return;"))
        self.assertRegex(asset, re.compile(r"if \(runPollState\.inFlight\) return;"))
        self.assertRegex(asset, re.compile(r"const current = state\.runs\.find\(run => run\.run_id === state\.selectedId\);"))
        self.assertRegex(asset, re.compile(r"if \(!current \|\| !RUN_POLL_STATUSES\.has\(current\.status\)\) return;"))

    def test_topic_polling_only_refreshes_active_topics_and_serializes_requests(self):
        asset = _read_app_js()

        self.assertIn(
            'const TOPIC_POLL_STATUSES = new Set(["processing_sources", "generating", "scoring"]);',
            asset,
        )
        self.assertRegex(asset, re.compile(r"if \(!current \|\| !TOPIC_POLL_STATUSES\.has\(current\.status\)\) return;"))
        self.assertRegex(asset, re.compile(r"if \(topicPollState\.inFlight\) return;"))

    def test_topic_detail_shows_budget_and_cache_guidance(self):
        asset = _read_app_js()

        self.assertIn("预算：候选", asset)
        self.assertIn("缓存", asset)


def _read_app_js() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "src"
        / "skill_gather"
        / "web_assets"
        / "app.js"
    ).read_text(encoding="utf-8")
