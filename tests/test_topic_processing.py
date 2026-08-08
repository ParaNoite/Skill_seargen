import json
import os
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from skill_gather.cli import main
from skill_gather.github_processing import (
    GitHubFile,
    GitHubRepositorySnapshot,
    fetch_public_github_repository,
    process_github_sources,
)
from skill_gather.models import TopicSourceCandidate
from skill_gather.topic_processing import process_web_sources, write_knowledge_markdown
from skill_gather.topics import TopicRunStore


class TopicProcessingTests(unittest.TestCase):
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
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = (
                    "<html><head><title>网页教程</title></head><body>"
                    "<script>const hidden = '不应进入网页证据';</script>"
                    "<h1>网页教程</h1><p>首先安装工具，然后配置项目。</p>"
                    "<p>使用示例验证配置是否成功，最后记录结果。请在每次修改后运行验证命令，"
                    "并保留可复查的输出，以便确认配置、依赖与实际效果一致。</p>"
                    "</body></html>"
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                store = TopicRunStore(Path(temp_dir) / "runs")
                task = store.start_or_resume("网页教程", mode="normal")
                candidate = TopicSourceCandidate(
                    url=f"http://127.0.0.1:{server.server_port}/guide",
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

                stdout = StringIO()
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
                self.assertNotIn("不应进入网页证据", knowledge.read_text(encoding="utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

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
            self.assertIsNone(payload["knowledge"])
            saved = store.load(task.run_id)
            self.assertEqual(saved.artifacts["github_processing_audit"], "github_processing_audit.json")


if __name__ == "__main__":
    unittest.main()
