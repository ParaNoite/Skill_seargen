from __future__ import annotations

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
