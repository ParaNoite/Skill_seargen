from __future__ import annotations

from pathlib import Path
from typing import Any

from .runs import read_json, safe_slug


def build_reliability_report(benchmark: dict[str, Any], runs_root: str | Path) -> dict[str, Any]:
    videos = benchmark.get("videos", [])
    if not isinstance(videos, list):
        raise ValueError("benchmark.videos 必须是数组。")
    root = Path(runs_root)
    samples: list[dict[str, Any]] = []
    for video in videos:
        if not isinstance(video, dict) or not str(video.get("source_id", "")).strip():
            raise ValueError("每个 benchmark 视频必须包含 source_id。")
        source = str(video.get("source", "bilibili"))
        source_id = str(video["source_id"])
        variant = str(video.get("run_variant", "")).strip()
        run_id = f"{source}-{safe_slug(source_id)}"
        if variant:
            run_id += f"--{safe_slug(variant)}"
        run_dir = root / run_id
        state = _optional_json(run_dir / "run_state.json")
        if not state:
            samples.append({"id": source_id, "run_id": run_id, "status": "not_run"})
            continue
        audit = _optional_json(run_dir / "model_audit.json")
        distillation = _optional_json(run_dir / "distillation.json")
        score = _optional_json(run_dir / "score.json")
        cli_result = _optional_json(run_dir / "cli_result.json")
        attempts = audit.get("distillation", {}).get("attempts", [])
        if not isinstance(attempts, list):
            attempts = []
        error_codes = _error_codes(run_dir, distillation, score)
        audit_required = distillation.get("status") == "failed" or any(
            item.get("status") == "failed" for item in attempts if isinstance(item, dict)
        )
        judge = score.get("judge", {}) if isinstance(score.get("judge"), dict) else {}
        audit_required = audit_required or judge.get("status") == "failed"
        samples.append(
            {
                "id": source_id,
                "run_id": run_id,
                "status": state.get("status", "unknown"),
                "expected_cli_exit_code": 1 if state.get("status") == "failed" else 0,
                "observed_cli_exit_code": cli_result.get("exit_code"),
                "completed_stage_count": len(state.get("completed_stages", [])),
                "distillation_attempt_count": len(attempts),
                "retry_succeeded": len(attempts) > 1 and attempts[-1].get("status") == "succeeded",
                "error_codes": error_codes,
                "model_audit_required": audit_required,
                "model_audit_present": bool(audit),
            }
        )

    executed = [sample for sample in samples if sample["status"] != "not_run"]
    failed = [sample for sample in executed if sample["status"] == "failed"]
    audit_required = [sample for sample in executed if sample.get("model_audit_required")]
    return {
        "benchmark_id": benchmark.get("benchmark_id", ""),
        "sample_count": len(samples),
        "executed_count": len(executed),
        "not_run_count": len(samples) - len(executed),
        "completed_count": sum(sample["status"] == "completed" for sample in executed),
        "failed_count": len(failed),
        "retry_count": sum(max(0, int(sample.get("distillation_attempt_count", 0)) - 1) for sample in executed),
        "retry_success_count": sum(bool(sample.get("retry_succeeded")) for sample in executed),
        "nonzero_exit_code_coverage": (
            round(sum(sample.get("observed_cli_exit_code") == 1 for sample in failed) / len(failed), 4)
            if failed
            else None
        ),
        "model_audit_coverage": (
            round(sum(bool(sample.get("model_audit_present")) for sample in audit_required) / len(audit_required), 4)
            if audit_required
            else None
        ),
        "samples": samples,
    }


def build_vision_report(run_dirs: list[str | Path], expected_fields: list[str]) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for value in run_dirs:
        run_dir = Path(value)
        vision = _optional_json(run_dir / "vision_ocr.json")
        timings = _optional_json(run_dir / "stage_timings.json")
        extracted = _vision_text(vision)
        matched = [field for field in expected_fields if _normalized(field) in extracted]
        runs.append(
            {
                "run_dir": str(run_dir),
                "strategy": vision.get("strategy", "unknown"),
                "source_frame_count": int(vision.get("source_frame_count", vision.get("frame_count", 0))),
                "remote_call_count": int(vision.get("remote_call_count", 0)),
                "vision_duration_ms": _stage_duration(timings, "vision_ocr"),
                "expected_field_count": len(expected_fields),
                "matched_field_count": len(matched),
                "field_accuracy": round(len(matched) / len(expected_fields), 4) if expected_fields else None,
                "matched_fields": matched,
            }
        )
    return {"run_count": len(runs), "expected_fields": expected_fields, "runs": runs}


def _error_codes(run_dir: Path, distillation: dict[str, Any], score: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for name in ("media_probe.json", "media_extract.json", "asr.json", "vision_ocr.json"):
        record = _optional_json(run_dir / name)
        code = str(record.get("reason_code", "")).strip()
        if code and code not in codes:
            codes.append(code)
    for record in (distillation, score.get("judge", {})):
        if isinstance(record, dict):
            code = str(record.get("reason_code", "")).strip()
            if code and code not in codes:
                codes.append(code)
    return codes


def _vision_text(vision: dict[str, Any]) -> str:
    values: list[str] = []
    for frame in vision.get("items", []):
        if not isinstance(frame, dict):
            continue
        for observation in frame.get("observations", []):
            if isinstance(observation, dict):
                values.extend([str(observation.get("claim", "")), str(observation.get("raw_excerpt", ""))])
    return _normalized(" ".join(values))


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def _stage_duration(timings: dict[str, Any], stage: str) -> int | None:
    for item in timings.get("stages", []):
        if isinstance(item, dict) and item.get("stage") == stage:
            return int(item.get("duration_ms", 0))
    return None


def _optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = read_json(path)
    return value if isinstance(value, dict) else {}
