from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class YtDlpError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "yt_dlp_error",
        returncode: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.returncode = returncode
        self.safe_summary = sanitize_command_output(message)


URL_RE = re.compile(r"https?://\S+")
SECRET_HINT_RE = re.compile(
    r"(?i)(cookie|token|authorization|api[_-]?key|signature|credential|password)"
)


def sanitize_command_output(value: str, *, max_length: int = 240) -> str:
    sanitized_parts: list[str] = []
    for raw_part in value.split():
        part = URL_RE.sub("[redacted-url]", raw_part)
        if SECRET_HINT_RE.search(part):
            part = "[redacted-secret]"
        sanitized_parts.append(part)
    sanitized = " ".join(sanitized_parts).strip()
    if len(sanitized) > max_length:
        return sanitized[: max_length - 3].rstrip() + "..."
    return sanitized


@dataclass(frozen=True, slots=True)
class YtDlpClient:
    binary: str = "yt-dlp"
    timeout_sec: int = 120

    def probe_metadata(self, url: str) -> dict[str, Any]:
        args = [
            self.binary,
            "--dump-json",
            "--skip-download",
            "--no-playlist",
            url,
        ]
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
            raise YtDlpError(
                f"{self.binary} not found; install yt-dlp to probe metadata",
                code="binary_not_found",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise YtDlpError(
                f"{self.binary} timed out while probing metadata",
                code="metadata_probe_timeout",
            ) from exc

        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "unknown yt-dlp error"
            raise YtDlpError(
                message,
                code="metadata_probe_failed",
                returncode=completed.returncode,
            )

        try:
            parsed = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise YtDlpError(
                "yt-dlp returned invalid JSON metadata",
                code="invalid_metadata_json",
            ) from exc

        if not isinstance(parsed, dict):
            raise YtDlpError(
                "yt-dlp returned non-object JSON metadata",
                code="invalid_metadata_shape",
            )
        return parsed

    def download_media(self, url: str, target_dir: str | Path) -> dict[str, Any]:
        output_dir = Path(target_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_template = "%(id)s.%(ext)s"
        args = [
            self.binary,
            "--no-playlist",
            "--format",
            "bv*+ba/b",
            "--merge-output-format",
            "mp4",
            "--paths",
            str(output_dir),
            "--output",
            output_template,
            url,
        ]
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
            raise YtDlpError(
                f"{self.binary} not found; install yt-dlp to download media",
                code="binary_not_found",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise YtDlpError(
                f"{self.binary} timed out while downloading media",
                code="media_download_timeout",
            ) from exc

        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "unknown yt-dlp error"
            raise YtDlpError(
                message,
                code="media_download_failed",
                returncode=completed.returncode,
            )

        media_files = [
            str(path)
            for path in sorted(output_dir.iterdir())
            if path.is_file()
        ]
        return {
            "status": "downloaded",
            "target_dir": str(output_dir),
            "output_template": output_template,
            "media_files": media_files,
            "returncode": completed.returncode,
        }
