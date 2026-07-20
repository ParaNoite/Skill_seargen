from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ..adapters.bilibili import manifest_from_yt_dlp_metadata
from ..config import AppConfig
from ..integrations import YtDlpClient, YtDlpError
from ..models import (
    EvidenceTimeline,
    PIPELINE_STAGES,
    RunState,
    ScoreResult,
    SkillPackageMetadata,
    VideoSourceManifest,
)
from ..packaging import write_audit_package
from ..runs import RunStore, read_json, write_json
from ..scoring import conservative_score
from ..source import SourceInfo


class MetadataProbe(Protocol):
    def probe_metadata(self, url: str) -> dict[str, Any]:
        ...


def run_video_pipeline(
    *,
    config: AppConfig,
    store: RunStore,
    state: RunState,
    manifest: VideoSourceManifest,
    out_dir: str | Path,
    metadata_probe: MetadataProbe | None = None,
) -> RunState:
    if state.status in {"completed", "failed"} and state.completed_stages == PIPELINE_STAGES:
        return state

    state.status = "running"
    state.failure_reason = None
    store.save(state)

    probe = metadata_probe or YtDlpClient()
    _run_regular_stage(
        store,
        state,
        "manifest",
        lambda: _probe_manifest(store, state, manifest, probe),
    )
    _run_regular_stage(store, state, "media_extract", lambda: _write_media_extract(store, state))
    _run_regular_stage(store, state, "frame_extract", lambda: _write_frame_index(store, state))
    _run_regular_stage(store, state, "asr", lambda: _write_asr_result(store, state))
    _run_regular_stage(store, state, "vision_ocr", lambda: _write_vision_result(store, state))
    _run_regular_stage(store, state, "timeline_merge", lambda: _write_evidence_timeline(store, state))
    _run_regular_stage(store, state, "distill", lambda: _write_distillation(store, state))
    _run_regular_stage(store, state, "score", lambda: _write_score(store, state))
    _run_package_stage(config, store, state, manifest, out_dir)
    return state


def _run_regular_stage(
    store: RunStore,
    state: RunState,
    stage: str,
    action: Callable[[], None],
) -> None:
    if stage in state.completed_stages:
        return

    state.current_stage = stage
    store.save(state)
    action()
    _mark_completed(store, state, stage)


def _mark_completed(store: RunStore, state: RunState, stage: str) -> None:
    if stage not in state.completed_stages:
        state.completed_stages.append(stage)
    next_index = PIPELINE_STAGES.index(stage) + 1
    if next_index < len(PIPELINE_STAGES):
        state.current_stage = PIPELINE_STAGES[next_index]
    else:
        state.current_stage = stage
    store.save(state)


def _probe_manifest(
    store: RunStore,
    state: RunState,
    manifest: VideoSourceManifest,
    metadata_probe: MetadataProbe,
) -> None:
    path = store.manifest_path(state.run_id)
    if path.exists():
        manifest = VideoSourceManifest.from_dict(read_json(path))
        if "metadata_pending" not in manifest.risk_flags:
            state.artifacts["manifest"] = str(path)
            return

    try:
        metadata = metadata_probe.probe_metadata(manifest.url)
    except YtDlpError as exc:
        manifest.risk_flags = _without_flag(manifest.risk_flags, "metadata_pending")
        manifest.risk_flags.append("metadata_probe_failed")
        store.save_manifest(state.run_id, manifest)
        _write_media_probe(
            store,
            state,
            "failed",
            exc.code,
            returncode=exc.returncode,
            summary=exc.safe_summary,
        )
    else:
        source = SourceInfo(source=manifest.source, source_id=manifest.source_id)
        updated = manifest_from_yt_dlp_metadata(manifest.url, source, metadata)
        store.save_manifest(state.run_id, updated)
        _write_media_probe(store, state, "metadata_available", "")
    state.artifacts["manifest"] = str(path)


def _without_flag(flags: list[str], flag: str) -> list[str]:
    return [value for value in flags if value != flag]


def _write_media_probe(
    store: RunStore,
    state: RunState,
    status: str,
    reason_code: str,
    *,
    returncode: int | None = None,
    summary: str = "",
) -> Path:
    path = store.run_path(state.run_id) / "media_probe.json"
    payload = {"status": status}
    if reason_code:
        payload["reason_code"] = reason_code
    if returncode is not None:
        payload["returncode"] = returncode
    if summary:
        payload["summary"] = summary
    write_json(path, payload)
    state.artifacts["media_probe"] = str(path)
    return path


