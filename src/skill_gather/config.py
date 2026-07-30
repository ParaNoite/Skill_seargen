from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .integrations.faster_whisper import is_faster_whisper_model


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class NewApiConfig:
    base_url: str
    api_key_env: str
    vision_model: str
    asr_model: str
    distiller_model: str
    judge_model: str


@dataclass(frozen=True, slots=True)
class AppConfig:
    provider: str
    output_dir: str
    run_dir: str
    newapi: NewApiConfig


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8-sig") as file:
        raw = json.load(file)
    return parse_config(raw)


def parse_config(raw: dict[str, Any]) -> AppConfig:
    defaults = raw.get("defaults", {})
    provider = defaults.get("provider", "newapi")
    if provider != "newapi":
        raise ConfigError("v0.1 只支持 newapi provider。")

    providers = raw.get("providers", {})
    newapi_raw = providers.get("newapi")
    if not isinstance(newapi_raw, dict):
        raise ConfigError("配置缺少 providers.newapi。")

    required = [
        "base_url",
        "api_key_env",
        "vision_model",
        "asr_model",
        "distiller_model",
        "judge_model",
    ]
    missing = [key for key in required if not newapi_raw.get(key)]
    if missing:
        joined = ", ".join(missing)
        raise ConfigError(f"配置缺少 newapi 字段：{joined}。")

    asr_model = str(newapi_raw["asr_model"]).strip()
    if not is_faster_whisper_model(asr_model):
        raise ConfigError("ASR 是主链路必需能力；newapi.asr_model 必须写成 faster-whisper:<模型名或本地模型路径>。")

    return AppConfig(
        provider=provider,
        output_dir=defaults.get("output_dir", "./skills"),
        run_dir=defaults.get("run_dir", "./runs"),
        newapi=NewApiConfig(**{key: newapi_raw[key] for key in required}),
    )
