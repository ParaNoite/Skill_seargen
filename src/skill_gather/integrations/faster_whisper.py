from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .newapi import NewApiError


FasterWhisperImportError = (
    "faster-whisper is not installed. Install project dependencies first: "
    "python -m pip install -e ."
)


def _project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "skill_gather").is_dir():
            return parent
    return Path.cwd()


def _configure_hugging_face_environment() -> Path:
    os.environ.setdefault("HF_HOME", str(_project_root() / ".hf-cache"))
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    return Path(os.environ.get("HF_HUB_CACHE", Path(os.environ["HF_HOME"]) / "hub"))


def is_faster_whisper_model(model: str) -> bool:
    return str(model).strip().lower().startswith(("faster-whisper:", "faster_whisper:"))


def faster_whisper_model_name(model: str) -> str:
    _, _, value = str(model).partition(":")
    return value.strip() or "base"


@dataclass(slots=True)
class FasterWhisperClient:
    model_name: str
    device: str = "auto"
    compute_type: str = "default"

    @classmethod
    def from_model(cls, model: str) -> "FasterWhisperClient":
        return cls(
            model_name=faster_whisper_model_name(model),
            device=os.getenv("SKILL_GATHER_FASTER_WHISPER_DEVICE", "auto"),
            compute_type=os.getenv("SKILL_GATHER_FASTER_WHISPER_COMPUTE_TYPE", "default"),
        )

    def transcribe_audio(self, audio_file: str | Path, model: str) -> dict[str, Any]:
        path = Path(audio_file)
        if not path.exists():
            raise NewApiError("audio file does not exist", code="audio_file_missing")

        model_cache = _configure_hugging_face_environment()
        try:
            from faster_whisper import WhisperModel
            from huggingface_hub.errors import LocalEntryNotFoundError
        except ImportError as exc:
            raise NewApiError(FasterWhisperImportError, code="faster_whisper_missing") from exc

        try:
            model_options = {
                "device": self.device,
                "compute_type": self.compute_type,
                "download_root": str(model_cache),
            }
            try:
                whisper = WhisperModel(self.model_name, local_files_only=True, **model_options)
            except LocalEntryNotFoundError:
                whisper = WhisperModel(self.model_name, **model_options)
            segments, info = whisper.transcribe(str(path))
            segment_items = [
                {
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "text": str(segment.text).strip(),
                }
                for segment in segments
                if str(segment.text).strip()
            ]
        except Exception as exc:
            message = (
                f"faster-whisper failed to load or transcribe with local model '{self.model_name}': {exc}. "
                "If this is the first run, preload the model weights. On restricted networks, try setting "
                "HF_ENDPOINT=https://hf-mirror.com and HF_HUB_DISABLE_XET=1, or configure "
                "asr_model as faster-whisper:<local model path>."
            )
            raise NewApiError(message, code="faster_whisper_failed") from exc

        text = " ".join(segment["text"] for segment in segment_items).strip()
        return {
            "status": "transcribed",
            "model": model,
            "local_model": self.model_name,
            "audio_path": str(path),
            "text": text,
            "language": str(getattr(info, "language", "")),
            "segments": segment_items,
            "returncode": 0,
        }
