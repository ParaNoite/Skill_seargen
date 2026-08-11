import json
import threading
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from skill_gather.config import parse_config
from skill_gather.models import TopicBudget, TopicCachePolicy, TopicTask
from skill_gather.search import (
    FakeSearchProvider,
    RawSearchResult,
    BilibiliSearchProvider,
    GitHubSearchProvider,
    SearchCache,
    SearXNGProvider,
    build_queries,
    canonicalize_url,
    infer_source_type,
    normalize_candidates,
    search_topic,
)


CONFIG = {
    "providers": {
        "newapi": {
            "base_url": "https://api.example.test/v1",
            "api_key_env": "SEARCH_TEST_KEY",
            "vision_model": "vision",
            "asr_model": "faster-whisper:base",
            "distiller_model": "distiller",
            "judge_model": "judge",
        }
    },
    "defaults": {"provider": "newapi", "output_dir": "./skills", "run_dir": "./runs"},
}


class SearchTests(unittest.TestCase):
    def test_github_issue_and_pull_urls_are_web_evidence_not_repository_sources(self):
        self.assertEqual(infer_source_type("https://github.com/godotengine/godot"), "github")
        self.assertEqual(infer_source_type("https://github.com/godotengine/godot/issues/88648"), "web")
        self.assertEqual(infer_source_type("https://github.com/godotengine/godot/pull/123"), "web")

    def test_github_queries_add_latin_technical_fallback_for_mixed_language_topic(self):
        queries = build_queries(
            "Godot NavigationAgent2D 寻路避障",
            "technical",
            "github",
            max_queries=2,
        )

        self.assertEqual(queries, ["Godot NavigationAgent2D 寻路避障", "Godot NavigationAgent2D"])

    def test_github_queries_keep_example_variant_for_latin_topic(self):
        queries = build_queries("Godot StateCharts", "technical", "github", max_queries=2)

        self.assertEqual(queries, ["Godot StateCharts", "Godot StateCharts example"])

    def test_github_queries_focus_long_latin_topic(self):
        queries = build_queries(
            "Godot 4 NavigationAgent2D avoidance velocity_computed implementation",
            "technical",
            "github",
            max_queries=2,
        )

        self.assertEqual(
            queries,
            [
                "Godot 4 NavigationAgent2D avoidance velocity_computed implementation",
                "Godot NavigationAgent2D",
            ],
        )

    def test_github_queries_keep_core_noun_phrase_without_identifier(self):
        queries = build_queries(
            "Godot 4 typed signal event bus autoload decoupled communication",
            "technical",
            "github",
            max_queries=2,
        )

        self.assertEqual(queries[1], "Godot signal event bus")

    def test_canonicalize_url_removes_tracking_and_fragment(self):
        self.assertEqual(
            canonicalize_url("HTTPS://Example.COM:443/path?utm_source=x&b=2#section"),
            "https://example.com/path?b=2",
        )

    def test_normalization_deduplicates_and_keeps_provider_metadata(self):
        batches = [
            FakeSearchProvider(
                {
                    "*": [
                        RawSearchResult("fake", "Godot", 1, "https://example.test/a?utm_source=x", "Godot", "tutorial", "one"),
                        RawSearchResult("fake", "Godot", 2, "https://example.test/a", "Godot duplicate", "longer summary", "two"),
                    ]
                }
            ).search(["Godot"], max_results=10)
        ]
        candidates = normalize_candidates("Godot", batches, max_candidates=20)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].duplicate_count, 1)
        self.assertEqual(candidates[0].engines, ["one", "two"])
        self.assertTrue(candidates[0].candidate_id.startswith("cand-"))

    def test_normalization_drops_severe_download_keyword_spam(self):
        spam = "个人收藏书籍列表 " + "PDF下载 百度云 电子书下载 免费下载 " * 12
        batches = [
            FakeSearchProvider(
                {
                    "*": [
                        RawSearchResult(
                            "github",
                            "Godot navigation",
                            1,
                            "https://github.com/example/books",
                            "example/books",
                            spam,
                            "github",
                        )
                    ]
                }
            ).search(["Godot navigation"], max_results=10)
        ]

        self.assertEqual(normalize_candidates("Godot navigation", batches, max_candidates=20), [])

    def test_normalization_prefers_clean_summary_over_long_spam_duplicate(self):
        spam = "PDF下载 百度云 电子书下载 免费下载 " * 12
        batches = [
            FakeSearchProvider(
                {
                    "*": [
                        RawSearchResult(
                            "github",
                            "Godot navigation",
                            1,
                            "https://github.com/example/navigation",
                            "example/navigation",
                            "Godot NavigationAgent examples and API documentation.",
                            "github",
                        ),
                        RawSearchResult(
                            "searxng",
                            "Godot navigation",
                            2,
                            "https://github.com/example/navigation",
                            "example/navigation",
                            spam,
                            "search",
                        ),
                    ]
                }
            ).search(["Godot navigation"], max_results=10)
        ]

        candidates = normalize_candidates("Godot navigation", batches, max_candidates=20)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].summary, "Godot NavigationAgent examples and API documentation.")

    def test_cache_round_trip_and_expiry_configuration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = SearchCache(Path(temp_dir) / "cache.sqlite3", ttl_sec=3600)
            batch = FakeSearchProvider({"*": [RawSearchResult("fake", "x", 1, "https://example.test", "x")] }).search(["x"], max_results=5)
            cache.put("key", batch)
            restored = cache.get("key")
            self.assertIsNotNone(restored)
            self.assertTrue(restored.cache_hit)

    def test_fake_topic_search_produces_candidates_without_network(self):
        config = parse_config(CONFIG)
        task = TopicTask(
            run_id="topic-test",
            topic="Godot 导航",
            mode="technical",
            budget=TopicBudget(max_candidates=20, max_selected_sources=5),
            cache=TopicCachePolicy(reuse_cache=False),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            candidates, batches, audit, intent = search_topic(task, config, cache_path=Path(temp_dir) / "cache.sqlite3", use_fake=True)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(audit), 1)
        self.assertGreaterEqual(len(candidates), 3)
        self.assertEqual({candidate.source_type for candidate in candidates}, {"video", "github", "web"})
        self.assertEqual(intent.strategy, "deterministic")
        self.assertTrue(candidates[0].score_breakdown)

    def test_state_machine_candidate_ranks_above_broad_godot_matches(self):
        batch = FakeSearchProvider(
            {
                "*": [
                    RawSearchResult("bilibili", "Godot 4 角色状态机设计", 1, "https://www.bilibili.com/video/BV1xx411c7mD/", "Godot 4 UI 选项卡"),
                    RawSearchResult("bilibili", "Godot 4 角色状态机设计", 2, "https://www.bilibili.com/video/BV1yy411c7mD/", "Godot4 有限状态机教程"),
                ]
            }
        ).search(["Godot 4 角色状态机设计"], max_results=10)

        candidates = normalize_candidates("Godot 4 角色状态机设计", [batch], max_candidates=10)

        self.assertIn("状态机", candidates[0].title)
        self.assertGreater(candidates[0].quality_score, candidates[1].quality_score)

    def test_searxng_without_url_fails_explicitly(self):
        with self.assertRaises(Exception) as context:
            SearXNGProvider("").search(["test"], max_results=5)
        self.assertIn("SearXNG", str(context.exception))

    def test_bilibili_412_falls_back_to_public_search_page(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/api"):
                    self.send_response(412)
                    self.end_headers()
                    return
                body = (
                    '<div class="bili-video-card__wrap">'
                    '<a href="//www.bilibili.com/video/BV1FC4y1H7vn/"></a>'
                    '<h3 title="Godot 导航教程"></h3>'
                    '</div>'
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
            base_url = f"http://127.0.0.1:{server.server_port}"
            batch = BilibiliSearchProvider(f"{base_url}/api", f"{base_url}/all").search(["Godot"], max_results=2)
            self.assertEqual(batch.results[0].url, "https://www.bilibili.com/video/BV1FC4y1H7vn/")
            self.assertEqual(batch.results[0].title, "Godot 导航教程")
            self.assertEqual(batch.results[0].metadata["search_fallback"], "public_html")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_bilibili_api_skips_non_video_results(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                payload = {
                    "data": {
                        "result": [
                            {"arcurl": "https://www.bilibili.com/cheese/play/ss1", "bvid": "BV1paidcourse", "title": "付费课程"},
                            {"bvid": "BV1FC4y1H7vn", "title": "公开视频"},
                        ]
                    }
                }
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            provider = BilibiliSearchProvider(f"http://127.0.0.1:{server.server_port}/api")
            results = provider.search(["Godot"], max_results=5).results
            self.assertEqual([result.title for result in results], ["公开视频"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_real_provider_adapters_parse_local_json_responses(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/bili"):
                    payload = {"data": {"result": [{"bvid": "BVfixture", "arcurl": "https://www.bilibili.com/video/BVfixture/", "title": "B站教程", "description": "摘要", "author": "作者"}]}}
                elif self.path.startswith("/search/repositories"):
                    payload = {"items": [{"full_name": "example/repo", "html_url": "https://github.com/example/repo", "description": "仓库摘要", "language": "Python", "stargazers_count": 4}]}
                else:
                    payload = {"results": [{"url": "https://docs.example.test/guide", "title": "文档", "content": "网页摘要", "engines": ["fixture"]}]}
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            bili = BilibiliSearchProvider(f"{base_url}/bili")
            github = GitHubSearchProvider(base_url, "MISSING_TOKEN")
            searx = SearXNGProvider(base_url)
            self.assertEqual(bili.search(["x"], max_results=2).results[0].metadata["bvid"], "BVfixture")
            self.assertEqual(github.search(["x"], max_results=2).results[0].title, "example/repo")
            self.assertEqual(searx.search(["x"], max_results=2).results[0].engine, "fixture")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
