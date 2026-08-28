from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


PackageStatus = Literal["passed", "needs_review", "failed"]
RunStatus = Literal["created", "running", "completed", "paused", "failed"]
TopicMode = Literal["normal", "technical"]
TopicStatus = Literal[
    "created",
    "planning",
    "awaiting_plan_confirmation",
    "searching",
    "awaiting_selection",
    "processing_sources",
    "generating",
    "scoring",
    "completed",
    "paused",
    "failed",
]


TOPIC_RUN_STATUSES: set[str] = {
    "created",
    "planning",
    "awaiting_plan_confirmation",
    "searching",
    "awaiting_selection",
    "processing_sources",
    "generating",
    "scoring",
    "completed",
    "paused",
    "failed",
}
JUDGE_DIFFICULTIES = ("lenient", "standard", "strict", "off")


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
    vision_mode: str = "full"
    vision_frame_limit: int = 12
    run_variant: str = ""
    execution_mode: str = "manual"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RunState":
        return cls(**value)


@dataclass(slots=True)
class TopicBudget:
    max_candidates: int = 20
    max_selected_sources: int = 5
    max_video_duration_sec: int = 3600
    max_model_calls: int = 100
    max_estimated_cost_usd: float = 10.0
    max_runtime_sec: int = 3600

    def __post_init__(self) -> None:
        positive_ints = {
            "max_candidates": self.max_candidates,
            "max_selected_sources": self.max_selected_sources,
            "max_video_duration_sec": self.max_video_duration_sec,
            "max_model_calls": self.max_model_calls,
            "max_runtime_sec": self.max_runtime_sec,
        }
        invalid = [name for name, value in positive_ints.items() if not isinstance(value, int) or value <= 0]
        if invalid or not isinstance(self.max_estimated_cost_usd, (int, float)) or self.max_estimated_cost_usd <= 0:
            raise ValueError("主题预算必须使用正数")
        if self.max_selected_sources > self.max_candidates:
            raise ValueError("最大处理来源数不能大于最大候选数")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "TopicBudget":
        return cls(**(value or {}))


@dataclass(slots=True)
class TopicCachePolicy:
    reuse_cache: bool = True
    refresh_cache: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "TopicCachePolicy":
        return cls(**(value or {}))


@dataclass(slots=True)
class SemanticPlanOption:
    option_id: str
    label: str
    goal: str
    exclusions: list[str] = field(default_factory=list)
    facets: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SemanticPlanOption":
        return cls(**value)


@dataclass(slots=True)
class SemanticPlan:
    ambiguous: bool
    ambiguity_reasons: list[str] = field(default_factory=list)
    options: list[SemanticPlanOption] = field(default_factory=list)
    recommended_option_id: str = ""
    selected_option_id: str | None = None
    goal: str = ""
    exclusions: list[str] = field(default_factory=list)
    facets: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    generation_method: str = "deterministic"
    audit_status: str = "pending"
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["options"] = [option.to_dict() for option in self.options]
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "SemanticPlan | None":
        if not isinstance(value, dict):
            return None
        return cls(
            ambiguous=bool(value.get("ambiguous", False)),
            ambiguity_reasons=list(value.get("ambiguity_reasons", [])),
            options=[SemanticPlanOption.from_dict(item) for item in value.get("options", [])],
            recommended_option_id=str(value.get("recommended_option_id", "")),
            selected_option_id=value.get("selected_option_id"),
            goal=str(value.get("goal", "")),
            exclusions=list(value.get("exclusions", [])),
            facets=list(value.get("facets", [])),
            queries=list(value.get("queries", [])),
            generation_method=str(value.get("generation_method", "deterministic")),
            audit_status=str(value.get("audit_status", "pending")),
            warning=str(value.get("warning", "")),
        )


@dataclass(slots=True)
class TopicUsage:
    candidate_count: int = 0
    selected_source_count: int = 0
    processed_video_duration_sec: int = 0
    model_calls: int = 0
    estimated_cost_usd: float = 0.0
    elapsed_runtime_sec: int = 0

    def __post_init__(self) -> None:
        nonnegative_ints = {
            "candidate_count": self.candidate_count,
            "selected_source_count": self.selected_source_count,
            "processed_video_duration_sec": self.processed_video_duration_sec,
            "model_calls": self.model_calls,
            "elapsed_runtime_sec": self.elapsed_runtime_sec,
        }
        if any(not isinstance(value, int) or value < 0 for value in nonnegative_ints.values()):
            raise ValueError("主题用量必须使用非负整数")
        if not isinstance(self.estimated_cost_usd, (int, float)) or self.estimated_cost_usd < 0:
            raise ValueError("主题费用估算必须使用非负数")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "TopicUsage":
        return cls(**(value or {}))


@dataclass(slots=True)
class TopicSourceCandidate:
    url: str
    title: str = ""
    summary: str = ""
    source_type: str = "unknown"
    query: str = ""
    relevance_reason: str = ""
    risk_flags: list[str] = field(default_factory=list)
    candidate_id: str = ""
    canonical_url: str = ""
    host: str = ""
    quality_score: int = 0
    providers: list[str] = field(default_factory=list)
    engines: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    duplicate_count: int = 0
    matched_facets: list[str] = field(default_factory=list)
    score_breakdown: dict[str, int] = field(default_factory=dict)
    assessment_source: str = "rule"
    selected: bool = False
    confirmed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TopicSourceCandidate":
        return cls(**value)


