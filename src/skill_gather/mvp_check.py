from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .adapters.bilibili import build_initial_manifest
from .config import AppConfig
from .integrations import YtDlpError
from .pipeline import run_video_pipeline
from .runs import RunStore, read_json
from .source import infer_source


def run_mvp_check(config: AppConfig) -> dict[str, Any]:
    """Run an offline MVP smoke check against the real pipeline orchestration."""
    with tempfile.TemporaryDirectory(prefix="skill-gather-mvp-") as temp_dir:
        root = Path(temp_dir)
        candidate = _run_candidate_pipeline_check(config, root)
        audit = _run_failure_audit_pipeline_check(config, root)
    checks = {
        "candidate_pipeline": candidate,
        "failure_audit_pipeline": audit,
    }
    status = "passed" if all(check.get("status") == "passed" for check in checks.values()) else "failed"
    return {
        "status": status,
        "checks": checks,
        "notes": [
            "offline smoke check only; it does not call yt-dlp, ffmpeg, or newapi",
            "temporary run and skill artifacts are deleted after the check",
        ],
    }


def _run_candidate_pipeline_check(config: AppConfig, root: Path) -> dict[str, Any]:
    try:
        url = "https://www.bilibili.com/video/BV1xx411c7mD/"
        store = RunStore(root / "runs-candidate")
        out_dir = root / "skills"
        source = infer_source(url)
        manifest = build_initial_manifest(url, source)
        state = store.start_or_resume(source.source, source.source_id)
        store.save_manifest(state.run_id, manifest)

        result = run_video_pipeline(
            config=config,
            store=store,
            state=state,
            manifest=manifest,
            out_dir=out_dir,
            metadata_probe=_FakeMetadataProbe(
                {
                    "title": "MVP Candidate Demo",
                    "uploader": "Local Self Test",
                    "duration": 120,
                    "subtitles": {"zh-CN": [{"url": "https://example.test/subtitle.json"}]},
                }
            ),
            media_downloader=_FakeMediaDownloader(),
            media_processor=_FakeMediaProcessor(),
            asr_client=_FakeAsrClient(),
            vision_client=_FakeVisionClient(),
            distiller_client=_FakeDistillerClient(),
            judge_client=_FakeJudgeClient(score=86),
        )
        package_dir = Path(result.artifacts.get("package", ""))
        required_files = ["SKILL.md", "README.md", "metadata.json", "evidence_timeline.json"]
        missing = [name for name in required_files if not (package_dir / name).exists()]
        metadata = read_json(package_dir / "metadata.json") if (package_dir / "metadata.json").exists() else {}
        if result.status != "completed" or missing or metadata.get("package_status") not in {"passed", "needs_review"}:
            return {
                "status": "failed",
                "run_id": result.run_id,
                "pipeline_status": result.status,
                "package_dir": str(package_dir),
                "missing": missing,
                "package_status": metadata.get("package_status", ""),
            }
        return {
            "status": "passed",
            "run_id": result.run_id,
            "pipeline_status": result.status,
            "package_dir": str(package_dir),
            "artifacts": required_files,
            "final_status": metadata["package_status"],
        }
    except Exception as exc:  # pragma: no cover - defensive summary for CLI users
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def _run_failure_audit_pipeline_check(config: AppConfig, root: Path) -> dict[str, Any]:
    try:
        url = "https://www.bilibili.com/video/BV1yy411c7mE/"
        store = RunStore(root / "runs-audit")
        source = infer_source(url)
        manifest = build_initial_manifest(url, source)
        state = store.start_or_resume(source.source, source.source_id)
        store.save_manifest(state.run_id, manifest)

        result = run_video_pipeline(
            config=config,
            store=store,
            state=state,
            manifest=manifest,
            out_dir=root / "skills-audit",
            metadata_probe=_FakeMetadataProbe(
                error=YtDlpError(
                    "offline metadata probe failure",
                    code="metadata_probe_failed",
                    returncode=1,
                )
            ),
            media_downloader=_FakeMediaDownloader(),
            media_processor=_FakeMediaProcessor(),
        )
        run_dir = store.run_path(result.run_id)
        required_files = [
            "run_state.json",
            "manifest.json",
            "metadata.json",
            "evidence_timeline.json",
            "score.json",
            "failure_report.md",
        ]
        missing = [name for name in required_files if not (run_dir / name).exists()]
        metadata = read_json(run_dir / "metadata.json") if (run_dir / "metadata.json").exists() else {}
        if result.status != "failed" or missing or metadata.get("package_status") != "failed":
            return {
                "status": "failed",
                "run_id": result.run_id,
                "pipeline_status": result.status,
                "run_dir": str(run_dir),
                "missing": missing,
                "package_status": metadata.get("package_status", ""),
            }
        return {
            "status": "passed",
            "run_id": result.run_id,
            "pipeline_status": result.status,
            "run_dir": str(run_dir),
            "artifacts": required_files,
            "failure_reason": result.failure_reason,
        }
    except Exception as exc:  # pragma: no cover - defensive summary for CLI users
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


