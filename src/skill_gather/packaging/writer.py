from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from ..models import EvidenceTimeline, FrameManifest, RunState, SkillPackageMetadata
from ..runs import write_json


def write_audit_package(
    target_dir: str | Path,
    metadata: SkillPackageMetadata,
    *,
    run_state: RunState | None = None,
    evidence_timeline: EvidenceTimeline | None = None,
    frame_index: list[FrameManifest] | None = None,
    failure_reason: str | None = None,
) -> Path:
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)

    write_json(target / "metadata.json", metadata.to_dict())
    if run_state is not None:
        write_json(target / "run_state.json", run_state.to_dict())
    if evidence_timeline is not None:
        write_json(target / "evidence_timeline.json", evidence_timeline.to_dict())
    if frame_index is not None:
        write_json(
            target / "frame_index.json",
            [frame.to_dict() for frame in frame_index],
        )
    if failure_reason:
        lines = _failure_report_lines(
            metadata,
            failure_reason=failure_reason,
            run_state=run_state,
            evidence_timeline=evidence_timeline,
            frame_index=frame_index,
        )
        (target / "failure_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return target


def write_candidate_package(
    target_root: str | Path,
    metadata: SkillPackageMetadata,
    distillation: dict[str, Any],
    *,
    evidence_timeline: EvidenceTimeline,
) -> Path:
    package_name = _candidate_package_name(metadata, distillation)
    target = Path(target_root) / package_name
    target.mkdir(parents=True, exist_ok=True)

    ria = distillation.get("ria", {})
    if not isinstance(ria, dict):
        ria = {}
    title = str(distillation.get("candidate_title") or metadata.title or package_name)
    summary = str(distillation.get("summary", "")).strip()

    skill_lines = [
        f"# {title}",
        "",
        summary,
        "",
        "## Recall",
        "",
        _markdown_value(ria.get("recall")),
        "",
        "## Interpret",
        "",
        _markdown_value(ria.get("interpret")),
        "",
        "## Apply",
        "",
        _markdown_value(ria.get("apply")),
        "",
        "## Boundary",
        "",
        _markdown_value(ria.get("boundary")),
        "",
        "## Test",
        "",
        _markdown_value(ria.get("test")),
        "",
        "## Evidence Trace",
        "",
        *_evidence_markdown(evidence_timeline),
    ]
    (target / "SKILL.md").write_text("\n".join(skill_lines).rstrip() + "\n", encoding="utf-8")

    readme_lines = [
        f"# {title}",
        "",
        "## Review Summary",
        "",
        f"- source: {metadata.source} {metadata.source_id}",
        f"- source_url: {metadata.source_url}",
        f"- run_variant: {metadata.run_variant or 'default'}",
        f"- package_status: {metadata.package_status}",
        f"- final_score: {metadata.scores.final_score}",
        f"- rule_score: {metadata.scores.rule_score}",
        f"- llm_judge_score: {metadata.scores.llm_judge_score}",
        f"- conflict_policy: {metadata.scores.conflict_policy}",
        f"- risk_flags: {_comma_list(metadata.risk_flags)}",
        f"- evidence_items: {len(evidence_timeline.items)}",
        f"- frame_budget: {evidence_timeline.frame_budget}",
        f"- sampling_strategy: {evidence_timeline.sampling_strategy}",
        "",
        "## Summary",
        "",
        summary,
        "",
        "## Evidence Summary",
        "",
        *_evidence_markdown(evidence_timeline),
        "",
        "## Review Checklist",
        "",
        "- Confirm the candidate instructions are supported by the listed timestamped evidence.",
        "- Confirm risk flags are acceptable before installing or reusing this skill.",
        "- Re-run `skill-gather inspect <run-id>` when the original run directory is available.",
    ]
    (target / "README.md").write_text("\n".join(readme_lines).rstrip() + "\n", encoding="utf-8")
    write_json(target / "metadata.json", metadata.to_dict())
    write_json(target / "evidence_timeline.json", evidence_timeline.to_dict())
    return target


def _candidate_package_name(metadata: SkillPackageMetadata, distillation: dict[str, Any]) -> str:
    raw_title = str(distillation.get("candidate_title") or metadata.title or "skill-candidate")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw_title.lower()).strip("-") or "skill-candidate"
    identity = f"{metadata.source}:{metadata.source_id}"
    if metadata.run_variant:
        identity += f":{metadata.run_variant}"
    suffix = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:6]
    return f"{slug[:48].strip('-')}-{suffix}"


