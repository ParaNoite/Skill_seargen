from __future__ import annotations

from .models import PackageStatus, ScoreResult


def status_for_score(score: int) -> PackageStatus:
    if score >= 85:
        return "passed"
    if score >= 70:
        return "needs_review"
    return "failed"


def conservative_score(
    rule_score: int,
    llm_judge_score: int,
    *,
    single_channel_evidence: bool = False,
) -> ScoreResult:
    final_score = min(rule_score, llm_judge_score)
    final_status = status_for_score(final_score)

    if single_channel_evidence and final_status == "passed":
        final_status = "needs_review"

    return ScoreResult(
        rule_score=rule_score,
        llm_judge_score=llm_judge_score,
        final_score=final_score,
        final_status=final_status,
    )