def _write_media_extract(store: RunStore, state: RunState) -> None:
    path = store.run_path(state.run_id) / "media_extract.json"
    if not path.exists():
        manifest = VideoSourceManifest.from_dict(read_json(store.manifest_path(state.run_id)))
        probe_path = store.run_path(state.run_id) / "media_probe.json"
        if "metadata_probe_failed" in manifest.risk_flags:
            reason = "metadata probe failed; media extraction was not attempted"
        elif probe_path.exists():
            reason = "metadata available; full media download is not implemented yet"
        else:
            reason = "external media extraction is not implemented yet"
        write_json(path, {"status": "skipped", "reason": reason, "requires": ["yt-dlp"]})
    state.artifacts["media_extract"] = str(path)


def _write_frame_index(store: RunStore, state: RunState) -> None:
    path = store.frame_index_path(state.run_id)
    if not path.exists():
        write_json(path, [])
    state.artifacts["frame_index"] = str(path)


def _write_asr_result(store: RunStore, state: RunState) -> None:
    path = store.run_path(state.run_id) / "asr.json"
    if not path.exists():
        write_json(
            path,
            {
                "status": "skipped",
                "reason": "ASR integration is not implemented yet",
                "items": [],
            },
        )
    state.artifacts["asr"] = str(path)


def _write_vision_result(store: RunStore, state: RunState) -> None:
    path = store.run_path(state.run_id) / "vision_ocr.json"
    if not path.exists():
        write_json(
            path,
            {
                "status": "skipped",
                "reason": "vision/OCR integration is not implemented yet",
                "items": [],
            },
        )
    state.artifacts["vision_ocr"] = str(path)


def _write_evidence_timeline(store: RunStore, state: RunState) -> None:
    path = store.evidence_timeline_path(state.run_id)
    if not path.exists():
        manifest = VideoSourceManifest.from_dict(read_json(store.manifest_path(state.run_id)))
        timeline = EvidenceTimeline(
            video_duration_sec=manifest.duration_sec,
            frame_budget=0,
            sampling_strategy="not_started",
            items=[],
        )
        store.save_evidence_timeline(state.run_id, timeline)
    state.artifacts["evidence_timeline"] = str(path)


def _write_distillation(store: RunStore, state: RunState) -> None:
    path = store.run_path(state.run_id) / "distillation.json"
    if not path.exists():
        write_json(
            path,
            {
                "status": "skipped",
                "reason": "no evidence items are available for RIA++ distillation",
                "candidate_title": "",
            },
        )
    state.artifacts["distillation"] = str(path)


def _write_score(store: RunStore, state: RunState) -> None:
    path = store.run_path(state.run_id) / "score.json"
    if not path.exists():
        score = conservative_score(rule_score=0, llm_judge_score=0)
        write_json(path, score.to_dict())
    state.artifacts["score"] = str(path)


def _run_package_stage(
    config: AppConfig,
    store: RunStore,
    state: RunState,
    manifest: VideoSourceManifest,
    out_dir: str | Path,
) -> None:
    if "package" in state.completed_stages:
        return

    state.current_stage = "package"
    manifest = VideoSourceManifest.from_dict(read_json(store.manifest_path(state.run_id)))
    score = ScoreResult.from_dict(read_json(store.run_path(state.run_id) / "score.json"))
    timeline = EvidenceTimeline.from_dict(read_json(store.evidence_timeline_path(state.run_id)))
    metadata = SkillPackageMetadata(
        source=manifest.source,
        source_id=manifest.source_id,
        source_url=manifest.url,
        title=manifest.title,
        author=manifest.author,
        generated_at=datetime.now(UTC).isoformat(),
        package_status=score.final_status,
        models={
            "vision": config.newapi.vision_model,
            "asr": config.newapi.asr_model,
            "distiller": config.newapi.distiller_model,
            "judge": config.newapi.judge_model,
        },
        evidence=timeline.items,
        risk_flags=[*manifest.risk_flags, "insufficient_evidence"],
        scores=score,
    )

    state.status = "failed"
    media_probe_path = store.run_path(state.run_id) / "media_probe.json"
    if media_probe_path.exists():
        media_probe = read_json(media_probe_path)
    else:
        media_probe = {}
    if media_probe.get("status") == "failed":
        reason_code = media_probe.get("reason_code", "metadata_probe_failed")
        state.failure_reason = f"metadata probe failed: {reason_code}"
    else:
        state.failure_reason = (
            "insufficient evidence: media, ASR, and vision stages are not implemented yet"
        )
    state.artifacts["candidate_out_dir"] = str(Path(out_dir))
    _mark_completed(store, state, "package")
    package_path = write_audit_package(
        store.run_path(state.run_id),
        metadata,
        run_state=state,
        evidence_timeline=timeline,
        frame_index=[],
        failure_reason=state.failure_reason,
    )
    state.artifacts["package"] = str(package_path)
    store.save(state)