def _markdown_value(value: Any) -> str:
    if isinstance(value, list):
        lines = [f"- {str(item).strip()}" for item in value if str(item).strip()]
        return "\n".join(lines)
    return str(value or "").strip()


def _failure_report_lines(
    metadata: SkillPackageMetadata,
    *,
    failure_reason: str,
    run_state: RunState | None,
    evidence_timeline: EvidenceTimeline | None,
    frame_index: list[FrameManifest] | None,
) -> list[str]:
    completed_stages = run_state.completed_stages if run_state is not None else []
    evidence_items = evidence_timeline.items if evidence_timeline is not None else []
    frame_items = frame_index if frame_index is not None else []
    lines = [
        "# Failure Report",
        "",
        "## Summary",
        "",
        f"- source: {metadata.source} {metadata.source_id}".rstrip(),
        f"- source_url: {metadata.source_url}",
        f"- title: {metadata.title or 'unknown'}",
        f"- author: {metadata.author or 'unknown'}",
        f"- package_status: {metadata.package_status}",
        f"- run_id: {run_state.run_id if run_state is not None else 'unknown'}",
        f"- run_status: {run_state.status if run_state is not None else 'unknown'}",
        f"- current_stage: {run_state.current_stage if run_state is not None else 'unknown'}",
        f"- completed_stages: {_comma_list(completed_stages)}",
        f"- reason: {failure_reason}",
        f"- risk_flags: {_comma_list(metadata.risk_flags)}",
        f"- evidence_items: {len(evidence_items)}",
        f"- frame_index_items: {len(frame_items)}",
    ]
    if run_state is not None and run_state.artifacts:
        lines.extend(["", "## Artifacts", ""])
        for key in sorted(run_state.artifacts):
            lines.append(f"- {key}: {run_state.artifacts[key]}")

    lines.extend(["", "## Available Evidence", ""])
    lines.extend(_evidence_markdown(evidence_timeline or _empty_timeline()))

    if frame_items:
        lines.extend(["", "## Frame Index Sample", ""])
        for frame in frame_items[:5]:
            lines.append(
                f"- {frame.timestamp} [{frame.visual_type}] {frame.frame_path} "
                f"(reason={frame.reason})"
            )
        remaining = len(frame_items) - 5
        if remaining > 0:
            lines.append(f"- ... {remaining} more frame item(s) omitted from this summary.")

    lines.extend(
        [
            "",
            "## Suggested Rerun",
            "",
            "- Run `skill-gather inspect <run-id> --runs <runs-dir>` to review the preserved artifacts.",
            "- If the failure came from missing media, retry after confirming yt-dlp and ffmpeg are available.",
            "- If the failure came from missing model output, retry after confirming newapi credentials and model names.",
        ]
    )
    return lines


def _empty_timeline() -> EvidenceTimeline:
    return EvidenceTimeline(
        video_duration_sec=0,
        frame_budget=0,
        sampling_strategy="none",
        items=[],
    )


def _evidence_markdown(timeline: EvidenceTimeline, *, limit: int = 5) -> list[str]:
    if not timeline.items:
        return ["- No evidence items recorded."]

    lines = []
    for item in timeline.items[:limit]:
        lines.append(
            f"- {item.timestamp} [{item.type}] {_single_line(item.claim)} "
            f"(confidence={item.confidence:.2f})"
        )
    remaining = len(timeline.items) - limit
    if remaining > 0:
        lines.append(f"- ... {remaining} more evidence item(s) omitted from this summary.")
    return lines


def _comma_list(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def _single_line(value: str, *, max_length: int = 120) -> str:
    clean = " ".join(str(value).split())
    if len(clean) <= max_length:
        return clean
    return clean[: max_length - 1].rstrip() + "…"
