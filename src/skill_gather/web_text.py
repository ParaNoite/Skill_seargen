from __future__ import annotations

from dataclasses import asdict, dataclass
from html.parser import HTMLParser
import re
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


MAX_RESPONSE_BYTES = 2_000_000
MAX_SNAPSHOT_CHARS = 12_000


class WebTextError(RuntimeError):
    """A public webpage could not be fetched or converted into usable text."""


@dataclass(frozen=True, slots=True)
class WebPage:
    requested_url: str
    final_url: str
    title: str
    text: str
    content_type: str
    truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class _TextExtractor(HTMLParser):
    _ignored_tags = {"script", "style", "noscript", "svg", "template"}
    _block_tags = {
        "article", "blockquote", "br", "div", "h1", "h2", "h3", "h4",
        "h5", "h6", "li", "p", "section", "td", "th",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in self._ignored_tags:
            self._ignored_depth += 1
        elif lowered == "title":
            self._in_title = True
        elif lowered in self._block_tags:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self._ignored_tags and self._ignored_depth:
            self._ignored_depth -= 1
        elif lowered == "title":
            self._in_title = False
        elif lowered in self._block_tags:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
        self._parts.append(data)

    def extracted(self) -> tuple[str, str]:
        title = _compact(" ".join(self._title_parts))
        lines = [_compact(line) for line in "".join(self._parts).splitlines()]
        text = "\n".join(line for line in lines if line)
        return title, text


def fetch_public_page(url: str, *, timeout_sec: int = 15) -> WebPage:
    """Fetch a public HTML page without credentials, cookies, or browser state."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise WebTextError("网页来源必须是公开的 http 或 https URL")
    if parsed.username or parsed.password:
        raise WebTextError("网页来源 URL 不能包含用户名或密码")
    if timeout_sec <= 0:
        raise WebTextError("网页抓取超时必须是正整数")

    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "skill-seargen/0.5 (+local-public-web-fetch)",
        },
    )
    try:
        with urlopen(request, timeout=timeout_sec) as response:  # nosec B310: selected sources are explicitly confirmed by the user
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                raise WebTextError(f"网页响应不是 HTML：{content_type}")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            truncated = len(raw) > MAX_RESPONSE_BYTES
            raw = raw[:MAX_RESPONSE_BYTES]
            charset = response.headers.get_content_charset() or "utf-8"
            final_url = response.geturl()
    except HTTPError as exc:
        raise WebTextError(f"网页请求失败：HTTP {exc.code}") from exc
    except URLError as exc:
        raise WebTextError(f"网页请求失败：{exc.reason}") from exc
    except OSError as exc:
        raise WebTextError(f"网页请求失败：{exc}") from exc

    if urlparse(final_url).scheme not in {"http", "https"}:
        raise WebTextError("网页重定向到了不受支持的 URL 协议")
    try:
        html = raw.decode(charset, errors="replace")
    except LookupError:
        html = raw.decode("utf-8", errors="replace")
    extractor = _TextExtractor()
    extractor.feed(html)
    title, text = extractor.extracted()
    if len(text) < 80:
        raise WebTextError("网页正文不足，可能是登录页、动态页面或低质量来源")
    return WebPage(
        requested_url=url,
        final_url=final_url,
        title=title,
        text=text[:MAX_SNAPSHOT_CHARS],
        content_type=content_type,
        truncated=truncated or len(text) > MAX_SNAPSHOT_CHARS,
    )


def score_web_page(text: str, *, candidate_quality: int = 0) -> tuple[int, list[str]]:
    """Return a transparent, coarse quality score for a fetched text source."""
    length = len(text)
    score = min(max(candidate_quality, 0), 60)
    score += 15 if length >= 500 else 5
    score += 15 if length >= 1_500 else 0
    score += 10 if length >= 4_000 else 0
    risk_flags: list[str] = []
    if length < 500:
        risk_flags.append("web_text_short")
    if len(re.findall(r"\b(cookie|login|sign in|subscribe)\b", text, flags=re.IGNORECASE)) >= 3:
        risk_flags.append("possible_access_gate")
    return min(score, 100), risk_flags


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


WebFetcher = Callable[[str], WebPage]
