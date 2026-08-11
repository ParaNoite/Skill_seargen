from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import TopicSourceCandidate, TopicTask
from .runs import write_json


@dataclass(frozen=True, slots=True)
class ReleaseDecision:
    status: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reasons": list(self.reasons)}


def choose_auto_sources(task: TopicTask) -> list[str]:
    ranked = sorted(task.candidates, key=lambda item: (bool(item.risk_flags), -item.quality_score, item.candidate_id))
    selected: list[TopicSourceCandidate] = []
    preferred = ["web", "github", "video"] if task.mode == "technical" else ["web", "video"]
    for source_type in preferred:
        candidate = next((item for item in ranked if item.source_type == source_type), None)
        if candidate and len(selected) < task.budget.max_selected_sources:
            selected.append(candidate)
    for candidate in ranked:
        if len(selected) >= task.budget.max_selected_sources:
            break
        if candidate not in selected:
            selected.append(candidate)
    return [item.candidate_id for item in selected]


def evaluate_release_gate(task: TopicTask, fusion: dict[str, Any], score: dict[str, Any]) -> ReleaseDecision:
    reasons: list[str] = []
    budget_pairs = (
        (task.usage.candidate_count, task.budget.max_candidates),
        (task.usage.selected_source_count, task.budget.max_selected_sources),
        (task.usage.processed_video_duration_sec, task.budget.max_video_duration_sec),
        (task.usage.model_calls, task.budget.max_model_calls),
        (task.usage.estimated_cost_usd, task.budget.max_estimated_cost_usd),
        (task.usage.elapsed_runtime_sec, task.budget.max_runtime_sec),
    )
    if any(used > limit for used, limit in budget_pairs):
        reasons.append("预算超限")
    if len(task.selected_sources) < 2:
        reasons.append("缺少交叉来源")
    if fusion.get("conflicts"):
        reasons.append("存在来源冲突")
    if fusion.get("evidence_gaps"):
        reasons.append("存在证据缺口")
    if fusion.get("risk_flags"):
        reasons.append("存在风险标记")
    if task.failure_reason:
        reasons.append("外部服务或处理阶段失败")
    if int(score.get("final_score", 0) or 0) < 75:
        reasons.append("评分未达到标准阈值")
    if score.get("final_status") not in {"passed", None}:
        reasons.append("评分结论要求人工复核")
    return ReleaseDecision("passed" if not reasons else "needs_review", tuple(dict.fromkeys(reasons)))


def persist_release_gate(task: TopicTask, run_root: Path, fusion: dict[str, Any], score: dict[str, Any]) -> ReleaseDecision:
    decision = evaluate_release_gate(task, fusion, score)
    write_json(run_root / "release_gate.json", decision.to_dict())
    task.artifacts["release_gate"] = "release_gate.json"
    if task.execution_mode == "auto" and decision.status != "passed" and score.get("final_status") == "passed":
        score["final_status"] = "needs_review"
        score["release_gate_reasons"] = list(decision.reasons)
        if task.package and task.package.score:
            write_json(run_root / task.package.score, score)
    return decision


def persist_video_release_gate(run_root: Path, score: dict[str, Any]) -> ReleaseDecision:
    decision = ReleaseDecision("needs_review", ("单视频缺少交叉证据",))
    if score.get("final_status") == "passed":
        score["final_status"] = decision.status
        score["release_gate_reasons"] = list(decision.reasons)
        write_json(run_root / "score.json", score)
    write_json(run_root / "release_gate.json", decision.to_dict())
    return decision
