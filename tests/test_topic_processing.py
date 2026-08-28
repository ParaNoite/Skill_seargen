import json
import http.client
import os
import ssl
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from skill_gather.cli import main
from skill_gather.github_processing import (
    GitHubFile,
    GitHubRepositorySnapshot,
    select_interesting_paths,
    _get_text,
    fetch_public_github_repository,
    process_github_sources,
)
from skill_gather.models import TopicSourceCandidate
from skill_gather.topic_processing import process_web_sources, write_knowledge_markdown
from skill_gather.topics import TopicRunStore
from skill_gather.web_text import _TextExtractor


class TopicProcessingTests(unittest.TestCase):
    def test_github_path_selection_includes_game_runtime_sources(self):
        tree = [
            {"type": "blob", "path": "README.md"},
            {"type": "blob", "path": "src/game.js"},
            {"type": "blob", "path": "src/collision.js"},
            {"type": "blob", "path": "src/mobile-input.ts"},
            {"type": "blob", "path": "assets/texture.js"},
        ]

        selected = select_interesting_paths(tree, max_files=10)

        self.assertIn("src/game.js", selected)
        self.assertIn("src/collision.js", selected)
        self.assertIn("src/mobile-input.ts", selected)
        self.assertNotIn("assets/texture.js", selected)

    def test_github_path_selection_includes_mobile_game_scripts_outside_src(self):
        tree = [
            {"type": "blob", "path": "README.md"},
            {"type": "blob", "path": "scripts/touch-controls.js"},
            {"type": "blob", "path": "scripts/resize.js"},
            {"type": "blob", "path": "js/game-state.js"},
            {"type": "blob", "path": "lib/components/collision.js"},
            {"type": "blob", "path": "assets/vendor-three.js"},
        ]

        selected = select_interesting_paths(tree, max_files=24)

        self.assertIn("scripts/touch-controls.js", selected)
        self.assertIn("scripts/resize.js", selected)
        self.assertIn("js/game-state.js", selected)
        self.assertIn("lib/components/collision.js", selected)
        self.assertNotIn("assets/vendor-three.js", selected)

    def test_github_api_signals_prioritize_complete_gameplay_evidence(self):
        from skill_gather.github_processing import _signals_for_file

        signals = _signals_for_file(
            "scripts/game.js",
            "\n".join(
                [
                    "const score = 0;",
                    "function updateDecorations() {}",
                    "window.addEventListener('pointerdown', handleTouch);",
                    "window.addEventListener('resize', onResize);",
                    "function checkAABB(player, obstacle) { return true; }",
                    "if (gameOver) showGameOver();",
                    "function restartGame() { resetState(); }",
                    "requestAnimationFrame(gameLoop);",
                ]
            ),
        )

        api = " ".join(item.lower() for item in signals["api"])
        self.assertIn("pointerdown", api)
        self.assertIn("resize", api)
        self.assertIn("checkaabb", api)
        self.assertIn("gameover", api)
        self.assertIn("restartgame", api)

    def test_web_text_prefers_main_content_over_large_navigation_sidebar(self):
        extractor = _TextExtractor()
        extractor.feed(
            "<html><head><title>NavigationAgent 指南</title></head><body>"
            "<nav>" + "站点目录 无关菜单 " * 100 + "</nav>"
            "<div role='main'><div itemprop='articleBody'><h1>使用 NavigationAgent</h1>"
            "<p>NavigationAgent2D 提供路径寻找、路径跟随和代理避障功能。</p>"
            "<p>设置 target_position 后，应在物理帧中读取下一个路径位置并移动父节点。</p>"
            "</div></div></body></html>"
        )

        title, text = extractor.extracted()

        self.assertEqual(title, "NavigationAgent 指南")
        self.assertIn("NavigationAgent2D 提供路径寻找", text)
        self.assertNotIn("站点目录", text)

    def test_web_text_falls_back_to_full_page_without_semantic_content_region(self):
        extractor = _TextExtractor()
        extractor.feed("<html><body><div>" + "普通网页正文内容。" * 20 + "</div></body></html>")

        _title, text = extractor.extracted()

        self.assertIn("普通网页正文内容", text)

    def test_github_text_fetch_retries_transient_tls_disconnects(self):
        from unittest.mock import patch

        class Response:
            headers = type("Headers", (), {"get_content_charset": lambda self: "utf-8"})()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, _limit):
                return b"GitHub content"

        with patch(
            "skill_gather.github_processing.urllib.request.urlopen",
            side_effect=[
                ssl.SSLEOFError(8, "EOF occurred in violation of protocol"),
                http.client.RemoteDisconnected("Remote end closed connection without response"),
                Response(),
            ],
        ) as urlopen, patch("time.sleep") as sleep:
            text = _get_text("https://raw.githubusercontent.com/example/repo/main/README.md", timeout_sec=5, max_bytes=100)

        self.assertEqual(text, "GitHub content")
        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.25, 0.5])

    def test_process_web_sources_writes_evidence_snapshot_and_knowledge(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("家庭烘焙", mode="normal")
            task.package.knowledge = None
            candidate = TopicSourceCandidate(
                url="https://example.test/baking",
                canonical_url="https://example.test/baking",
                candidate_id="cand-baking",
                source_type="web",
                title="烘焙指南",
                quality_score=42,
                selected=True,
            )
            task.candidates = [candidate]
            task.selected_sources = [candidate]
            task.status = "processing_sources"
            task.current_stage = "processing_sources"
            store.save(task)

            def fetcher(_url, *, timeout_sec):
                self.assertEqual(timeout_sec, 9)
                from skill_gather.web_text import WebPage

                return WebPage(
                    requested_url="https://example.test/baking",
                    final_url="https://example.test/baking",
                    title="烘焙指南",
                    content_type="text/html",
                    text="首先准备材料。然后预热烤箱至 180 度。使用温度计确认中心温度，避免烘烤不足。" * 12,
                )

            result = process_web_sources(task, store.run_path(task.run_id), timeout_sec=9, fetcher=fetcher)
            knowledge_path = write_knowledge_markdown(task, store.run_path(task.run_id), result)

            self.assertEqual(len(result.evidence), 1)
            self.assertFalse(result.failures)
            self.assertTrue((store.run_path(task.run_id) / "topic_package/evidence/cand-baking.json").exists())
            self.assertTrue((store.run_path(task.run_id) / "topic_package/references/cand-baking.txt").exists())
            self.assertEqual(task.package.knowledge, "topic_package/knowledge.md")
            self.assertIn("[S1]", knowledge_path.read_text(encoding="utf-8"))
            self.assertIn("步骤或方法", knowledge_path.read_text(encoding="utf-8"))

    def test_process_records_failed_and_skipped_sources_without_hiding_them(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("测试主题", mode="normal")
            web = TopicSourceCandidate(url="https://example.test/nope", candidate_id="cand-web", source_type="web")
            video = TopicSourceCandidate(url="https://www.bilibili.com/video/BVtest", candidate_id="cand-video", source_type="video")
            task.selected_sources = [web, video]

            def failing_fetcher(_url, *, timeout_sec):
                from skill_gather.web_text import WebTextError

                raise WebTextError("网页请求失败：HTTP 404")

            result = process_web_sources(task, store.run_path(task.run_id), fetcher=failing_fetcher)
            audit = json.loads((store.run_path(task.run_id) / "web_processing_audit.json").read_text(encoding="utf-8"))

            self.assertFalse(result.evidence)
            self.assertEqual(len(result.failures), 1)
            self.assertEqual(len(result.skipped), 1)
            self.assertEqual(audit["failed_sources"][0]["candidate_id"], "cand-web")
            self.assertEqual(audit["skipped_sources"][0]["candidate_id"], "cand-video")

    def test_topic_process_command_completes_normal_web_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("网页教程", mode="normal")
            candidate = TopicSourceCandidate(
                url="https://docs.example.test/guide",
                candidate_id="cand-guide",
                source_type="web",
                title="网页教程",
                selected=True,
            )
            task.candidates = [candidate]
            task.selected_sources = [candidate]
            task.status = "processing_sources"
            task.current_stage = "processing_sources"
            store.save(task)

            from io import StringIO
            from skill_gather.web_text import WebPage

            def fixture_fetcher(_url, *, timeout_sec):
                self.assertEqual(timeout_sec, 15)
                return WebPage(
                    requested_url="https://docs.example.test/guide",
                    final_url="https://docs.example.test/guide",
                    title="网页教程",
                    content_type="text/html",
                    text=(
                        "首先安装工具，然后配置项目。使用示例验证配置是否成功，"
                        "最后记录结果。请在每次修改后运行验证命令，并保留可复查的输出，"
                        "以便确认配置、依赖与实际效果一致。"
                    ) * 3,
                )

            stdout = StringIO()
            with patch(
                "skill_gather.cli.process_web_sources",
                side_effect=lambda current_task, run_root, *, timeout_sec: process_web_sources(
                    current_task, run_root, timeout_sec=timeout_sec, fetcher=fixture_fetcher
                ),
            ):
                exit_code = main(
                    ["topic", "process", task.run_id, "--runs", str(store.root)],
                    stdout=stdout,
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["status"], "completed")
            knowledge = store.run_path(task.run_id) / "topic_package/knowledge.md"
            self.assertTrue(knowledge.exists())
            self.assertIn("网页教程", knowledge.read_text(encoding="utf-8"))

    def test_process_github_sources_writes_technical_evidence_and_reference(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("Skill 工具开发", mode="technical")
            candidate = TopicSourceCandidate(
                url="https://github.com/example/toolkit",
                canonical_url="https://github.com/example/toolkit",
                candidate_id="cand-github",
                source_type="github",
                title="example/toolkit",
                quality_score=40,
                selected=True,
            )
            task.candidates = [candidate]
            task.selected_sources = [candidate]

            def fetcher(_url, *, timeout_sec):
                self.assertEqual(timeout_sec, 7)
                return GitHubRepositorySnapshot(
                    repo="example/toolkit",
                    html_url="https://github.com/example/toolkit",
                    default_branch="main",
                    files=[
                        GitHubFile("README.md", "# Toolkit\n\nInstall with `pip install toolkit`.\n\nUsage: `python -m toolkit`.", ""),
                        GitHubFile("docs/api.md", "API 参数说明：调用 generate_skill(topic)。", ""),
                        GitHubFile("SKILL.md", "Codex skill instructions for the existing workflow.", ""),
                    ],
                )

            result = process_github_sources(task, store.run_path(task.run_id), timeout_sec=7, fetcher=fetcher)
            run_root = store.run_path(task.run_id)

            self.assertEqual(len(result.evidence), 1)
            self.assertFalse(result.failures)
            evidence_path = run_root / "topic_package/evidence/github-cand-github.json"
            self.assertTrue(evidence_path.exists())
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(evidence["confidence"], evidence["quality_score"] / 100)
            reference = run_root / "topic_package/references/github-cand-github.md"
            self.assertTrue(reference.exists())
            self.assertIn("已有 skill 材料", reference.read_text(encoding="utf-8"))
            self.assertIn("github_existing_skill_material_downgraded", candidate.risk_flags)

    def test_process_github_sources_skips_github_in_normal_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("普通主题", mode="normal")
            candidate = TopicSourceCandidate(
                url="https://github.com/example/toolkit",
                canonical_url="https://github.com/example/toolkit",
                candidate_id="cand-github",
                source_type="github",
                selected=True,
            )
            task.selected_sources = [candidate]

            result = process_github_sources(task, store.run_path(task.run_id), fetcher=lambda *_args, **_kwargs: None)

            self.assertFalse(result.evidence)
            self.assertEqual(result.skipped[0]["candidate_id"], "cand-github")
            self.assertIn("普通模式", result.skipped[0]["reason"])

    def test_process_github_sources_records_truncated_snapshot_risk(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("Skill 工具开发", mode="technical")
            candidate = TopicSourceCandidate(
                url="https://github.com/example/toolkit",
                canonical_url="https://github.com/example/toolkit",
                candidate_id="cand-github",
                source_type="github",
                selected=True,
            )
            task.selected_sources = [candidate]

            result = process_github_sources(
                task,
                store.run_path(task.run_id),
                fetcher=lambda *_args, **_kwargs: GitHubRepositorySnapshot(
                    repo="example/toolkit",
                    html_url="https://github.com/example/toolkit",
                    default_branch="main",
                    files=[GitHubFile("README.md", "Install with pip install toolkit.")],
                    truncated=True,
                ),
            )

            self.assertEqual(len(result.evidence), 1)
            self.assertIn("github_file_selection_truncated", result.evidence[0]["risk_flags"])

    def test_process_github_sources_forwards_token_environment_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("Skill 工具开发", mode="technical")
            candidate = TopicSourceCandidate(
                url="https://github.com/example/toolkit",
                canonical_url="https://github.com/example/toolkit",
                candidate_id="cand-github",
                source_type="github",
                selected=True,
            )
            task.selected_sources = [candidate]

            from unittest.mock import patch

            with patch(
                "skill_gather.github_processing.fetch_public_github_repository",
                return_value=GitHubRepositorySnapshot(
                    repo="example/toolkit",
                    html_url="https://github.com/example/toolkit",
                    default_branch="main",
                    files=[GitHubFile("README.md", "安装：pip install toolkit")],
                ),
            ) as fetcher:
                process_github_sources(
                    task,
                    store.run_path(task.run_id),
                    token_env="CUSTOM_GITHUB_TOKEN",
                )

                fetcher.assert_called_once_with(
                    "https://github.com/example/toolkit",
                    timeout_sec=15,
                    token_env="CUSTOM_GITHUB_TOKEN",
                )

    def test_process_github_sources_explains_rate_limit_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("Skill 工具开发", mode="technical")
            candidate = TopicSourceCandidate(
                url="https://github.com/example/toolkit",
                canonical_url="https://github.com/example/toolkit",
                candidate_id="cand-github",
                source_type="github",
                selected=True,
            )
            task.selected_sources = [candidate]
            error = urllib.error.HTTPError(
                candidate.url,
                403,
                "rate limit exceeded",
                hdrs=None,
                fp=None,
            )

            result = process_github_sources(
                task,
                store.run_path(task.run_id),
                fetcher=lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
            )

            self.assertIn("GitHub API 速率限制", result.failures[0]["reason"])

    def test_github_fetch_falls_back_to_public_readme_when_anonymous_api_is_rate_limited(self):
        from unittest.mock import patch

        rate_limit = urllib.error.HTTPError(
            "https://api.github.com/repos/example/toolkit",
            403,
            "rate limit exceeded",
            hdrs=None,
            fp=None,
        )

        def read_public_file(url, **_kwargs):
            if url.endswith("/main/README.md"):
                return "# Toolkit\n\nInstall with pip install toolkit."
            raise urllib.error.HTTPError(url, 404, "not found", hdrs=None, fp=None)

        with patch.dict(os.environ, {}, clear=True), patch(
            "skill_gather.github_processing._get_json",
            side_effect=rate_limit,
        ), patch(
            "skill_gather.github_processing._get_text",
            side_effect=read_public_file,
        ):
            snapshot = fetch_public_github_repository("https://github.com/example/toolkit")

        self.assertEqual(snapshot.default_branch, "main")
        self.assertEqual([file.path for file in snapshot.files], ["README.md"])
        self.assertTrue(snapshot.truncated)
        self.assertEqual(snapshot.fallback_reason, "github_api_rate_limit_fallback")

    def test_github_fetch_falls_back_to_default_branch_readme_when_tree_times_out(self):
        from unittest.mock import patch

        metadata = {
            "default_branch": "stable",
            "full_name": "example/toolkit",
            "html_url": "https://github.com/example/toolkit",
        }

        def read_public_file(url, **_kwargs):
            self.assertTrue(url.endswith("/stable/README.md"))
            return "# Toolkit\n\nInstall with pip install toolkit."

        with patch(
            "skill_gather.github_processing._get_json",
            side_effect=[metadata, urllib.error.URLError("tree timed out")],
        ), patch(
            "skill_gather.github_processing._get_text",
            side_effect=read_public_file,
        ):
            snapshot = fetch_public_github_repository("https://github.com/example/toolkit")

        self.assertEqual(snapshot.default_branch, "stable")
        self.assertEqual(snapshot.fallback_reason, "github_network_readme_fallback")
        self.assertEqual([file.path for file in snapshot.files], ["README.md"])

    def test_github_fetch_falls_back_to_readme_when_selected_contents_file_times_out(self):
        from unittest.mock import patch

        metadata = {
            "default_branch": "main",
            "full_name": "example/toolkit",
            "html_url": "https://github.com/example/toolkit",
        }
        tree = {"tree": [{"type": "blob", "path": "docs/api.md"}]}

        def read_public_file(url, **_kwargs):
            if "/contents/docs/api.md?ref=main" in url:
                raise TimeoutError("raw file timed out")
            if url.endswith("/main/README.md"):
                return "# Toolkit\n\nInstall with pip install toolkit."
            raise AssertionError(f"unexpected URL: {url}")

        with patch(
            "skill_gather.github_processing._get_json",
            side_effect=[metadata, tree],
        ), patch(
            "skill_gather.github_processing._get_text",
            side_effect=read_public_file,
        ):
            snapshot = fetch_public_github_repository("https://github.com/example/toolkit")

        self.assertEqual(snapshot.fallback_reason, "github_network_readme_fallback")
        self.assertEqual([file.path for file in snapshot.files], ["README.md"])

    def test_github_network_readme_fallback_is_explained_in_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("Skill 工具开发", mode="technical")
            candidate = TopicSourceCandidate(
                url="https://github.com/example/toolkit",
                candidate_id="cand-github",
                source_type="github",
                selected=True,
            )
            task.selected_sources = [candidate]

            result = process_github_sources(
                task,
                store.run_path(task.run_id),
                fetcher=lambda *_args, **_kwargs: GitHubRepositorySnapshot(
                    repo="example/toolkit",
                    html_url="https://github.com/example/toolkit",
                    default_branch="main",
                    files=[GitHubFile("README.md", "Install with pip install toolkit.")],
                    truncated=True,
                    fallback_reason="github_network_readme_fallback",
                ),
            )

            evidence = result.evidence[0]
            self.assertIn("github_network_readme_fallback", evidence["risk_flags"])
            self.assertIn("github_file_selection_truncated", evidence["risk_flags"])
            self.assertTrue(any("网络超时" in item and "README" in item for item in evidence["limitations"]))

    def test_topic_process_command_completes_technical_github_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("Skill 工具开发", mode="technical")
            candidate = TopicSourceCandidate(
                url="https://github.com/example/toolkit",
                canonical_url="https://github.com/example/toolkit",
                candidate_id="cand-github",
                source_type="github",
                title="example/toolkit",
                selected=True,
            )
            task.candidates = [candidate]
            task.selected_sources = [candidate]
            task.status = "processing_sources"
            task.current_stage = "processing_sources"
            store.save(task)

            def fetcher(_url, *, timeout_sec):
                return GitHubRepositorySnapshot(
                    repo="example/toolkit",
                    html_url="https://github.com/example/toolkit",
                    default_branch="main",
                    files=[GitHubFile("README.md", "安装：pip install toolkit\n命令：python -m toolkit", "")],
                )

            from io import StringIO
            from unittest.mock import patch

            stdout = StringIO()
            with patch("skill_gather.github_processing.fetch_public_github_repository", side_effect=fetcher):
                exit_code = main(
                    ["topic", "process", task.run_id, "--runs", str(store.root)],
                    stdout=stdout,
                )

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["successful_github_sources"], 1)
            self.assertEqual(payload["knowledge"], "topic_package/knowledge.md")
            self.assertEqual(payload["fusion"], "topic_package/fusion.json")
            saved = store.load(task.run_id)
            self.assertEqual(saved.artifacts["github_processing_audit"], "github_processing_audit.json")
            self.assertEqual(saved.artifacts["fusion"], "topic_package/fusion.json")


if __name__ == "__main__":
    unittest.main()
