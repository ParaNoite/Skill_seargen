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
class FrameManifest:
    timestamp: str
    frame_path: str
    reason: str
    visual_type: str
    importance: float = 0.0
    ocr_density: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FrameManifest":
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

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EvidenceItem":
        return cls(**value)


@dataclass(slots=True)
class EvidenceTimeline:
    video_duration_sec: int
    frame_budget: int
    sampling_strategy: str
    items: list[EvidenceItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_duration_sec": self.video_duration_sec,
            "frame_budget": self.frame_budget,
            "sampling_strategy": self.sampling_strategy,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EvidenceTimeline":
        items = [EvidenceItem.from_dict(item) for item in value.get("items", [])]
        return cls(
            video_duration_sec=value["video_duration_sec"],
            frame_budget=value["frame_budget"],
            sampling_strategy=value["sampling_strategy"],
            items=items,
        )


@dataclass(slots=True)
class RunState:
    run_id: str
    source_id: str
    status: RunStatus = "created"
    current_stage: str = "manifest"
    completed_stages: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    failure_reason: str | None = None
    judge_difficulty: str = "standard"

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

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "ScoreResult":
        if not isinstance(value, dict):
            value = {}
        return cls(
            rule_score=value.get("rule_score", 0),
            llm_judge_score=value.get("llm_judge_score", 0),
            final_score=value.get("final_score", 0),
            final_status=value.get("final_status", "failed"),
            conflict_policy=value.get("conflict_policy", "conservative"),
        )


@dataclass(slots=True)
class SkillPackageMetadata:
    source: str
    source_id: str
    source_url: str
    title: str = ""
    author: str = ""
    generated_at: str = ""
    package_status: PackageStatus = "failed"
    models: dict[str, str] = field(default_factory=dict)
    evidence: list[EvidenceItem] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    scores: ScoreResult = field(
        default_factory=lambda: ScoreResult(
            rule_score=0,
            llm_judge_score=0,
            final_score=0,
            final_status="failed",
        )
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "title": self.title,
            "author": self.author,
            "generated_at": self.generated_at,
            "package_status": self.package_status,
            "models": dict(self.models),
            "evidence": [item.to_dict() for item in self.evidence],
            "risk_flags": list(self.risk_flags),
            "scores": self.scores.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SkillPackageMetadata":
        evidence = [EvidenceItem.from_dict(item) for item in value.get("evidence", [])]
        scores = ScoreResult.from_dict(
            value.get(
                "scores",
                {
                    "rule_score": 0,
                    "llm_judge_score": 0,
                    "final_score": 0,
                    "final_status": "failed",
                    "conflict_policy": "conservative",
                },
            )
        )
        return cls(
            source=value.get("source", ""),
            source_id=value.get("source_id", ""),
            source_url=value.get("source_url", ""),
            title=value.get("title", ""),
            author=value.get("author", ""),
            generated_at=value.get("generated_at", ""),
            package_status=value.get("package_status", "failed"),
            models=dict(value.get("models", {})),
            evidence=evidence,
            risk_flags=list(value.get("risk_flags", [])),
            scores=scores,
        )