@dataclass(slots=True)
class TopicVideoRun:
    parent_run_id: str
    candidate_id: str
    child_run_id: str
    source_url: str
    status: str = "created"
    title: str = ""
    duration_sec: int = 0
    model_calls: int = 0
    evidence_path: str = ""
    failure_reason: str | None = None
    vision_status: str = ""
    vision_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TopicVideoRun":
        return cls(**value)


@dataclass(slots=True)
class TopicPackage:
    root: str
    sources: str
    evidence: str
    references: str
    course: str | None = None
    knowledge: str | None = None
    fusion: str | None = None
    skill: str | None = None
    score: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TopicPackage":
        return cls(**value)


@dataclass(slots=True)
class TopicTask:
    run_id: str
    topic: str
    mode: TopicMode = "normal"
    output_language: str = "zh-CN"
    status: TopicStatus = "created"
    current_stage: str = "created"
    budget: TopicBudget = field(default_factory=TopicBudget)
    usage: TopicUsage = field(default_factory=TopicUsage)
    cache: TopicCachePolicy = field(default_factory=TopicCachePolicy)
    judge_difficulty: str = "standard"
    execution_mode: str = "manual"
    plan: SemanticPlan | None = None
    plan_audit: list[dict[str, Any]] = field(default_factory=list)
    paused_from: str | None = None
    candidates: list[TopicSourceCandidate] = field(default_factory=list)
    selected_sources: list[TopicSourceCandidate] = field(default_factory=list)
    video_runs: list[TopicVideoRun] = field(default_factory=list)
    package: TopicPackage | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    failure_reason: str | None = None
    failure_stage: str | None = None

    def __post_init__(self) -> None:
        if not self.topic.strip():
            raise ValueError("主题不能为空")
        if self.mode not in {"normal", "technical"}:
            raise ValueError("主题模式必须是 normal 或 technical")
        if self.status not in TOPIC_RUN_STATUSES:
            raise ValueError("无效的主题任务状态")
        if self.judge_difficulty not in JUDGE_DIFFICULTIES:
            raise ValueError("无效的 Judge 难度")
        if self.execution_mode not in {"manual", "auto"}:
            raise ValueError("执行模式必须是 manual 或 auto")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "topic": self.topic,
            "mode": self.mode,
            "output_language": self.output_language,
            "status": self.status,
            "current_stage": self.current_stage,
            "budget": self.budget.to_dict(),
            "usage": self.usage.to_dict(),
            "cache": self.cache.to_dict(),
            "judge_difficulty": self.judge_difficulty,
            "execution_mode": self.execution_mode,
            "plan": self.plan.to_dict() if self.plan else None,
            "plan_audit": list(self.plan_audit),
            "paused_from": self.paused_from,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "selected_sources": [source.to_dict() for source in self.selected_sources],
            "video_runs": [video_run.to_dict() for video_run in self.video_runs],
            "package": self.package.to_dict() if self.package else None,
            "artifacts": dict(self.artifacts),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "failure_reason": self.failure_reason,
            "failure_stage": self.failure_stage,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "TopicTask":
        package_value = value.get("package")
        return cls(
            run_id=value["run_id"],
            topic=value["topic"],
            mode=value.get("mode", "normal"),
            output_language=value.get("output_language", "zh-CN"),
            status=value.get("status", "created"),
            current_stage=value.get("current_stage", "created"),
            budget=TopicBudget.from_dict(value.get("budget")),
            usage=TopicUsage.from_dict(value.get("usage")),
            cache=TopicCachePolicy.from_dict(value.get("cache")),
            judge_difficulty=value.get("judge_difficulty", "standard"),
            execution_mode=value.get("execution_mode", "manual"),
            plan=SemanticPlan.from_dict(value.get("plan")),
            plan_audit=list(value.get("plan_audit", [])),
            paused_from=value.get("paused_from"),
            candidates=[TopicSourceCandidate.from_dict(item) for item in value.get("candidates", [])],
            selected_sources=[TopicSourceCandidate.from_dict(item) for item in value.get("selected_sources", [])],
            video_runs=[TopicVideoRun.from_dict(item) for item in value.get("video_runs", [])],
            package=TopicPackage.from_dict(package_value) if isinstance(package_value, dict) else None,
            artifacts=dict(value.get("artifacts", {})),
            created_at=value.get("created_at", ""),
            updated_at=value.get("updated_at", ""),
            failure_reason=value.get("failure_reason"),
            failure_stage=value.get("failure_stage"),
        )


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
    run_variant: str = ""
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
            "run_variant": self.run_variant,
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
            run_variant=value.get("run_variant", ""),
            generated_at=value.get("generated_at", ""),
            package_status=value.get("package_status", "failed"),
            models=dict(value.get("models", {})),
            evidence=evidence,
            risk_flags=list(value.get("risk_flags", [])),
            scores=scores,
        )
