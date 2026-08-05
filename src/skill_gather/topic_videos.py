from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .adapters.bilibili import build_initial_manifest
from .config import AppConfig
from .models import TopicSourceCandidate, TopicTask, TopicUsage, TopicVideoRun
from .pipeline import run_video_pipeline
from .runs import RunStore, read_json, safe_slug, write_json
from .source import SourceInferenceError, infer_source


@dataclass(slots=True)
class VideoProcessingResult:
    successful: list[TopicVideoRun] = field(default_factory=list)
    failed: list[TopicVideoRun] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


def process_topic_videos(
    task: TopicTask,
    run_root: Path,
    config: AppConfig,
    *,
    vision_mode: str = "sampled",
    vision_frame_limit: int = 12,
) -> VideoProcessingResult:
    """Process confirmed Bilibili videos into topic-scoped child runs and evidence."""
    package = task.package
    if package is None:
        raise ValueError("主题任务缺少主题包索引")
    if vision_mode not in {"full", "sampled", "off"}:
        raise ValueError("视觉模式必须是 full、sampled 或 off")
    if vision_frame_limit <= 0:
        raise ValueError("视觉帧数上限必须是正整数")

    result = VideoProcessingResult()
    child_store = RunStore(run_root / "video_runs")
    evidence_dir = run_root / package.evidence
    evidence_dir.mkdir(parents=True, exist_ok=True)
    preserved = [entry for entry in task.video_runs if not _is_selected_video(entry, task.selected_sources)]
    processed_duration = sum(entry.duration_sec for entry in preserved)
    model_calls = sum(entry.model_calls for entry in preserved)

    for candidate in task.selected_sources:
        if candidate.source_type != "video":
            continue
        try:
            source = infer_source(candidate.canonical_url or candidate.url)
        except SourceInferenceError as exc:
            entry = TopicVideoRun(
                parent_run_id=task.run_id,
                candidate_id=candidate.candidate_id,
                child_run_id="",
                source_url=candidate.canonical_url or candidate.url,
                status="failed",
                failure_reason=str(exc),
            )
            result.failed.append(entry)
            continue

        state = child_store.start_or_resume(source.source, source.source_id, run_variant=task.run_id)
        if task.cache.refresh_cache:
            child_store.delete_run(state.run_id)
            state = child_store.start_or_resume(source.source, source.source_id, run_variant=task.run_id)
        manifest_path = child_store.manifest_path(state.run_id)
        manifest = build_initial_manifest(candidate.canonical_url or candidate.url, source)
        if manifest_path.exists():
            from .models import VideoSourceManifest

            manifest = VideoSourceManifest.from_dict(read_json(manifest_path))

        baseline_duration = processed_duration

        def preflight(probed_manifest: Any) -> str | None:
            if probed_manifest.duration_sec <= 0:
                return "主题视频预算预检失败：无法确定视频时长"
            if baseline_duration + probed_manifest.duration_sec > task.budget.max_video_duration_sec:
                return (
                    "主题视频时长预算超限："
                    f"已计划 {baseline_duration}s，当前视频 {probed_manifest.duration_sec}s，"
                    f"上限 {task.budget.max_video_duration_sec}s"
                )
            return None

        if vision_mode != "off" and model_calls >= task.budget.max_model_calls:
            entry = TopicVideoRun(
                parent_run_id=task.run_id,
                candidate_id=candidate.candidate_id,
                child_run_id=state.run_id,
                source_url=candidate.canonical_url or candidate.url,
                status="failed",
                failure_reason="主题模型调用预算已耗尽，未开始该视频的视觉处理",
            )
            result.failed.append(entry)
            continue
        try:
            state = run_video_pipeline(
                config=config,
                store=child_store,
                state=state,
                manifest=manifest,
                out_dir=run_root / "unused-video-skill-output",
                judge_difficulty=task.judge_difficulty,
                vision_mode=vision_mode,
                vision_frame_limit=min(vision_frame_limit, max(1, task.budget.max_model_calls - model_calls)),
                evidence_only=True,
                pre_media_extract=preflight,
            )
        except Exception as exc:
            state.status = "failed"
            state.failure_reason = _safe_reason(exc)
            child_store.save(state)
            entry = TopicVideoRun(
                parent_run_id=task.run_id,
                candidate_id=candidate.candidate_id,
                child_run_id=state.run_id,
                source_url=candidate.canonical_url or candidate.url,
                status="failed",
                failure_reason=_safe_reason(exc),
            )
            result.failed.append(entry)
            continue

        child_manifest = read_json(manifest_path) if manifest_path.exists() else manifest.to_dict()
        duration_sec = int(child_manifest.get("duration_sec", 0) or 0)
        vision_path = child_store.run_path(state.run_id) / "vision_ocr.json"
        vision = read_json(vision_path) if vision_path.exists() else {}
        child_calls = int(vision.get("remote_call_count", 0) or 0)
        vision_status = str(vision.get("status", ""))
        vision_reason = str(vision.get("reason", ""))
        processed_duration = baseline_duration + duration_sec
        model_calls += child_calls
        timeline_path = child_store.evidence_timeline_path(state.run_id)
        timeline = read_json(timeline_path) if timeline_path.exists() else {}
        evidence_path = f"{package.evidence}/video-{_safe_component(candidate.candidate_id)}.json"
        entry = TopicVideoRun(
            parent_run_id=task.run_id,
            candidate_id=candidate.candidate_id,
            child_run_id=state.run_id,
            source_url=candidate.canonical_url or candidate.url,
            status="completed" if state.status == "completed" and timeline.get("items") else "failed",
            title=str(child_manifest.get("title", "")),
            duration_sec=duration_sec,
            model_calls=child_calls,
            evidence_path=evidence_path,
            failure_reason=None if state.status == "completed" and timeline.get("items") else (state.failure_reason or "视频未生成可用证据"),
            vision_status=vision_status,
            vision_reason=vision_reason,
        )
        if entry.status == "completed":
            write_json(
                run_root / evidence_path,
                {
                    "source_type": "video",
                    "candidate_id": candidate.candidate_id,
                    "child_run_id": state.run_id,
                    "child_run_path": f"video_runs/{state.run_id}",
                    "manifest": child_manifest,
                    "timeline": timeline,
                },
            )
            result.successful.append(entry)
        else:
            result.failed.append(entry)

    task.video_runs = preserved + result.successful + result.failed
    task.usage = TopicUsage(
        candidate_count=len(task.candidates),
        selected_source_count=len(task.selected_sources),
        processed_video_duration_sec=sum(entry.duration_sec for entry in task.video_runs),
        model_calls=sum(entry.model_calls for entry in task.video_runs),
        estimated_cost_usd=task.usage.estimated_cost_usd,
        elapsed_runtime_sec=task.usage.elapsed_runtime_sec,
    )
    write_json(
        run_root / "video_processing_audit.json",
        {
            "topic": task.topic,
            "processed_at": datetime.now(UTC).isoformat(),
            "successful_video_runs": [entry.to_dict() for entry in result.successful],
            "failed_video_runs": [entry.to_dict() for entry in result.failed],
            "skipped_sources": result.skipped,
        },
    )
    return result


def _is_selected_video(entry: TopicVideoRun, selected: list[TopicSourceCandidate]) -> bool:
    return any(candidate.source_type == "video" and candidate.candidate_id == entry.candidate_id for candidate in selected)


def _safe_component(value: str) -> str:
    return safe_slug(value) or "video-source"


def _safe_reason(exc: Exception) -> str:
    return str(getattr(exc, "safe_summary", str(exc)))
