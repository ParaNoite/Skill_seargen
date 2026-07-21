"""Thin wrappers around external commands and services."""

from .ffmpeg import FfmpegClient, FfmpegError
from .newapi import NewApiClient, NewApiError
from .yt_dlp import YtDlpClient, YtDlpError

__all__ = [
    "FfmpegClient",
    "FfmpegError",
    "NewApiClient",
    "NewApiError",
    "YtDlpClient",
    "YtDlpError",
]
