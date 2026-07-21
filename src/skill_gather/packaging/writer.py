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
        lines = [
            "# Failure Report",
            "",
            f"- source: {metadata.source}",
            f"- source_id: {metadata.source_id}",
            f"- package_status: {metadata.package_status}",
            f"- reason: {failure_reason}",
        ]
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

    skill_lines = [
        f"# {distillation.get('candidate_title', metadata.title or package_name)}",
        "",
        str(distillation.get("summary", "")).strip(),
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
    ]
    (target / "SKILL.md").write_text("\n".join(skill_lines).rstrip() + "\n", encoding="utf-8")

    readme_lines = [
        f"# {distillation.get('candidate_title', metadata.title or package_name)}",
        "",
        f"- source: {metadata.source} {metadata.source_id}",
        f"- package_status: {metadata.package_status}",
        f"- final_score: {metadata.scores.final_score}",
        "",
        str(distillation.get("summary", "")).strip(),
    ]
    (target / "README.md").write_text("\n".join(readme_lines).rstrip() + "\n", encoding="utf-8")
    write_json(target / "metadata.json", metadata.to_dict())
    write_json(target / "evidence_timeline.json", evidence_timeline.to_dict())
    return target


def _candidate_package_name(metadata: SkillPackageMetadata, distillation: dict[str, Any]) -> str:
    raw_title = str(distillation.get("candidate_title") or metadata.title or "skill-candidate")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw_title.lower()).strip("-") or "skill-candidate"
    suffix = hashlib.sha1(f"{metadata.source}:{metadata.source_id}".encode("utf-8")).hexdigest()[:6]
    return f"{slug[:48].strip('-')}-{suffix}"


def _markdown_value(value: Any) -> str:
    if isinstance(value, list):
        lines = [f"- {str(item).strip()}" for item in value if str(item).strip()]
        return "\n".join(lines)
    return str(value or "").strip()
