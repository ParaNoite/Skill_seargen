from pathlib import Path
import re
import unittest


class WebAssetRegressionTests(unittest.TestCase):
    def test_topic_candidates_keep_structured_workspace_layout(self):
        root = Path(__file__).resolve().parents[1]
        css = (root / "src" / "skill_gather" / "web_assets" / "app.css").read_text(encoding="utf-8")

        for selector in (".candidate-list", ".candidate-card", ".candidate-summary", ".candidate-meta", ".candidate-score", ".topic-confirmation"):
            self.assertIn(selector, css)
        self.assertRegex(css, re.compile(r"\.candidate-card\s*\{[^}]*grid-template-columns", re.DOTALL))

    def test_react_site_shell_includes_home_catalog_and_workspace(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "frontend" / "nav.jsx").read_text(encoding="utf-8")
        bundle = (root / "src" / "skill_gather" / "web_assets" / "react-nav.js")
        self.assertIn('createRoot(root).render(<SiteApp />)', source)
        self.assertIn("Search</span><span className=\"multiply\">×", source)
        self.assertIn("浏览 Skills", source)
        self.assertIn("工作台", source)
        self.assertIn("/api/catalog", source)
        self.assertTrue(bundle.exists())

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
            'const TOPIC_POLL_STATUSES = new Set(["awaiting_plan_confirmation", "processing_sources", "generating", "scoring"]);',
            asset,
        )
        self.assertRegex(asset, re.compile(r"if \(!current \|\| !TOPIC_POLL_STATUSES\.has\(current\.status\)\) return;"))
        self.assertRegex(asset, re.compile(r"if \(topicPollState\.inFlight\) return;"))

    def test_polling_does_not_replace_selected_details_with_loading_state(self):
        asset = _read_app_js()
        video_poll = re.search(r"async function poll\(\) \{(?P<body>[\s\S]*?)\n\}", asset)
        topic_poll = re.search(r"async function pollTopics\(\) \{(?P<body>[\s\S]*?)\n\}", asset)

        self.assertIsNotNone(video_poll)
        self.assertIsNotNone(topic_poll)
        self.assertNotIn("selectRun(", video_poll.group("body"))
        self.assertNotIn("selectTopic(", topic_poll.group("body"))
        self.assertIn("selectedDetailVersion", video_poll.group("body"))
        self.assertIn("selectedDetailVersion", topic_poll.group("body"))

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
