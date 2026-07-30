from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .yt_dlp import sanitize_command_output


class FfmpegError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "ffmpeg_error",
        returncode: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.returncode = returncode
        self.safe_summary = sanitize_command_output(message)


@dataclass(frozen=True, slots=True)
class FfmpegClient:
    binary: str = "ffmpeg"
    timeout_sec: int = 300

    def extract_audio(self, media_file: str | Path, target_path: str | Path) -> dict[str, Any]:
        media = Path(media_file)
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        args = [
            self.binary,
            "-y",
            "-i",
            str(media),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(target),
        ]
        self._run(args, error_code="audio_extract_failed")
        return {
            "status": "extracted",
            "audio_path": str(target),
            "source_media": str(media),
            "returncode": 0,
        }

    def extract_frames(
        self,
        media_file: str | Path,
        target_dir: str | Path,
        *,
        interval_sec: int = 10,
    ) -> dict[str, Any]:
        if interval_sec <= 0:
            raise FfmpegError(
                "frame extraction interval must be greater than zero",
                code="invalid_frame_interval",
            )
        media = Path(media_file)
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        pattern = "frame-%06d.jpg"
        args = [
            self.binary,
            "-y",
            "-i",
            str(media),
            "-vf",
            f"fps=1/{interval_sec}",
            str(target / pattern),
        ]
        self._run(args, error_code="frame_extract_failed")
        frame_paths = [str(path) for path in sorted(target.glob("*.jpg")) if path.is_file()]
        return {
            "status": "extracted",
            "frame_dir": str(target),
            "frame_pattern": pattern,
            "frame_paths": frame_paths,
            "interval_sec": interval_sec,
            "source_media": str(media),
            "returncode": 0,
        }

    def _run(self, args: list[str], *, error_code: str) -> None:
        try:
            completed = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise FfmpegError(
                f"{self.binary} not found; install ffmpeg to process media",
                code="binary_not_found",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise FfmpegError(
                f"{self.binary} timed out while processing media",
                code=f"{error_code}_timeout",
            ) from exc

        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "unknown ffmpeg error"
            raise FfmpegError(message, code=error_code, returncode=completed.returncode)
