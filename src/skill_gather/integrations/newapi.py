from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .yt_dlp import sanitize_command_output


class NewApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "newapi_error",
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.safe_summary = sanitize_command_output(message)


def _multipart_form_data(fields: dict[str, str], file_field: str, file_name: str, file_bytes: bytes) -> tuple[bytes, str]:
    boundary = "----skillgatherboundary7d1b0d1d0f7f"
    lines: list[bytes] = []
    for key, value in fields.items():
        lines.extend(
            [
                f"--{boundary}".encode("utf-8"),
                f'Content-Disposition: form-data; name="{key}"'.encode("utf-8"),
                b"",
                value.encode("utf-8"),
            ]
        )
    lines.extend(
        [
            f"--{boundary}".encode("utf-8"),
            f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"'.encode("utf-8"),
            b"Content-Type: application/octet-stream",
            b"",
            file_bytes,
            f"--{boundary}--".encode("utf-8"),
            b"",
        ]
    )
    body = b"\r\n".join(lines)
    return body, f"multipart/form-data; boundary={boundary}"


@dataclass(frozen=True, slots=True)
class NewApiClient:
    base_url: str
    api_key: str
    timeout_sec: int = 120

    @classmethod
    def from_config(cls, config: Any) -> "NewApiClient | None":
        api_key = os.getenv(str(config.api_key_env), "").strip()
        if not api_key:
            return None
        return cls(base_url=str(config.base_url), api_key=api_key)

    def transcribe_audio(self, audio_file: str | Path, model: str) -> dict[str, Any]:
        path = Path(audio_file)
        if not path.exists():
            raise NewApiError("audio file does not exist", code="audio_file_missing")

        body, content_type = _multipart_form_data(
            {"model": model},
            "file",
            path.name,
            path.read_bytes(),
        )
        request = urllib.request.Request(
            url=f"{self.base_url.rstrip('/')}/audio/transcriptions",
            data=body,
            method="POST",
        )
        request.add_header("Authorization", f"Bearer {self.api_key}")
        request.add_header("Content-Type", content_type)
        request.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                payload = response.read().decode("utf-8", errors="replace")
                status_code = getattr(response, "status", 200)
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise NewApiError(
                body_text or str(exc),
                code="transcription_failed",
                status_code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise NewApiError(
                str(exc.reason),
                code="transcription_unreachable",
            ) from exc

        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise NewApiError(
                "newapi returned invalid JSON for transcription",
                code="invalid_transcription_json",
                status_code=status_code,
            ) from exc

        if not isinstance(parsed, dict):
            raise NewApiError(
                "newapi returned non-object transcription JSON",
                code="invalid_transcription_shape",
                status_code=status_code,
            )

        text = str(parsed.get("text", ""))
        segments = parsed.get("segments", [])
        if not isinstance(segments, list):
            segments = []
        return {
            "status": "transcribed",
            "model": model,
            "audio_path": str(path),
            "text": text,
            "language": str(parsed.get("language", "")),
            "segments": segments,
            "returncode": 0,
        }
