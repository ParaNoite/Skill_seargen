"""Thin wrappers around external commands and services."""

from .ffmpeg import FfmpegClient, FfmpegError
from .yt_dlp import YtDlpClient, YtDlpError

__all__ = ["FfmpegClient", "FfmpegError", "YtDlpClient", "YtDlpError"]
