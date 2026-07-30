from __future__ import annotations

import os
from contextlib import contextmanager
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

from ..adapters.bilibili import manifest_from_yt_dlp_metadata
from ..config import AppConfig
from ..integrations import (
    FasterWhisperClient,
    FfmpegClient,
    FfmpegError,
    NewApiClient,
    NewApiError,
    YtDlpClient,
    YtDlpError,
)
from ..models import (
    EvidenceTimeline,
    EvidenceItem,
    FrameManifest,
    PIPELINE_STAGES,
    RunState,
    ScoreResult,
    SkillPackageMetadata,
    VideoSourceManifest,
)
from ..packaging import write_audit_package, write_candidate_package
from ..runs import RunStore, read_json, write_json
from ..scoring import conservative_score, has_single_channel_evidence, normalize_judge_difficulty, rule_score_for_distillation
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


class AsrClient(Protocol):
    def transcribe_audio(self, audio_file: str | Path, model: str) -> dict[str, Any]:
        ...


class VisionClient(Protocol):
    def analyze_frame(self, frame_file: str | Path, model: str) -> dict[str, Any]:
        ...


class DistillerClient(Protocol):
    def distill_skill(
        self,
        evidence_timeline: dict[str, Any],
        manifest: dict[str, Any],
        model: str,
    ) -> dict[str, Any]:
        ...


class JudgeClient(Protocol):
    def judge_skill(
        self,
        distillation: dict[str, Any],
        evidence_timeline: dict[str, Any],
        manifest: dict[str, Any],
        model: str,
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
    asr_client: AsrClient | None = None,
    vision_client: VisionClient | None = None,
    distiller_client: DistillerClient | None = None,
    judge_client: JudgeClient | None = None,
    judge_difficulty: str = "standard",
) -> RunState:
    if state.status in {"completed", "failed"} and state.completed_stages == PIPELINE_STAGES:
        return state

    state.status = "running"
    state.failure_reason = None
    state.judge_difficulty = normalize_judge_difficulty(judge_difficulty)
    store.save(state)

    probe = metadata_probe or YtDlpClient()
    downloader = media_downloader or YtDlpClient()
    processor = media_processor or FfmpegClient()
    newapi = NewApiClient.from_config(config.newapi)
    asr = asr_client or _default_asr_client(config, newapi)
    vision = vision_client or newapi
    distiller = distiller_client or newapi
    judge = judge_client or newapi
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
    _run_regular_stage(
        store,
        state,
        "asr",
        lambda: _write_asr_result(store, state, config, asr),
    )
    _run_regular_stage(
        store,
        state,
        "vision_ocr",
        lambda: _write_vision_result(store, state, config, vision),
    )
    _run_regular_stage(store, state, "timeline_merge", lambda: _write_evidence_timeline(store, state))
    _run_regular_stage(
        store,
        state,
        "distill",
        lambda: _write_distillation(store, state, config, distiller),
    )
    _run_regular_stage(store, state, "score", lambda: _write_score(store, state, config, judge, state.judge_difficulty))
    _run_package_stage(config, store, state, manifest, out_dir)
    return state


def _default_asr_client(config: AppConfig, newapi: NewApiClient | None) -> AsrClient | None:
    return FasterWhisperClient.from_model(config.newapi.asr_model)


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
    with _stage_timer(store, state, stage):
        action()
        _mark_completed(store, state, stage)


@contextmanager
def _stage_timer(store: RunStore, state: RunState, stage: str):
    started_at = datetime.now(UTC)
    started = perf_counter()
    try:
        yield
    except Exception as exc:
        _write_stage_timing(
            store,
            state,
            stage,
            started_at=started_at,
            started=started,
            status="error",
            error_type=type(exc).__name__,
        )
        raise
    else:
        _write_stage_timing(
            store,
            state,
            stage,
            started_at=started_at,
            started=started,
            status="completed",
        )


