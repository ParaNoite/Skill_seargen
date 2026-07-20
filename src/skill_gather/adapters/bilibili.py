from __future__ import annotations

from typing import Any

from ..models import VideoSourceManifest
from ..source import SourceInfo


def build_initial_manifest(url: str, source: SourceInfo) -> VideoSourceManifest:
    return VideoSourceManifest(
        source=source.source,
        source_id=source.source_id,
        url=url,
        title="",
        author="",
        duration_sec=0,
        subtitle_available=False,
        media_access="public",
        risk_flags=["metadata_pending"],
    )


def manifest_from_yt_dlp_metadata(
    url: str,
    source: SourceInfo,
    metadata: dict[str, Any],
) -> VideoSourceManifest:
    subtitles = metadata.get("subtitles")
    automatic_captions = metadata.get("automatic_captions")
    subtitle_available = bool(subtitles) or bool(automatic_captions)
    risk_flags: list[str] = []
    if not subtitle_available:
        risk_flags.append("no_subtitle")
    if metadata.get("is_live"):
        risk_flags.append("live_or_stream")
    if metadata.get("playlist_count", 0):
        risk_flags.append("playlist_context")

    duration = metadata.get("duration") or 0
    return VideoSourceManifest(
        source=source.source,
        source_id=source.source_id,
        url=url,
        title=str(metadata.get("title") or ""),
        author=str(metadata.get("uploader") or metadata.get("channel") or ""),
        duration_sec=int(duration),
        subtitle_available=subtitle_available,
        media_access="public",
        risk_flags=risk_flags,
    )
