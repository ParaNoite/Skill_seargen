"""Thin wrappers around external commands and services."""

from .yt_dlp import YtDlpClient, YtDlpError

__all__ = ["YtDlpClient", "YtDlpError"]