def _write_stage_timing(
    store: RunStore,
    state: RunState,
    stage: str,
    *,
    started_at: datetime,
    started: float,
    status: str,
    error_type: str = "",
) -> None:
    finished_at = datetime.now(UTC)
    path = store.run_path(state.run_id) / "stage_timings.json"
    if path.exists():
        payload = read_json(path)
    else:
        payload = {"run_id": state.run_id, "stages": []}

    existing = payload.get("stages", [])
    stages = [item for item in existing if isinstance(item, dict) and item.get("stage") != stage]
    record = {
        "stage": stage,
        "status": status,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_ms": round((perf_counter() - started) * 1000, 3),
    }
    if error_type:
        record["error_type"] = error_type
    stages.append(record)
    order = {name: index for index, name in enumerate(PIPELINE_STAGES)}
    stages.sort(key=lambda item: order.get(str(item.get("stage", "")), len(order)))
    payload = {
        "run_id": state.run_id,
        "stages": stages,
        "total_duration_ms": round(sum(float(item.get("duration_ms", 0)) for item in stages), 3),
    }
    write_json(path, payload)
    state.artifacts["stage_timings"] = str(path)
    store.save(state)


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


def _append_manifest_risk(store: RunStore, state: RunState, flag: str) -> None:
    manifest = VideoSourceManifest.from_dict(read_json(store.manifest_path(state.run_id)))
    if flag not in manifest.risk_flags:
        manifest.risk_flags.append(flag)
    store.save_manifest(state.run_id, manifest)


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


def _write_asr_result(
    store: RunStore,
    state: RunState,
    config: AppConfig,
    asr_client: AsrClient | None,
) -> None:
    path = store.run_path(state.run_id) / "asr.json"
    if not path.exists():
        audio_path = store.run_path(state.run_id) / "audio.json"
        if not audio_path.exists():
            write_json(
                path,
                {
                    "status": "skipped",
                    "reason": "audio extraction is not available yet",
                    "items": [],
                },
            )
        else:
            audio = read_json(audio_path)
            if audio.get("status") != "extracted":
                write_json(
                    path,
                    {
                        "status": "skipped",
                        "reason": "audio is not ready for transcription",
                        "items": [],
                    },
                )
            elif asr_client is None:
                write_json(
                    path,
                    {
                        "status": "failed",
                        "reason_code": "local_asr_unavailable",
                        "summary": "local faster-whisper ASR client is not available",
                        "requires": ["faster-whisper"],
                        "model": config.newapi.asr_model,
                        "audio_path": audio.get("audio_path", ""),
                        "items": [],
                    },
                )
            else:
                try:
                    result = asr_client.transcribe_audio(
                        audio.get("audio_path", ""),
                        config.newapi.asr_model,
                    )
                except NewApiError as exc:
                    manifest = VideoSourceManifest.from_dict(read_json(store.manifest_path(state.run_id)))
                    manifest.risk_flags.append("asr_failed")
                    store.save_manifest(state.run_id, manifest)
                    write_json(
                        path,
                        {
                            "status": "failed",
                            "reason_code": exc.code,
                            "returncode": exc.status_code,
                            "summary": exc.safe_summary,
                            "audio_path": audio.get("audio_path", ""),
                            "model": config.newapi.asr_model,
                            "items": [],
                        },
                    )
                else:
                    result["audio_path"] = str(audio.get("audio_path", result.get("audio_path", "")))
                    result["model"] = config.newapi.asr_model
                    write_json(path, result)
    state.artifacts["asr"] = str(path)


