from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


PackageStatus = Literal["passed", "needs_review", "failed"]
RunStatus = Literal["created", "running", "completed", "failed"]


PIPELINE_STAGES = [
    "manifest",
    "media_extract",
    "frame_extract",
    "asr",
    "vision_ocr",
    "timeline_merge",
    "distill",
    "score",
    "package",
]


@dataclass(slots=True)
class VideoSourceManifest:
    source: str
    source_id: str
    url: str
    title: str = ""
    author: str = ""
    duration_sec: int = 0
    subtitle_available: bool = False
    media_access: str = "public"
    risk_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "VideoSourceManifest":
        return cls(**value)


@dataclass(slots=True)
class EvidenceItem:
    timestamp: str
    type: str
    claim: str
    raw_excerpt: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvidenceTimeline:
    video_duration_sec: int
    frame_budget: int
    sampling_strategy: str
    items: list[EvidenceItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["items"] = [item.to_dict() for item in self.items]
        return data


@dataclass(slots=True)
class RunState:
    run_id: str
    source_id: str
    status: RunStatus = "created"
    current_stage: str = "manifest"
    completed_stages: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RunState":
        return cls(**value)


@dataclass(slots=True)
class ScoreResult:
    rule_score: int
    llm_judge_score: int
    final_score: int
    final_status: PackageStatus
    conflict_policy: str = "conservative"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
