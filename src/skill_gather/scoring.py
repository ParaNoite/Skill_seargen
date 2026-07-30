from __future__ import annotations

from typing import Any

from .models import EvidenceTimeline
from .models import PackageStatus, ScoreResult


JUDGE_DIFFICULTIES = {"lenient", "standard", "strict", "off"}


def normalize_judge_difficulty(value: str | None) -> str:
    difficulty = str(value or "standard").strip().lower()
    if difficulty not in JUDGE_DIFFICULTIES:
        return "standard"
    return difficulty


def thresholds_for_difficulty(difficulty: str | None) -> tuple[int, int]:
    difficulty = normalize_judge_difficulty(difficulty)
    if difficulty == "lenient":
        return 80, 60
    if difficulty == "strict":
        return 90, 78
    return 85, 70


def status_for_score(score: int, *, difficulty: str | None = None) -> PackageStatus:
    passed_threshold, review_threshold = thresholds_for_difficulty(difficulty)
    if score >= passed_threshold:
        return "passed"
    if score >= review_threshold:
        return "needs_review"
    return "failed"


def conservative_score(
    rule_score: int,
    llm_judge_score: int,
    *,
    single_channel_evidence: bool = False,
    difficulty: str | None = None,
) -> ScoreResult:
    final_score = min(rule_score, llm_judge_score)
    final_status = status_for_score(final_score, difficulty=difficulty)

    if single_channel_evidence and final_status == "passed":
        final_status = "needs_review"

    return ScoreResult(
        rule_score=rule_score,
        llm_judge_score=llm_judge_score,
        final_score=final_score,
        final_status=final_status,
        conflict_policy=f"conservative:{normalize_judge_difficulty(difficulty)}",
    )


def rule_score_for_distillation(distillation: dict[str, Any], timeline: EvidenceTimeline) -> int:
    if distillation.get("status") != "distilled" or not timeline.items:
        return 0

    score = 15
    if str(distillation.get("candidate_title", "")).strip():
        score += 10
    if str(distillation.get("summary", "")).strip():
        score += 5

    ria = distillation.get("ria")
    if isinstance(ria, dict):
        for key in ["recall", "interpret", "apply", "boundary", "test"]:
            value = ria.get(key)
            if isinstance(value, list):
                has_content = any(str(item).strip() for item in value)
            else:
                has_content = bool(str(value or "").strip())
            if has_content:
                score += 10

    evidence_refs = distillation.get("evidence_refs", [])
    if isinstance(evidence_refs, list) and evidence_refs:
        score += 10

    return min(score, 100)


def has_single_channel_evidence(timeline: EvidenceTimeline) -> bool:
    channels: set[str] = set()
    for item in timeline.items:
        if item.type == "asr":
            channels.add("asr")
        else:
            channels.add("visual")
    return len(channels) == 1
