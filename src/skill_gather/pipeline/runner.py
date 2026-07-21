from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ..adapters.bilibili import manifest_from_yt_dlp_metadata
from ..config import AppConfig
from ..integrations import FfmpegClient, FfmpegError, YtDlpClient, YtDlpError
from ..models import (
    EvidenceTimeline,
    FrameManifest,
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


class MediaDownloader(Protocol):
    def download_media(self, url: str, target_dir: str | Path) -> dict[str, Any]:
        ...


class MediaProcessor(Protocol):
    def extract_audio(self, media_file: str | Path, target_path: str | Path) -> dict[str, Any]:
        ...

    def extract_frames(
        self,
        media_file: str | Path,
        target_dir: str | Path,
        *,
        interval_sec: int = 10,
    ) -> dict[str, Any]:
        ...


def run_video_pipeline(
    *,
    config: AppConfig,
    store: RunStore,
    state: RunState,
    manifest: VideoSourceManifest,
    out_dir: str | Path,
    metadata_probe: MetadataProbe | None = None,
    media_downloader: MediaDownloader | None = None,
    media_processor: MediaProcessor | None = None,
) -> RunState:
    if state.status in {"completed", "failed"} and state.completed_stages == PIPELINE_STAGES:
        return state

    state.status = "running"
    state.failure_reason = None
    store.save(state)

    probe = metadata_probe or YtDlpClient()
    downloader = media_downloader or YtDlpClient()
    processor = media_processor or FfmpegClient()
    _run_regular_stage(
        store,
        state,
        "manifest",
        lambda: _probe_manifest(store, state, manifest, probe),
    )
    _run_regular_stage(
        store,
        state,
        "media_extract",
        lambda: _write_media_extract(store, state, downloader),
    )
    _run_regular_stage(
        store,
        state,
        "frame_extract",
        lambda: _write_frame_extract(store, state, processor),
    )
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
            _write_media_probe(store, state, "metadata_available", "")
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


def _write_media_extract(
    store: RunStore,
    state: RunState,
    media_downloader: MediaDownloader,
) -> None:
    path = store.run_path(state.run_id) / "media_extract.json"
    existing = read_json(path) if path.exists() else {}
    if existing.get("status") not in {"downloaded", "failed"}:
        manifest = VideoSourceManifest.from_dict(read_json(store.manifest_path(state.run_id)))
        probe_path = store.run_path(state.run_id) / "media_probe.json"
        if "metadata_probe_failed" in manifest.risk_flags:
            reason = "metadata probe failed; media extraction was not attempted"
            write_json(path, {"status": "skipped", "reason": reason, "requires": ["yt-dlp"]})
        elif probe_path.exists() and read_json(probe_path).get("status") == "metadata_available":
            media_dir = store.run_path(state.run_id) / "media"
            try:
                result = media_downloader.download_media(manifest.url, media_dir)
            except YtDlpError as exc:
                manifest.risk_flags.append("media_download_failed")
                store.save_manifest(state.run_id, manifest)
                write_json(
                    path,
                    {
                        "status": "failed",
                        "reason_code": exc.code,
                        "returncode": exc.returncode,
                        "summary": exc.safe_summary,
                        "target_dir": str(media_dir),
                    },
                )
            else:
                write_json(path, media_download_record(result))
        else:
            reason = "external media extraction is not implemented yet"
            write_json(path, {"status": "skipped", "reason": reason, "requires": ["yt-dlp"]})
    state.artifacts["media_extract"] = str(path)


def media_download_record(result: dict[str, Any]) -> dict[str, Any]:
    media_files = result.get("media_files", [])
    if not isinstance(media_files, list):
        media_files = []
    return {
        "status": "downloaded",
        "target_dir": str(result.get("target_dir", "")),
        "output_template": str(result.get("output_template", "")),
        "media_files": [str(path) for path in media_files],
        "returncode": result.get("returncode", 0),
    }


def _write_frame_extract(
    store: RunStore,
    state: RunState,
    media_processor: MediaProcessor,
) -> None:
    path = store.run_path(state.run_id) / "frame_extract.json"
    existing = read_json(path) if path.exists() else {}
    if existing.get("status") in {"extracted", "failed"}:
        state.artifacts["frame_extract"] = str(path)
        return

    media_extract = read_json(store.run_path(state.run_id) / "media_extract.json")
    media_files = media_extract.get("media_files", [])
    if not isinstance(media_files, list) or not media_files:
        write_json(
            path,
            {
                "status": "skipped",
                "reason": "no downloaded media file is available",
            },
        )
        state.artifacts["frame_extract"] = str(path)
        return

    media_file = resolve_media_file(media_files[0], media_extract, store.run_path(state.run_id))
    if not media_file.exists():
        write_json(
            path,
            {
                "status": "failed",
                "reason_code": "media_file_missing",
                "source_media": str(media_file),
            },
        )
        state.artifacts["frame_extract"] = str(path)
        return

    audio_path = store.run_path(state.run_id) / "audio.wav"
    audio_record_path = store.run_path(state.run_id) / "audio.json"
    frames_dir = store.run_path(state.run_id) / "frames"
    try:
        audio_result = media_processor.extract_audio(media_file, audio_path)
    except FfmpegError as exc:
        audio_result = {
            "status": "failed",
            "reason_code": exc.code,
            "returncode": exc.returncode,
            "summary": exc.safe_summary,
            "audio_path": str(audio_path),
            "source_media": str(media_file),
        }
        write_json(audio_record_path, audio_result)
        write_json(
            path,
            {
                "status": "failed",
                "reason_code": exc.code,
                "returncode": exc.returncode,
                "summary": exc.safe_summary,
                "source_media": str(media_file),
            },
        )
        state.artifacts["audio"] = str(audio_record_path)
        state.artifacts["frame_extract"] = str(path)
        return
    else:
        write_json(audio_record_path, audio_result)
        state.artifacts["audio"] = str(audio_record_path)

    try:
        frame_result = media_processor.extract_frames(media_file, frames_dir, interval_sec=10)
    except FfmpegError as exc:
        write_json(
            path,
            {
                "status": "failed",
                "reason_code": exc.code,
                "returncode": exc.returncode,
                "summary": exc.safe_summary,
                "source_media": str(media_file),
                "audio_path": str(audio_path),
            },
        )
        manifest = VideoSourceManifest.from_dict(read_json(store.manifest_path(state.run_id)))
        manifest.risk_flags.append("frame_extract_failed")
        store.save_manifest(state.run_id, manifest)
        state.artifacts["frame_extract"] = str(path)
        return

    frame_paths = frame_result.get("frame_paths", [])
    frame_entries: list[FrameManifest] = []
    if isinstance(frame_paths, list):
        for index, frame_path in enumerate(frame_paths):
            frame_entries.append(
                FrameManifest(
                    timestamp=seconds_to_timestamp(index * int(frame_result.get("interval_sec", 10))),
                    frame_path=str(frame_path),
                    reason="ffmpeg_interval",
                    visual_type="video_frame",
                )
            )

    write_json(path, frame_result | {"status": "extracted", "frame_count": len(frame_entries)})
    write_json(
        store.frame_index_path(state.run_id),
        [frame.to_dict() for frame in frame_entries],
    )
    state.artifacts["frame_extract"] = str(path)
    state.artifacts["frame_index"] = str(store.frame_index_path(state.run_id))


def resolve_media_file(raw_media_file: Any, media_extract: dict[str, Any], run_dir: Path) -> Path:
    media_file = Path(str(raw_media_file))
    if media_file.is_absolute():
        return media_file

    candidates = [media_file, run_dir / media_file]
    target_dir = media_extract.get("target_dir")
    if target_dir:
        target = Path(str(target_dir))
        candidates.extend([target / media_file, target / media_file.name])

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]


