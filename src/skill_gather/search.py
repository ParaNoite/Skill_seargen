from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import AppConfig, SearchConfig
from .integrations.newapi import NewApiClient, NewApiError
from .models import TopicSourceCandidate, TopicTask


_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
_BILIBILI_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv"}
_DOWNLOAD_SPAM_MARKERS = ("pdf下载", "电子书下载", "百度云", "免费下载", "网盘下载")
_MAX_CANDIDATE_SUMMARY_CHARS = 360
_TAG_RE = re.compile(r"<[^>]+>")
_BILIBILI_VIDEO_PATH_RE = re.compile(r"^//(?:www\.)?bilibili\.com/video/(BV[0-9A-Za-z]{10})/?$")
_BILIBILI_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://search.bilibili.com/",
}


class SearchProviderError(RuntimeError):
    def __init__(self, message: str, *, code: str = "search_provider_error"):
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class RawSearchResult:
    provider: str
    query: str
    rank: int
    url: str
    title: str = ""
    snippet: str = ""
    engine: str = ""
    published_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RawSearchResult":
        return cls(**value)


@dataclass(slots=True)
class SearchBatch:
    provider: str
    queries: list[str]
    results: list[RawSearchResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    cache_hit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "queries": list(self.queries),
            "results": [result.to_dict() for result in self.results],
            "warnings": list(self.warnings),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cache_hit": self.cache_hit,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SearchBatch":
        return cls(
            provider=str(value["provider"]),
            queries=[str(item) for item in value.get("queries", [])],
            results=[RawSearchResult.from_dict(item) for item in value.get("results", [])],
            warnings=[str(item) for item in value.get("warnings", [])],
            started_at=str(value.get("started_at", "")),
            finished_at=str(value.get("finished_at", "")),
            cache_hit=bool(value.get("cache_hit", False)),
        )


@dataclass(slots=True)
class SearchIntent:
    topic: str
    mode: str
    goal: str
    facets: list[str]
    exclusions: list[str]
    query_variants: list[str] = field(default_factory=list)
    strategy: str = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SearchProvider(Protocol):
    name: str

    def search(self, queries: list[str], *, max_results: int) -> SearchBatch:
        """Return public search-result metadata only; never fetch result pages."""


class SearchCache:
    """Small local cache for provider result metadata, never page content."""

    def __init__(self, path: str | Path, *, ttl_sec: int):
        self.path = Path(path)
        self.ttl_sec = ttl_sec
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS search_cache (
                    cache_key TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    def get(self, key: str) -> SearchBatch | None:
        connection = sqlite3.connect(self.path)
        try:
            row = connection.execute(
                "SELECT created_at, payload FROM search_cache WHERE cache_key = ?", (key,)
            ).fetchone()
        finally:
            connection.close()
        if row is None or time.time() - float(row[0]) > self.ttl_sec:
            return None
        try:
            value = json.loads(str(row[1]))
            batch = SearchBatch.from_dict(value)
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
        batch.cache_hit = True
        return batch

    def put(self, key: str, batch: SearchBatch) -> None:
        payload = json.dumps(batch.to_dict(), ensure_ascii=False, sort_keys=True)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                "INSERT OR REPLACE INTO search_cache(cache_key, created_at, payload) VALUES (?, ?, ?)",
                (key, time.time(), payload),
            )
            connection.commit()
        finally:
            connection.close()


class FakeSearchProvider:
    name = "fake"

    def __init__(self, fixtures: dict[str, list[RawSearchResult]] | None = None, *, warning: str = ""):
        self.fixtures = fixtures or {}
        self.warning = warning

    def search(self, queries: list[str], *, max_results: int) -> SearchBatch:
        started_at = _now()
        results: list[RawSearchResult] = []
        for query in queries:
            for item in self.fixtures.get(query, self.fixtures.get("*", [])):
                results.append(
                    RawSearchResult(
                        provider=self.name,
                        query=query,
                        rank=len(results) + 1,
                        url=item.url,
                        title=item.title,
                        snippet=item.snippet,
                        engine=item.engine or "fixture",
                        published_at=item.published_at,
                        metadata=dict(item.metadata),
                    )
                )
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break
        return SearchBatch(
            provider=self.name,
            queries=queries,
            results=results,
            warnings=[self.warning] if self.warning else [],
            started_at=started_at,
            finished_at=_now(),
        )


class _BilibiliSearchPageParser(HTMLParser):
    """Extract public video cards from Bilibili's server-rendered search page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, str]] = []
        self._div_depth = 0
        self._card_depth: int | None = None
        self._card: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "div":
            self._div_depth += 1
            if "bili-video-card__wrap" in values.get("class", ""):
                self._card_depth = self._div_depth
                self._card = {}
        if self._card is None:
            return
        title = values.get("title", "").strip()
        if title and "title" not in self._card:
            self._card["title"] = title
        if tag != "a":
            return
        href = values.get("href", "")
        match = _BILIBILI_VIDEO_PATH_RE.match(href)
        if match:
            self._card["bvid"] = match.group(1)
            self._card["url"] = f"https://www.bilibili.com/video/{match.group(1)}/"

    def handle_endtag(self, tag: str) -> None:
        if tag != "div":
            return
        if self._card is not None and self._card_depth == self._div_depth:
            if self._card.get("url"):
                self.cards.append(self._card)
            self._card = None
            self._card_depth = None
        self._div_depth -= 1


class BilibiliSearchProvider:
    name = "bilibili"

    def __init__(self, base_url: str, web_search_url: str = "https://search.bilibili.com/all", *, timeout_sec: int = 15):
        self.base_url = base_url
        self.web_search_url = web_search_url
        self.timeout_sec = timeout_sec

    def search(self, queries: list[str], *, max_results: int) -> SearchBatch:
        started_at = _now()
        results: list[RawSearchResult] = []
        for query in queries:
            params = urlencode({"search_type": "video", "keyword": query, "page": 1, "page_size": max_results})
            try:
                payload = _get_json(
                    f"{self.base_url}?{params}",
                    timeout_sec=self.timeout_sec,
                    headers={**_BILIBILI_BROWSER_HEADERS, "Accept": "application/json, text/plain, */*"},
                )
            except SearchProviderError as exc:
                if exc.code != "http_412":
                    raise
                return self._search_public_page(queries, max_results=max_results, started_at=started_at)
            if isinstance(payload, dict) and payload.get("code", 0) not in {0, None}:
                raise SearchProviderError(
                    f"Bilibili 搜索失败：{payload.get('message') or payload.get('code')}",
                    code="bilibili_search_failed",
                )
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            items = data.get("result", []) if isinstance(data, dict) else []
            if not isinstance(items, list):
                raise SearchProviderError("Bilibili 搜索响应缺少视频结果", code="invalid_bilibili_response")
            for item in items:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("arcurl") or "").strip()
                bvid = str(item.get("bvid") or "").strip()
                if url and not _is_bilibili_public_video_url(url):
                    continue
                if bvid:
                    url = f"https://www.bilibili.com/video/{bvid}/"
                elif not url:
                    continue
                results.append(
                    RawSearchResult(
                        provider=self.name,
                        query=query,
                        rank=len(results) + 1,
                        url=url,
                        title=_strip_html(item.get("title", "")),
                        snippet=_strip_html(item.get("description", "")),
                        engine="bilibili",
                        metadata={
                            "author": str(item.get("author") or ""),
                            "duration": str(item.get("duration") or ""),
                            "bvid": bvid,
                        },
                    )
                )
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break
        return SearchBatch(self.name, queries, results, started_at=started_at, finished_at=_now())

    def _search_public_page(self, queries: list[str], *, max_results: int, started_at: str) -> SearchBatch:
        results: list[RawSearchResult] = []
        for query in queries:
            page = _get_text(
                f"{self.web_search_url}?{urlencode({'keyword': query})}",
                timeout_sec=self.timeout_sec,
                headers={**_BILIBILI_BROWSER_HEADERS, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
            )
            parser = _BilibiliSearchPageParser()
            parser.feed(page)
            parser.close()
            for card in parser.cards:
                results.append(
                    RawSearchResult(
                        provider=self.name,
                        query=query,
                        rank=len(results) + 1,
                        url=card["url"],
                        title=card.get("title", ""),
                        engine="bilibili-web-search",
                        metadata={"bvid": card.get("bvid", ""), "search_fallback": "public_html"},
                    )
                )
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break
        return SearchBatch(self.name, queries, results, started_at=started_at, finished_at=_now())


class GitHubSearchProvider:
    name = "github"

    def __init__(self, api_url: str, token_env: str, *, timeout_sec: int = 15):
        self.api_url = api_url.rstrip("/")
        self.token_env = token_env
        self.timeout_sec = timeout_sec

    def search(self, queries: list[str], *, max_results: int) -> SearchBatch:
        started_at = _now()
        results: list[RawSearchResult] = []
        headers = {"User-Agent": "skill-seargen/0.4", "Accept": "application/vnd.github+json"}
        token = os.getenv(self.token_env, "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        for query in queries:
            params = urlencode({"q": query, "per_page": max_results})
            payload = _get_json(f"{self.api_url}/search/repositories?{params}", timeout_sec=self.timeout_sec, headers=headers)
            items = payload.get("items", []) if isinstance(payload, dict) else []
            if not isinstance(items, list):
                raise SearchProviderError("GitHub 搜索响应缺少仓库结果", code="invalid_github_response")
            for item in items:
                if not isinstance(item, dict):
                    continue
                results.append(
                    RawSearchResult(
                        provider=self.name,
                        query=query,
                        rank=len(results) + 1,
                        url=str(item.get("html_url") or ""),
                        title=str(item.get("full_name") or item.get("name") or ""),
                        snippet=str(item.get("description") or ""),
                        engine="github",
                        published_at=str(item.get("updated_at") or ""),
                        metadata={
                            "language": str(item.get("language") or ""),
                            "stars": int(item.get("stargazers_count") or 0),
                            "forks": int(item.get("forks_count") or 0),
                        },
                    )
                )
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break
        return SearchBatch(self.name, queries, results, started_at=started_at, finished_at=_now())


class SearXNGProvider:
    name = "searxng"

    def __init__(self, base_url: str, *, timeout_sec: int = 15):
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec

    def search(self, queries: list[str], *, max_results: int) -> SearchBatch:
        if not self.base_url:
            raise SearchProviderError("未配置 search.searxng_base_url；请先部署并配置本地 SearXNG。", code="searxng_not_configured")
        started_at = _now()
        results: list[RawSearchResult] = []
        for query in queries:
            params = urlencode({"q": query, "format": "json", "safesearch": 1, "language": "zh-CN"})
            payload = _get_json(f"{self.base_url}/search?{params}", timeout_sec=self.timeout_sec, headers={"User-Agent": "skill-seargen/0.4"})
            items = payload.get("results", []) if isinstance(payload, dict) else []
            if not isinstance(items, list):
                raise SearchProviderError("SearXNG 未返回 JSON 搜索结果。", code="invalid_searxng_response")
            for item in items:
                if not isinstance(item, dict):
                    continue
                engines = item.get("engines", [])
                engine = ",".join(str(value) for value in engines) if isinstance(engines, list) else str(item.get("engine") or "")
                results.append(
                    RawSearchResult(
                        provider=self.name,
                        query=query,
                        rank=len(results) + 1,
                        url=str(item.get("url") or ""),
                        title=str(item.get("title") or ""),
                        snippet=str(item.get("content") or ""),
                        engine=engine,
                        published_at=str(item.get("publishedDate") or ""),
                    )
                )
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break
        return SearchBatch(self.name, queries, results, started_at=started_at, finished_at=_now())


def provider_names_for_mode(mode: str) -> list[str]:
    return ["bilibili", "searxng"] + (["github"] if mode == "technical" else [])


def build_queries(topic: str, mode: str, provider: str, *, max_queries: int, extra_queries: list[str] | None = None) -> list[str]:
    base = topic.strip()
    github_terms = _unique(re.findall(r"[A-Za-z][A-Za-z0-9_.+#-]*", base))
    generic_github_terms = {
        "and", "architecture", "best", "communication", "decoupled", "failure",
        "handling", "implementation", "practices", "progress", "technical", "typed", "workflow",
    }
    focused_github_terms = [
        term
        for term in github_terms
        if term.lower() != "godot" and term.lower() not in generic_github_terms
    ]
    identifier_terms = [
        term
        for term in focused_github_terms
        if "_" in term or any(character.isupper() for character in term[1:]) or any(character.isdigit() for character in term)
    ]
    focus = identifier_terms[:1] or focused_github_terms[:3]
    github_fallback = " ".join(
        (["Godot"] if any(term.lower() == "godot" for term in github_terms) else [])
        + focus
    )
    github_queries = [base, github_fallback if github_fallback and github_fallback != base else f"{base} example"]
    templates = {
        "bilibili": [base, f"{base} 教程"],
        "github": github_queries,
        "searxng": [base, f"{base} 教程", f"{base} official documentation", f"{base} GitHub" if mode == "technical" else f"{base} 实践"],
        "fake": [base],
    }
    result: list[str] = []
    for query in templates.get(provider, [base]):
        if query not in result:
            result.append(query)
    for query in extra_queries or []:
        if query not in result:
            result.append(query)
    return result[:max_queries]


def build_search_intent(topic: str, mode: str, client: NewApiClient | None, model: str, *, use_llm: bool) -> tuple[SearchIntent, str | None]:
    facets = _unique(re.findall(r"[A-Za-z][A-Za-z0-9_\-]{1,}|[\u4e00-\u9fff]{2,}", topic))[:3]
    intent = SearchIntent(
        topic=topic,
        mode=mode,
        goal=f"发现与 {topic} 直接相关的公开来源",
        facets=facets or [topic],
        exclusions=["付费或受限内容", "非公开来源", "与主题无关的结果"],
    )
    if not use_llm:
        return intent, None
    if client is None:
        return intent, "skipped_missing_api_key"
    try:
        enriched = client.build_search_intent(topic, mode, model)
    except NewApiError as exc:
        return intent, f"fallback:{exc.code}"
    intent.goal = enriched["goal"] or intent.goal
    intent.facets = _unique(intent.facets + enriched["facets"])[:3]
    intent.exclusions = _unique(intent.exclusions + enriched["exclusions"])[:3]
    intent.query_variants = [query for query in enriched["queries"] if query != topic][:2]
    intent.strategy = "newapi"
    return intent, None


def build_provider(name: str, config: SearchConfig, *, fake_fixtures: dict[str, list[RawSearchResult]] | None = None) -> SearchProvider:
    if name == "fake":
        return FakeSearchProvider(fake_fixtures)
    if name == "bilibili":
        return BilibiliSearchProvider(
            config.bilibili_search_url,
            config.bilibili_web_search_url,
            timeout_sec=config.timeout_sec,
        )
    if name == "github":
        return GitHubSearchProvider(config.github_api_url, config.github_token_env, timeout_sec=config.timeout_sec)
    if name == "searxng":
        return SearXNGProvider(config.searxng_base_url, timeout_sec=config.timeout_sec)
    raise ValueError(f"未知搜索 provider：{name}")


def search_topic(
    task: TopicTask,
    config: AppConfig,
    *,
    cache_path: str | Path,
    use_fake: bool = False,
    fake_fixtures: dict[str, list[RawSearchResult]] | None = None,
) -> tuple[list[TopicSourceCandidate], list[SearchBatch], list[dict[str, str]], SearchIntent]:
    provider_names = ["fake"] if use_fake else provider_names_for_mode(task.mode)
    if use_fake and fake_fixtures is None:
        fake_fixtures = {
            "*": [
                RawSearchResult(
                    provider="fake",
                    query="",
                    rank=1,
                    url="https://www.bilibili.com/video/BVfakev04demo/",
                    title=f"{task.topic} 视频教程（fixture）",
                    snippet="用于离线验证主题候选流程的公开视频结果。",
                    engine="fixture-bilibili",
                ),
                RawSearchResult(
                    provider="fake",
                    query="",
                    rank=2,
                    url="https://github.com/example/skill-seargen-fixture",
                    title=f"example/skill-seargen-fixture: {task.topic}",
                    snippet="用于离线验证技术主题候选流程的公开仓库结果。",
                    engine="fixture-github",
                ),
                RawSearchResult(
                    provider="fake",
                    query="",
                    rank=3,
                    url="https://docs.example.test/topics/skill-seargen-fixture",
                    title=f"{task.topic} 官方文档（fixture）",
                    snippet="用于离线验证网页候选流程的文档结果。",
                    engine="fixture-web",
                ),
            ]
        }
    cache = SearchCache(cache_path, ttl_sec=config.search.cache_ttl_sec)
    batches: list[SearchBatch] = []
    query_audit: list[dict[str, str]] = []
    client = NewApiClient.from_config(config.newapi)
    intent, intent_warning = build_search_intent(task.topic, task.mode, client, config.newapi.distiller_model, use_llm=config.search.use_newapi_query_expansion)
    if intent_warning:
        query_audit.append({"provider": "newapi", "query": "", "reason": intent_warning})
    query_audit.extend({"provider": "intent", "query": query, "reason": intent.strategy} for query in intent.query_variants)
    for name in provider_names:
        queries = build_queries(task.topic, task.mode, name, max_queries=config.search.max_queries, extra_queries=intent.query_variants)
        query_audit.extend({"provider": name, "query": query, "reason": "deterministic_template"} for query in queries)
        provider = build_provider(name, config.search, fake_fixtures=fake_fixtures)
        cache_key = _cache_key(name, queries, config.search.per_provider_results)
        batch = None if task.cache.refresh_cache or not task.cache.reuse_cache else cache.get(cache_key)
        if batch is None:
            try:
                batch = provider.search(queries, max_results=config.search.per_provider_results)
            except SearchProviderError as exc:
                batch = SearchBatch(name, queries, warnings=[f"{exc.code}: {exc}"] , started_at=_now(), finished_at=_now())
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                batch = SearchBatch(name, queries, warnings=[f"network_error: {exc}"], started_at=_now(), finished_at=_now())
            if not batch.warnings:
                cache.put(cache_key, batch)
        batches.append(batch)
    candidates = normalize_candidates(task.topic, batches, max_candidates=task.budget.max_candidates, intent=intent)
    assessment_warning = assess_candidates(candidates, intent, client, config.newapi.distiller_model, use_llm=config.search.use_newapi_candidate_assessment)
    if assessment_warning:
        query_audit.append({"provider": "newapi", "query": "", "reason": assessment_warning})
    return candidates, batches, query_audit, intent


def normalize_candidates(topic: str, batches: list[SearchBatch], *, max_candidates: int, intent: SearchIntent | None = None) -> list[TopicSourceCandidate]:
    grouped: dict[str, list[RawSearchResult]] = {}
    for batch in batches:
        for result in batch.results:
            canonical = canonicalize_url(result.url)
            if not canonical:
                continue
            grouped.setdefault(canonical, []).append(result)

    candidates: list[TopicSourceCandidate] = []
    for canonical, results in grouped.items():
        first = results[0]
        title = max((item.title.strip() for item in results), key=len, default="")
        summaries = [item.snippet for item in results if item.snippet.strip()]
        clean_summaries = [_clean_candidate_summary(summary) for summary in summaries if not _is_severe_download_spam(summary)]
        if summaries and not clean_summaries:
            continue
        summary = max(clean_summaries, key=len, default="")
        providers = _unique([item.provider for item in results])
        engines = _unique([item.engine for item in results if item.engine])
        queries = _unique([item.query for item in results])
        source_type = infer_source_type(canonical)
        risks = ["unknown_source"] if source_type == "unknown" else []
        score = score_candidate(topic, title, summary, source_type, risks)
        facets = _matched_facets(title, summary, intent.facets if intent else [topic])
        candidates.append(
            TopicSourceCandidate(
                candidate_id=f"cand-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:12]}",
                url=first.url,
                canonical_url=canonical,
                host=urlsplit(canonical).hostname or "",
                title=title,
                summary=summary,
                source_type=source_type,
                query=queries[0] if queries else "",
                queries=queries,
                providers=providers,
                engines=engines,
                relevance_reason=f"命中查询：{'；'.join(queries)}",
                quality_score=score,
                risk_flags=risks,
                duplicate_count=max(0, len(results) - 1),
                matched_facets=facets,
                score_breakdown={"rule": score},
            )
        )
    return _diverse_rank(candidates, max_candidates)


def canonicalize_url(url: str) -> str:
    try:
        parsed = urlsplit(str(url).strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname.lower()
    port = parsed.port
    netloc = host if port in {None, 80, 443} else f"{host}:{port}"
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_QUERY_KEYS
    ]
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", urlencode(query, doseq=True), ""))


def infer_source_type(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host in _BILIBILI_HOSTS or host.endswith(".bilibili.com"):
        return "video"
    parts = [part for part in parsed.path.split("/") if part]
    if host == "github.com" and len(parts) == 2:
        return "github"
    return "web" if host else "unknown"


def score_candidate(topic: str, title: str, summary: str, source_type: str, risks: list[str]) -> int:
    haystack = f"{title} {summary}".lower()
    latin_tokens = [token.lower() for token in re.findall(r"[A-Za-z]+|[0-9]+", topic)]
    latin_score = sum((8 if token.isalpha() else 2) for token in latin_tokens if token in haystack)
    chinese_segments = re.findall(r"[\u4e00-\u9fff]{2,}", topic)
    chinese_match = max((_longest_common_substring_length(segment, haystack) for segment in chinese_segments), default=0)
    relevance = min(40, 8 + latin_score + chinese_match * 4)
    trust = {"github": 25, "video": 18, "web": 14}.get(source_type, 5)
    completeness = (8 if title else 0) + (7 if summary else 0)
    freshness = 5
    risk_penalty = 10 if risks else 0
    return max(0, min(100, relevance + trust + completeness + freshness - risk_penalty))


def _clean_candidate_summary(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()
    if len(normalized) <= _MAX_CANDIDATE_SUMMARY_CHARS:
        return normalized
    return normalized[: _MAX_CANDIDATE_SUMMARY_CHARS - 1].rstrip() + "…"


def _is_severe_download_spam(value: str) -> bool:
    lowered = re.sub(r"\s+", "", value.lower())
    hits = sum(lowered.count(marker) for marker in _DOWNLOAD_SPAM_MARKERS)
    distinct_markers = sum(marker in lowered for marker in _DOWNLOAD_SPAM_MARKERS)
    return hits >= 6 and distinct_markers >= 2


def _matched_facets(title: str, summary: str, facets: list[str]) -> list[str]:
    haystack = f"{title} {summary}".lower()
    return [facet for facet in facets if facet and _facet_matches(facet, haystack)][:3]


def _facet_matches(facet: str, haystack: str) -> bool:
    lowered = facet.lower()
    if lowered in haystack:
        return True
    chinese_segments = re.findall(r"[\u4e00-\u9fff]{2,}", lowered)
    return any(_longest_common_substring_length(segment, haystack) >= min(3, len(segment)) for segment in chinese_segments)


def _longest_common_substring_length(left: str, right: str) -> int:
    previous = [0] * (len(right) + 1)
    longest = 0
    for left_char in left:
        current = [0] * (len(right) + 1)
        for index, right_char in enumerate(right, start=1):
            if left_char == right_char:
                current[index] = previous[index - 1] + 1
                longest = max(longest, current[index])
        previous = current
    return longest


def assess_candidates(candidates: list[TopicSourceCandidate], intent: SearchIntent, client: NewApiClient | None, model: str, *, use_llm: bool) -> str | None:
    if not use_llm:
        return None
    if client is None:
        return "assessment_skipped_missing_api_key"
    try:
        assessments = client.assess_search_candidates(intent.to_dict(), [candidate.to_dict() for candidate in candidates], model)
    except NewApiError as exc:
        return f"assessment_fallback:{exc.code}"
    for candidate in candidates:
        assessment = assessments.get(candidate.candidate_id)
        if not assessment:
            continue
        semantic_score = int(assessment["relevance"])
        rule_score = candidate.quality_score
        candidate.quality_score = round(rule_score * 0.7 + semantic_score * 0.3)
        candidate.score_breakdown = {"rule": rule_score, "semantic": semantic_score, "final": candidate.quality_score}
        candidate.matched_facets = _unique(candidate.matched_facets + assessment["matched_facets"])[:3]
        candidate.risk_flags = _unique(candidate.risk_flags + assessment["risk_flags"])
        candidate.assessment_source = "rule+newapi"
        if assessment["reason"]:
            candidate.relevance_reason = assessment["reason"]
    candidates.sort(key=lambda candidate: (-candidate.quality_score, candidate.candidate_id))
    return None


def _diverse_rank(candidates: list[TopicSourceCandidate], max_candidates: int) -> list[TopicSourceCandidate]:
    ranked = sorted(candidates, key=lambda item: (-item.quality_score, item.candidate_id))
    provider_groups: dict[str, list[TopicSourceCandidate]] = {}
    for candidate in ranked:
        provider_groups.setdefault(candidate.providers[0] if candidate.providers else "unknown", []).append(candidate)
    result: list[TopicSourceCandidate] = []
    cap = max(1, int(max_candidates * 0.6))
    counts: dict[str, int] = {}
    for provider, group in provider_groups.items():
        for candidate in group[:3]:
            if len(result) >= max_candidates:
                break
            result.append(candidate)
            counts[provider] = counts.get(provider, 0) + 1
    for candidate in ranked:
        if len(result) >= max_candidates or candidate in result:
            continue
        provider = candidate.providers[0] if candidate.providers else "unknown"
        if len(provider_groups) > 1 and counts.get(provider, 0) >= cap:
            continue
        result.append(candidate)
        counts[provider] = counts.get(provider, 0) + 1
    return result


def _get_json(url: str, *, timeout_sec: int, headers: dict[str, str]) -> Any:
    request = urllib.request.Request(url, method="GET")
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise SearchProviderError(f"HTTP {exc.code}", code=f"http_{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise SearchProviderError(str(exc.reason), code="provider_unreachable") from exc
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SearchProviderError("响应不是合法 JSON", code="invalid_json") from exc


def _get_text(url: str, *, timeout_sec: int, headers: dict[str, str]) -> str:
    request = urllib.request.Request(url, method="GET")
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise SearchProviderError(f"HTTP {exc.code}", code=f"http_{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise SearchProviderError(str(exc.reason), code="provider_unreachable") from exc


def _cache_key(provider: str, queries: list[str], max_results: int) -> str:
    raw = json.dumps({"schema": 1, "provider": provider, "queries": queries, "max_results": max_results}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _strip_html(value: Any) -> str:
    return unescape(_TAG_RE.sub("", str(value))).strip()


def _is_bilibili_public_video_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.netloc.lower() in _BILIBILI_HOSTS and parts.path.startswith("/video/")


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _now() -> str:
    return datetime.now(UTC).isoformat()
