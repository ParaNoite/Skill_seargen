from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


BILIBILI_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv"}
BILIBILI_ID_RE = re.compile(r"(?P<id>BV[0-9A-Za-z]{10}|av\d+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SourceInfo:
    source: str
    source_id: str


class SourceInferenceError(ValueError):
    pass


def infer_source(url: str) -> SourceInfo:
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if host in BILIBILI_HOSTS or host.endswith(".bilibili.com"):
        source_id = extract_bilibili_id(url)
        return SourceInfo(source="bilibili", source_id=source_id)

    raise SourceInferenceError(
        "无法从 URL 自动判断来源；v0.1 只支持 B站公开视频 URL。"
    )


def extract_bilibili_id(url: str) -> str:
    match = BILIBILI_ID_RE.search(url)
    if match:
        return match.group("id")

    parsed = urlparse(url)
    if parsed.netloc.lower() == "b23.tv":
        return stable_short_link_id(parsed.path)

    raise SourceInferenceError("无法从 B站 URL 中解析 BV/av 视频 ID。")


def stable_short_link_id(path: str) -> str:
    cleaned = path.strip("/").split("/", maxsplit=1)[0]
    if not cleaned:
        raise SourceInferenceError("无法从 b23.tv 短链接中解析来源 ID。")
    return f"b23-{cleaned}"