def seconds_to_timestamp(total_seconds: int) -> str:
    hours, remainder = divmod(max(total_seconds, 0), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


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
    media_extract_path = store.run_path(state.run_id) / "media_extract.json"
    if media_extract_path.exists():
        media_extract = read_json(media_extract_path)
    else:
        media_extract = {}
    if media_probe.get("status") == "failed":
        reason_code = media_probe.get("reason_code", "metadata_probe_failed")
        state.failure_reason = f"metadata probe failed: {reason_code}"
    elif media_extract.get("status") == "failed":
        reason_code = media_extract.get("reason_code", "media_download_failed")
        state.failure_reason = f"media extraction failed: {reason_code}"
    else:
        state.failure_reason = (
            "insufficient evidence: media, ASR, and vision stages are not implemented yet"
        )
    state.artifacts["candidate_out_dir"] = str(Path(out_dir))
    _mark_completed(store, state, "package")
    frame_index = read_frame_index(store, state.run_id)
    package_path = write_audit_package(
        store.run_path(state.run_id),
        metadata,
        run_state=state,
        evidence_timeline=timeline,
        frame_index=frame_index,
        failure_reason=state.failure_reason,
    )
    state.artifacts["package"] = str(package_path)
    store.save(state)


def read_frame_index(store: RunStore, run_id: str) -> list[FrameManifest]:
    path = store.frame_index_path(run_id)
    if not path.exists():
        return []
    raw = read_json(path)
    if not isinstance(raw, list):
        return []
    return [FrameManifest.from_dict(item) for item in raw if isinstance(item, dict)]
