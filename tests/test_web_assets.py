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

    def test_school_frontend_clone_has_its_own_source_and_assets(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "frontend-school" / "nav.jsx").read_text(encoding="utf-8")
        asset_root = root / "src" / "skill_gather" / "web_assets_school"

        self.assertIn("AI 大学", source)
        self.assertIn("技能市场", source)
        self.assertIn("教务工作台", source)
        for name in ("index.html", "app.css", "app.js", "react-nav.js"):
            self.assertTrue((asset_root / name).is_file(), name)

    def test_school_home_uses_four_product_entries_instead_of_skill_preview(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "frontend-school" / "nav.jsx").read_text(encoding="utf-8")
        home = source[source.index("function Home"):source.index("function Catalog")]

        for label in ("单视频蒸馏", "主题研究", "技能包市场", "Agent 市场"):
            self.assertIn(label, source)
        self.assertIn('className="home-actions"', home)
        self.assertNotIn("hero-shelf", home)
        self.assertNotIn("SkillCard", home)
        self.assertIn('workspaceTarget === "topic" ? "#topic-input" : "#video-url"', source)

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


    def test_topic_confirmation_layout_keeps_plan_controls_full_width(self):
        root = Path(__file__).resolve().parents[1]
        for relative in ("src/skill_gather/web_assets/app.css", "src/skill_gather/web_assets_school/app.css"):
            css = (root / relative).read_text(encoding="utf-8")
            self.assertRegex(css, re.compile(r"\.operation-progress\s*\{[^}]*position:\s*relative", re.DOTALL))
            self.assertIn(".topic-confirmation .candidate-list { grid-column: 1 / -1;", css)
            self.assertRegex(css, re.compile(r"\.plan-editor\s*\{[^}]*grid-column:\s*1 / -1", re.DOTALL))

    def test_result_artifact_preview_and_download_controls_exist(self):
        root = Path(__file__).resolve().parents[1]
        for asset_root in (root / "src/skill_gather/web_assets", root / "src/skill_gather/web_assets_school"):
            html = (asset_root / "index.html").read_text(encoding="utf-8")
            javascript = (asset_root / "app.js").read_text(encoding="utf-8")
            css = (asset_root / "app.css").read_text(encoding="utf-8")
            self.assertIn('id="artifact-preview"', html)
            self.assertIn("openArtifactPreview", javascript)
            self.assertIn("/download/${kind}", javascript)
            self.assertIn('actualMode === "skill" ? "Agent Skill" : "产物摘要"', javascript)
            self.assertIn(".artifact-preview-dialog", css)


def _read_app_js() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "src"
        / "skill_gather"
        / "web_assets"
        / "app.js"
    ).read_text(encoding="utf-8")
