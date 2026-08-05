from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .integrations.faster_whisper import is_faster_whisper_model
from .models import JUDGE_DIFFICULTIES, TopicBudget, TopicCachePolicy


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
class TopicDefaults:
    budget: TopicBudget
    cache: TopicCachePolicy
    judge_difficulty: str = "standard"


@dataclass(frozen=True, slots=True)
class SearchConfig:
    cache_ttl_sec: int = 86400
    timeout_sec: int = 15
    max_queries: int = 4
    per_provider_results: int = 10
    bilibili_search_url: str = "https://api.bilibili.com/x/web-interface/search/type"
    bilibili_web_search_url: str = "https://search.bilibili.com/all"
    github_api_url: str = "https://api.github.com"
    github_token_env: str = "GITHUB_TOKEN"
    searxng_base_url: str = ""
    use_newapi_query_expansion: bool = False
    use_newapi_candidate_assessment: bool = False


@dataclass(frozen=True, slots=True)
class AppConfig:
    provider: str
    output_dir: str
    run_dir: str
    newapi: NewApiConfig
    topic_defaults: TopicDefaults
    search: SearchConfig


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

    try:
        topic_defaults = _parse_topic_defaults(raw.get("topic_defaults", {}))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"主题任务配置无效：{exc}") from exc

    return AppConfig(
        provider=provider,
        output_dir=defaults.get("output_dir", "./skills"),
        run_dir=defaults.get("run_dir", "./runs"),
        newapi=NewApiConfig(**{key: newapi_raw[key] for key in required}),
        topic_defaults=topic_defaults,
        search=_parse_search_config(raw.get("search", {})),
    )


def _parse_topic_defaults(raw: Any) -> TopicDefaults:
    if not isinstance(raw, dict):
        raise ValueError("topic_defaults 必须是对象")

    budget_keys = {
        "max_candidates",
        "max_selected_sources",
        "max_video_duration_sec",
        "max_model_calls",
        "max_estimated_cost_usd",
        "max_runtime_sec",
    }
    budget = TopicBudget.from_dict({key: raw[key] for key in budget_keys if key in raw})
    cache = TopicCachePolicy.from_dict(
        {key: raw[key] for key in {"reuse_cache", "refresh_cache"} if key in raw}
    )
    if not isinstance(cache.reuse_cache, bool) or not isinstance(cache.refresh_cache, bool):
        raise ValueError("缓存策略必须使用布尔值")

    judge_difficulty = str(raw.get("judge_difficulty", "standard"))
    if judge_difficulty not in JUDGE_DIFFICULTIES:
        raise ValueError("judge_difficulty 必须是 lenient、standard、strict 或 off")
    return TopicDefaults(budget=budget, cache=cache, judge_difficulty=judge_difficulty)


def _parse_search_config(raw: Any) -> SearchConfig:
    if not isinstance(raw, dict):
        raise ConfigError("search 必须是对象")
    values = asdict(SearchConfig())
    values.update({key: value for key, value in raw.items() if key in values})
    for key in ("cache_ttl_sec", "timeout_sec", "max_queries", "per_provider_results"):
        if not isinstance(values[key], int) or values[key] <= 0:
            raise ConfigError(f"search.{key} 必须是正整数")
    for key in ("use_newapi_query_expansion", "use_newapi_candidate_assessment"):
        if not isinstance(values[key], bool):
            raise ConfigError(f"search.{key} 必须是布尔值")
    return SearchConfig(**values)
