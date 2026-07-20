from __future__ import annotations

from pathlib import Path

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