def _write_vision_result(
    store: RunStore,
    state: RunState,
    config: AppConfig,
    vision_client: VisionClient | None,
) -> None:
    path = store.run_path(state.run_id) / "vision_ocr.json"
    if not path.exists():
        frames = read_frame_index(store, state.run_id)
        if not frames:
            write_json(
                path,
                {
                    "status": "skipped",
                    "reason": "no frame index is available",
                    "items": [],
                    "errors": [],
                },
            )
        elif vision_client is None:
            _append_manifest_risk(store, state, "vision_ocr_skipped")
            write_json(
                path,
                {
                    "status": "skipped",
                    "reason": "newapi API key is not configured",
                    "requires": ["newapi"],
                    "model": config.newapi.vision_model,
                    "items": [],
                    "errors": [],
                },
            )
        else:
            items: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            original_frame_count = len(frames)
            frame_limit = _vision_frame_limit()
            selected_frames = frames[:frame_limit] if frame_limit is not None else frames
            if frame_limit is not None and original_frame_count > len(selected_frames):
                _append_manifest_risk(store, state, "vision_frame_limit_applied")
            for frame in selected_frames:
                try:
                    result = vision_client.analyze_frame(frame.frame_path, config.newapi.vision_model)
                except NewApiError as exc:
                    errors.append(
                        {
                            "timestamp": frame.timestamp,
                            "frame_path": frame.frame_path,
                            "reason_code": exc.code,
                            "returncode": exc.status_code,
                            "summary": exc.safe_summary,
                        }
                    )
                else:
                    items.append(
                        {
                            "timestamp": frame.timestamp,
                            "frame_path": frame.frame_path,
                            "status": result.get("status", "analyzed"),
                            "model": config.newapi.vision_model,
                            "observations": result.get("observations", []),
                        }
                    )
            status = "analyzed"
            if errors and items:
                status = "partial"
                _append_manifest_risk(store, state, "vision_ocr_partial")
            elif errors:
                status = "failed"
                _append_manifest_risk(store, state, "vision_ocr_failed")
            write_json(
                path,
                {
                    "status": status,
                    "model": config.newapi.vision_model,
                    "frame_count": original_frame_count,
                    "analyzed_frame_count": len(selected_frames),
                    "items": items,
                    "errors": errors,
                },
            )
    state.artifacts["vision_ocr"] = str(path)


