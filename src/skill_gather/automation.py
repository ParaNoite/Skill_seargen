from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import TopicSourceCandidate, TopicTask


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
