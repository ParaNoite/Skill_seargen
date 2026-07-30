"""Thin wrappers around external commands and services."""

from .ffmpeg import FfmpegClient, FfmpegError
from .faster_whisper import FasterWhisperClient, is_faster_whisper_model
from .newapi import NewApiClient, NewApiError
from .yt_dlp import YtDlpClient, YtDlpError

__all__ = [
    "FasterWhisperClient",
    "FfmpegClient",
    "FfmpegError",
    "NewApiClient",
    "NewApiError",
    "YtDlpClient",
    "YtDlpError",
    "is_faster_whisper_model",
]