class _FakeMetadataProbe:
    def __init__(self, metadata: dict[str, Any] | None = None, error: Exception | None = None):
        self.metadata = metadata or {}
        self.error = error

    def probe_metadata(self, url: str) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        return self.metadata


class _FakeMediaDownloader:
    def download_media(self, url: str, target_dir: str | Path) -> dict[str, Any]:
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        media_file = target / "mvp-demo.mp4"
        media_file.write_text("placeholder media", encoding="utf-8")
        return {
            "status": "downloaded",
            "target_dir": str(target),
            "output_template": "%(id)s.%(ext)s",
            "media_files": [str(media_file)],
            "returncode": 0,
        }


class _FakeMediaProcessor:
    def extract_audio(self, media_file: str | Path, target_path: str | Path) -> dict[str, Any]:
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("placeholder audio", encoding="utf-8")
        return {
            "status": "extracted",
            "audio_path": str(target),
            "source_media": str(media_file),
            "returncode": 0,
        }

    def extract_frames(
        self,
        media_file: str | Path,
        target_dir: str | Path,
        *,
        interval_sec: int = 10,
    ) -> dict[str, Any]:
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        frames = [target / "frame-000001.jpg", target / "frame-000002.jpg"]
        for frame in frames:
            frame.write_text("placeholder frame", encoding="utf-8")
        return {
            "status": "extracted",
            "frame_dir": str(target),
            "frame_pattern": "frame-%06d.jpg",
            "frame_paths": [str(frame) for frame in frames],
            "interval_sec": interval_sec,
            "source_media": str(media_file),
            "returncode": 0,
        }


class _FakeAsrClient:
    def transcribe_audio(self, audio_file: str | Path, model: str) -> dict[str, Any]:
        return {
            "status": "transcribed",
            "model": model,
            "audio_path": str(audio_file),
            "text": "Use editable install while developing a local Python package.",
            "language": "en",
            "segments": [
                {
                    "start": 10.0,
                    "end": 12.0,
                    "text": "Use editable install while developing a local Python package.",
                }
            ],
            "returncode": 0,
        }


class _FakeVisionClient:
    def analyze_frame(self, frame_file: str | Path, model: str) -> dict[str, Any]:
        return {
            "status": "analyzed",
            "model": model,
            "frame_path": str(frame_file),
            "observations": [
                {
                    "type": "frame_ocr",
                    "claim": "The frame shows python -m pip install -e .",
                    "raw_excerpt": "python -m pip install -e .",
                    "confidence": 0.93,
                }
            ],
            "returncode": 0,
        }


class _FakeDistillerClient:
    def distill_skill(
        self,
        evidence_timeline: dict[str, Any],
        manifest: dict[str, Any],
        model: str,
    ) -> dict[str, Any]:
        return {
            "status": "distilled",
            "model": model,
            "candidate_title": "Install editable Python package",
            "summary": "A reusable workflow for installing a local Python package in editable mode.",
            "ria": {
                "recall": "The evidence shows an editable install command and narration.",
                "interpret": "Editable installs are useful while developing a local Python package.",
                "apply": ["Run python -m pip install -e . from the package root."],
                "boundary": "Only use for Python projects with packaging metadata.",
                "test": ["Import the package after installing it."],
            },
            "evidence_refs": [
                {
                    "timestamp": "00:00:10",
                    "type": "frame_ocr",
                    "claim": "The frame shows python -m pip install -e .",
                }
            ],
            "returncode": 0,
        }


class _FakeJudgeClient:
    def __init__(self, score: int):
        self.score = score

    def judge_skill(
        self,
        distillation: dict[str, Any],
        evidence_timeline: dict[str, Any],
        manifest: dict[str, Any],
        model: str,
    ) -> dict[str, Any]:
        return {
            "status": "judged",
            "model": model,
            "score": self.score,
            "rationale": "The draft is actionable and backed by ASR plus frame OCR evidence.",
            "risk_flags": [],
            "returncode": 0,
        }
