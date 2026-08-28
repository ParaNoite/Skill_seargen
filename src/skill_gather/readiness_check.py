from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import AppConfig
from .integrations.faster_whisper import FasterWhisperClient
from .integrations.newapi import NewApiClient, NewApiError
from .search import (
    BilibiliSearchProvider,
    GitHubSearchProvider,
    SearchProviderError,
    SearXNGProvider,
)


@dataclass(frozen=True, slots=True)
class ReadinessProbe:
    name: str
    group: str
    label: str
    check: Callable[[], dict[str, Any]]


ProgressCallback = Callable[[dict[str, Any]], None]


def run_readiness_check(
    config: AppConfig,
    *,
    load_asr_model: bool = True,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    probes = [
        ReadinessProbe("config", "core", "配置文件", lambda: _pass("config loaded")),
        ReadinessProbe("yt-dlp", "tooling", "yt-dlp", lambda: _command_available("yt-dlp")),
        ReadinessProbe("ffmpeg", "tooling", "ffmpeg", lambda: _command_available("ffmpeg")),
        ReadinessProbe("bilibili_search", "search", "Bilibili 搜索", lambda: _search_provider(
            BilibiliSearchProvider(
                config.search.bilibili_search_url,
                config.search.bilibili_web_search_url,
                timeout_sec=config.search.timeout_sec,
            ),
            ["Godot 教程"],
        )),
        ReadinessProbe("searxng", "search", "SearXNG 搜索", lambda: _search_provider(
            SearXNGProvider(config.search.searxng_base_url, timeout_sec=config.search.timeout_sec),
            ["Godot 教程"],
        )),
        ReadinessProbe("github_search", "search", "GitHub 搜索", lambda: _search_provider(
            GitHubSearchProvider(
                config.search.github_api_url,
                config.search.github_token_env,
                timeout_sec=config.search.timeout_sec,
            ),
            ["Godot NavigationAgent3D"],
        )),
        ReadinessProbe("newapi_vision", "model", "NewAPI 视觉模型", lambda: _model_probe(
            config,
            config.newapi.vision_model,
            "vision",
        )),
        ReadinessProbe("newapi_distiller", "model", "NewAPI 蒸馏模型", lambda: _model_probe(
            config,
            config.newapi.distiller_model,
            "text",
        )),
        ReadinessProbe("newapi_judge", "model", "NewAPI Judge 模型", lambda: _model_probe(
            config,
            config.newapi.judge_model,
            "text",
        )),
        ReadinessProbe("local_asr", "model", "本地 faster-whisper ASR", lambda: _asr_probe(
            config,
            load_model=load_asr_model,
        )),
    ]
    total = len(probes)
    for index, probe in enumerate(probes, start=1):
        if on_progress:
            on_progress({
                "event": "probe_started",
                "index": index,
                "total": total,
                "name": probe.name,
                "group": probe.group,
                "label": probe.label,
                "checks": checks,
            })
        check = _run_probe(probe)
        checks.append(check)
        if on_progress:
            on_progress({
                "event": "probe_finished",
                "index": index,
                "total": total,
                "name": probe.name,
                "group": probe.group,
                "label": probe.label,
                "check": check,
                "checks": checks,
            })

    failed = [check for check in checks if check["status"] == "failed"]
    warnings = [check for check in checks if check["status"] == "warning"]
    status = "passed" if not failed and not warnings else "needs_attention" if not failed else "failed"
    result = {
        "status": status,
        "checks": checks,
        "summary": {
            "passed": sum(1 for check in checks if check["status"] == "passed"),
            "warning": len(warnings),
            "failed": len(failed),
            "total": len(checks),
        },
        "notes": [
            "真实功能开通检查会访问已配置的外部搜索服务和 NewAPI 模型。",
            "它不会下载公开视频、保存模型原文、写入 API key，不能替代一次完整真实主题验收。",
        ],
    }
    if on_progress:
        on_progress({
            "event": "finished",
            "index": total,
            "total": total,
            "status": status,
            "checks": checks,
            "result": result,
        })
    return result


def _run_probe(probe: ReadinessProbe) -> dict[str, Any]:
    try:
        result = probe.check()
    except Exception as exc:  # pragma: no cover - last-resort safe summary
        result = _fail(f"{type(exc).__name__}: {exc}")
    return {
        "name": probe.name,
        "group": probe.group,
        "label": probe.label,
        **result,
    }


def _pass(summary: str, **details: Any) -> dict[str, Any]:
    return {"status": "passed", "summary": summary, **details}


def _warn(summary: str, **details: Any) -> dict[str, Any]:
    return {"status": "warning", "summary": summary, **details}


def _fail(summary: str, **details: Any) -> dict[str, Any]:
    return {"status": "failed", "summary": summary, **details}


def _command_available(command: str) -> dict[str, Any]:
    path = shutil.which(command)
    if not path:
        return _fail(f"{command} 未在 PATH 中找到")
    return _pass("可执行文件已找到", path=path)


def _search_provider(provider: Any, queries: list[str]) -> dict[str, Any]:
    try:
        batch = provider.search(queries, max_results=1)
    except SearchProviderError as exc:
        return _fail(exc.code, error_code=exc.code)
    if not batch.results:
        return _warn("请求成功但没有返回候选")
    return _pass("请求成功", result_count=len(batch.results), engine=batch.results[0].engine)


def _model_probe(config: AppConfig, model: str, capability: str) -> dict[str, Any]:
    client = NewApiClient.from_config(config.newapi)
    if client is None:
        return _fail("未找到 NewAPI API Key", error_code="missing_api_key")
    result = client.probe_model(model, capability)
    if result.get("available"):
        return _pass("真实最小请求通过", model=model, capability=capability)
    return _fail(
        str(result.get("summary") or "模型探测失败"),
        model=model,
        capability=capability,
        error_code=result.get("error_code", "model_probe_failed"),
        status_code=result.get("status_code"),
    )


def _asr_probe(config: AppConfig, *, load_model: bool) -> dict[str, Any]:
    client = FasterWhisperClient.from_model(config.newapi.asr_model)
    if not load_model:
        return _warn(
            "已解析 ASR 配置，但未加载模型",
            model=config.newapi.asr_model,
            device=client.device,
            compute_type=client.compute_type,
        )
    try:
        from faster_whisper import WhisperModel
        from huggingface_hub.errors import LocalEntryNotFoundError
        from .integrations.faster_whisper import _configure_hugging_face_environment
    except ImportError:
        return _fail("faster-whisper 或 huggingface_hub 未安装", error_code="faster_whisper_missing")

    model_cache = _configure_hugging_face_environment()
    model_options = {
        "device": client.device,
        "compute_type": client.compute_type,
        "download_root": str(model_cache),
    }
    try:
        try:
            WhisperModel(client.model_name, local_files_only=True, **model_options)
        except LocalEntryNotFoundError:
            WhisperModel(client.model_name, **model_options)
    except Exception as exc:
        return _fail(
            f"ASR 模型加载失败：{exc}",
            error_code="faster_whisper_failed",
            model=config.newapi.asr_model,
            device=client.device,
            compute_type=client.compute_type,
        )
    return _pass(
        "ASR 模型加载成功",
        model=config.newapi.asr_model,
        local_model=client.model_name,
        device=client.device,
        compute_type=client.compute_type,
        cache=str(Path(model_cache)),
    )