def _vision_frame_limit() -> int | None:
    raw = os.getenv("SKILL_GATHER_VISION_FRAME_LIMIT", "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _write_evidence_timeline(store: RunStore, state: RunState) -> None:
    path = store.evidence_timeline_path(state.run_id)
    if not path.exists():
        manifest = VideoSourceManifest.from_dict(read_json(store.manifest_path(state.run_id)))
        asr_path = store.run_path(state.run_id) / "asr.json"
        asr_items = asr_evidence_items(read_json(asr_path)) if asr_path.exists() else []
        vision_path = store.run_path(state.run_id) / "vision_ocr.json"
        vision_items = vision_evidence_items(read_json(vision_path)) if vision_path.exists() else []
        metadata_items = metadata_evidence_items(manifest)
        items = sorted([*metadata_items, *asr_items, *vision_items], key=lambda item: item.timestamp)
        timeline = EvidenceTimeline(
            video_duration_sec=manifest.duration_sec,
            frame_budget=len(read_frame_index(store, state.run_id)),
            sampling_strategy="ffmpeg_interval_10s",
            items=items,
        )
        store.save_evidence_timeline(state.run_id, timeline)
    state.artifacts["evidence_timeline"] = str(path)


def asr_evidence_items(asr: dict[str, Any]) -> list[EvidenceItem]:
    if asr.get("status") != "transcribed":
        return []

    items: list[EvidenceItem] = []
    segments = asr.get("segments", [])
    if isinstance(segments, list):
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            text = str(segment.get("text", "")).strip()
            if not text:
                continue
            start = segment.get("start", 0)
            items.append(
                EvidenceItem(
                    timestamp=seconds_to_timestamp(int(float(start))),
                    type="asr",
                    claim=text,
                    raw_excerpt=text,
                    confidence=0.7,
                )
            )

    text = str(asr.get("text", "")).strip()
    if not items and text:
        items.append(
            EvidenceItem(
                timestamp="00:00:00",
                type="asr",
                claim=text,
                raw_excerpt=text,
                confidence=0.6,
            )
        )
    return items


def vision_evidence_items(vision: dict[str, Any]) -> list[EvidenceItem]:
    if vision.get("status") not in {"analyzed", "partial"}:
        return []

    items: list[EvidenceItem] = []
    frames = vision.get("items", [])
    if not isinstance(frames, list):
        return items

    for frame in frames:
        if not isinstance(frame, dict):
            continue
        timestamp = str(frame.get("timestamp", "00:00:00"))
        observations = frame.get("observations", [])
        if not isinstance(observations, list):
            continue
        for observation in observations:
            if not isinstance(observation, dict):
                continue
            claim = str(observation.get("claim", "")).strip()
            evidence_type = str(observation.get("type", "")).strip()
            if not claim or not evidence_type:
                continue
            confidence = observation.get("confidence", 0.0)
            try:
                confidence_value = float(confidence)
            except (TypeError, ValueError):
                confidence_value = 0.0
            items.append(
                EvidenceItem(
                    timestamp=timestamp,
                    type=evidence_type,
                    claim=claim,
                    raw_excerpt=str(observation.get("raw_excerpt", "")),
                    confidence=confidence_value,
                )
            )
    return items


def metadata_evidence_items(manifest: VideoSourceManifest) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    title = manifest.title.strip()
    if title:
        items.append(
            EvidenceItem(
                timestamp="00:00:00",
                type="metadata_title",
                claim=f"视频标题说明主题：{title}",
                raw_excerpt=title,
                confidence=0.45,
            )
        )
    author = manifest.author.strip()
    if author:
        items.append(
            EvidenceItem(
                timestamp="00:00:00",
                type="metadata_author",
                claim=f"视频作者：{author}",
                raw_excerpt=author,
                confidence=0.35,
            )
        )
    return items


def _write_distillation(
    store: RunStore,
    state: RunState,
    config: AppConfig,
    distiller_client: DistillerClient | None,
) -> None:
    path = store.run_path(state.run_id) / "distillation.json"
    if not path.exists():
        timeline = EvidenceTimeline.from_dict(read_json(store.evidence_timeline_path(state.run_id)))
        manifest = VideoSourceManifest.from_dict(read_json(store.manifest_path(state.run_id)))
        if not timeline.items:
            write_json(
                path,
                {
                    "status": "skipped",
                    "reason": "no evidence items are available for RIA++ distillation",
                    "candidate_title": "",
                },
            )
        elif distiller_client is None:
            _append_manifest_risk(store, state, "distillation_skipped")
            write_json(
                path,
                {
                    "status": "skipped",
                    "reason": "newapi API key is not configured",
                    "requires": ["newapi"],
                    "model": config.newapi.distiller_model,
                    "candidate_title": "",
                },
            )
        else:
            try:
                result = distiller_client.distill_skill(
                    timeline.to_dict(),
                    manifest.to_dict(),
                    config.newapi.distiller_model,
                )
            except NewApiError as exc:
                _append_manifest_risk(store, state, "distillation_failed")
                write_json(
                    path,
                    {
                        "status": "failed",
                        "reason_code": exc.code,
                        "returncode": exc.status_code,
                        "summary": exc.safe_summary,
                        "model": config.newapi.distiller_model,
                        "candidate_title": "",
                    },
                )
            else:
                result["model"] = config.newapi.distiller_model
                write_json(path, result)
    state.artifacts["distillation"] = str(path)


def _write_score(
    store: RunStore,
    state: RunState,
    config: AppConfig,
    judge_client: JudgeClient | None,
    judge_difficulty: str = "standard",
) -> None:
    path = store.run_path(state.run_id) / "score.json"
    if not path.exists():
        timeline = EvidenceTimeline.from_dict(read_json(store.evidence_timeline_path(state.run_id)))
        manifest = VideoSourceManifest.from_dict(read_json(store.manifest_path(state.run_id)))
        distillation = read_json(store.run_path(state.run_id) / "distillation.json")
        rule_score = rule_score_for_distillation(distillation, timeline)
        single_channel = has_single_channel_evidence(timeline)
        judge_record: dict[str, Any]
        if distillation.get("status") != "distilled":
            judge_score = 0
            judge_record = {
                "status": "skipped",
                "reason": "distillation did not produce a candidate draft",
            }
        elif judge_difficulty == "off":
            judge_score = rule_score
            judge_record = {
                "status": "disabled",
                "reason": "LLM judge was disabled for this run",
                "model": config.newapi.judge_model,
            }
        elif judge_client is None:
            _append_manifest_risk(store, state, "judge_skipped")
            judge_score = 0
            judge_record = {
                "status": "skipped",
                "reason": "newapi API key is not configured",
                "requires": ["newapi"],
                "model": config.newapi.judge_model,
            }
        else:
            try:
                try:
                    judge_record = judge_client.judge_skill(
                        distillation,
                        timeline.to_dict(),
                        manifest.to_dict(),
                        config.newapi.judge_model,
                        difficulty=judge_difficulty,
                    )
                except TypeError as exc:
                    if "difficulty" not in str(exc):
                        raise
                    judge_record = judge_client.judge_skill(
                        distillation,
                        timeline.to_dict(),
                        manifest.to_dict(),
                        config.newapi.judge_model,
                    )
            except NewApiError as exc:
                _append_manifest_risk(store, state, "judge_failed")
                judge_score = 0
                judge_record = {
                    "status": "failed",
                    "reason_code": exc.code,
                    "returncode": exc.status_code,
                    "summary": exc.safe_summary,
                    "model": config.newapi.judge_model,
                }
            else:
                judge_score = int(judge_record.get("score", 0))
                judge_record["model"] = config.newapi.judge_model
                for flag in judge_record.get("risk_flags", []):
                    if str(flag) == "single_channel_evidence":
                        single_channel = True
        if judge_difficulty == "off" and distillation.get("status") == "distilled":
            score = ScoreResult(
                rule_score=rule_score,
                llm_judge_score=judge_score,
                final_score=rule_score,
                final_status="needs_review",
                conflict_policy="judge_disabled",
            )
        else:
            score = conservative_score(
                rule_score=rule_score,
                llm_judge_score=judge_score,
                single_channel_evidence=single_channel,
                difficulty=judge_difficulty,
            )
        payload = score.to_dict()
        payload["judge_difficulty"] = judge_difficulty
        payload["judge"] = judge_record
        payload["single_channel_evidence"] = single_channel
        write_json(path, payload)
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

    with _stage_timer(store, state, "package"):
        state.current_stage = "package"
        manifest = VideoSourceManifest.from_dict(read_json(store.manifest_path(state.run_id)))
        score = ScoreResult.from_dict(read_json(store.run_path(state.run_id) / "score.json"))
        timeline = EvidenceTimeline.from_dict(read_json(store.evidence_timeline_path(state.run_id)))
        risk_flags = list(manifest.risk_flags)
        if score.final_status == "failed":
            risk_flags.append("insufficient_evidence")
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
            risk_flags=risk_flags,
            scores=score,
        )
        write_json(store.run_path(state.run_id) / "metadata.json", metadata.to_dict())

        if score.final_status in {"passed", "needs_review"}:
            state.status = "completed"
            state.failure_reason = None
            state.artifacts["candidate_out_dir"] = str(Path(out_dir))
            _mark_completed(store, state, "package")
            distillation = read_json(store.run_path(state.run_id) / "distillation.json")
            package_path = write_candidate_package(
                out_dir,
                metadata,
                distillation,
                evidence_timeline=timeline,
            )
            state.artifacts["package"] = str(package_path)
            store.save(state)
            return

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
            score_record = read_json(store.run_path(state.run_id) / "score.json")
            judge_record = score_record.get("judge", {}) if isinstance(score_record, dict) else {}
            if isinstance(judge_record, dict) and judge_record.get("status") == "skipped":
                state.failure_reason = f"insufficient evidence: {judge_record.get('reason', 'judge skipped')}"
            elif isinstance(judge_record, dict) and judge_record.get("status") == "failed":
                state.failure_reason = f"insufficient evidence: judge failed: {judge_record.get('reason_code', 'judge_failed')}"
            else:
                state.failure_reason = "insufficient evidence: final score is below the candidate threshold"
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
