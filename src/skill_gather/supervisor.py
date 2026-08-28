"""Persistent state helpers for the Codex-driven project supervisor."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .runs import read_json, write_json


DEFAULT_ALLOWED_PATHS = (
    "src",
    "tests",
    "frontend",
    "frontend-school",
    "configs",
    "scripts",
    "docs",
)


class SupervisorConfigError(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z._-]+", "-", value).strip("-")
    return slug or "supervision"


@dataclass(frozen=True, slots=True)
class CaptureConfig:
    """Configuration for event-driven TED evidence capture."""

    enabled: bool = True
    mode: str = "ted_story_event"
    ted_story_source: str = "README.md"
    ted_relevance_threshold: int = 70
    max_showcase_frames: int = 5
    require_trace: bool = True
    strict_redaction: bool = True
    offline_html: bool = True

    @classmethod
    def from_dict(cls, raw: Any) -> "CaptureConfig":
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise SupervisorConfigError("capture 必须是对象")
        enabled = raw.get("enabled", True)
        mode = raw.get("mode", "ted_story_event")
        source = raw.get("ted_story_source", "README.md")
        threshold = raw.get("ted_relevance_threshold", 70)
        max_frames = raw.get("max_showcase_frames", 5)
        require_trace = raw.get("require_trace", True)
        strict_redaction = raw.get("strict_redaction", True)
        offline_html = raw.get("offline_html", True)
        if not isinstance(enabled, bool) or not isinstance(require_trace, bool):
            raise SupervisorConfigError("capture.enabled 和 capture.require_trace 必须是布尔值")
        if not isinstance(strict_redaction, bool) or not isinstance(offline_html, bool):
            raise SupervisorConfigError("capture.strict_redaction 和 capture.offline_html 必须是布尔值")
        if not isinstance(mode, str) or not mode.strip():
            raise SupervisorConfigError("capture.mode 必须是非空字符串")
        if not isinstance(source, str) or not source.strip():
            raise SupervisorConfigError("capture.ted_story_source 必须是非空字符串")
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 100:
            raise SupervisorConfigError("capture.ted_relevance_threshold 必须在 0 到 100 之间")
        if isinstance(max_frames, bool) or not isinstance(max_frames, int) or not 1 <= max_frames <= 50:
            raise SupervisorConfigError("capture.max_showcase_frames 必须在 1 到 50 之间")
        return cls(
            enabled=enabled,
            mode=mode.strip(),
            ted_story_source=source.strip(),
            ted_relevance_threshold=threshold,
            max_showcase_frames=max_frames,
            require_trace=require_trace,
            strict_redaction=strict_redaction,
            offline_html=offline_html,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "ted_story_source": self.ted_story_source,
            "ted_relevance_threshold": self.ted_relevance_threshold,
            "max_showcase_frames": self.max_showcase_frames,
            "require_trace": self.require_trace,
            "strict_redaction": self.strict_redaction,
            "offline_html": self.offline_html,
        }


@dataclass(frozen=True, slots=True)
class SupervisorConfig:
    max_fix_rounds: int = 3
    max_normal_topics: int = 5
    allowed_paths: tuple[str, ...] = DEFAULT_ALLOWED_PATHS
    ted_critical_topics: tuple[str, ...] = ()
    opening_game: dict[str, Any] = field(default_factory=dict)
    capture: CaptureConfig = field(default_factory=CaptureConfig)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SupervisorConfig":
        if not isinstance(raw, dict):
            raise SupervisorConfigError("监工配置必须是对象")
        values = raw.get("supervisor", raw)
        if not isinstance(values, dict):
            raise SupervisorConfigError("supervisor 必须是对象")
        max_fix_rounds = values.get("max_fix_rounds", 3)
        max_normal_topics = values.get("max_normal_topics", 5)
        if isinstance(max_fix_rounds, bool) or not isinstance(max_fix_rounds, int) or max_fix_rounds < 1:
            raise SupervisorConfigError("max_fix_rounds 必须是正整数")
        if isinstance(max_normal_topics, bool) or not isinstance(max_normal_topics, int) or max_normal_topics < 1:
            raise SupervisorConfigError("max_normal_topics 必须是正整数")
        allowed_paths = values.get("allowed_paths", list(DEFAULT_ALLOWED_PATHS))
        critical_topics = values.get("ted_critical_topics", [])
        opening_game = values.get("opening_game", {})
        capture = CaptureConfig.from_dict(values.get("capture", {}))
        if not isinstance(allowed_paths, list) or not all(isinstance(item, str) and item.strip() for item in allowed_paths):
            raise SupervisorConfigError("allowed_paths 必须是非空字符串数组")
        if not isinstance(critical_topics, list) or not all(isinstance(item, str) and item.strip() for item in critical_topics):
            raise SupervisorConfigError("ted_critical_topics 必须是字符串数组")
        if not isinstance(opening_game, dict):
            raise SupervisorConfigError("opening_game 必须是对象")
        return cls(
            max_fix_rounds=max_fix_rounds,
            max_normal_topics=max_normal_topics,
            allowed_paths=tuple(item.strip() for item in allowed_paths),
            ted_critical_topics=tuple(item.strip() for item in critical_topics),
            opening_game=opening_game,
            capture=capture,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_fix_rounds": self.max_fix_rounds,
            "max_normal_topics": self.max_normal_topics,
            "allowed_paths": list(self.allowed_paths),
            "ted_critical_topics": list(self.ted_critical_topics),
            "opening_game": self.opening_game,
            "capture": self.capture.to_dict(),
        }


def load_supervisor_config(path: str | Path) -> SupervisorConfig:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise SupervisorConfigError(f"监工配置 JSON 无效：{exc}") from exc
    return SupervisorConfig.from_dict(raw)


class SupervisorStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def run_path(self, supervision_id: str) -> Path:
        return self.root / _safe_slug(supervision_id)

    def state_path(self, supervision_id: str) -> Path:
        return self.run_path(supervision_id) / "supervision_state.json"

    def latest_active(self) -> dict[str, Any] | None:
        if not self.root.exists():
            return None
        candidates: list[dict[str, Any]] = []
        for path in self.root.glob("*/supervision_state.json"):
            try:
                state = read_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(state, dict) and state.get("schema_version") == "1.0" and state.get("status") == "active":
                candidates.append(state)
        if not candidates:
            return None
        return max(candidates, key=lambda item: str(item.get("updated_at", "")))

    def start_or_resume(self, config: SupervisorConfig) -> tuple[dict[str, Any], bool]:
        state = self.latest_active()
        if state is not None:
            return state, True
        return self.start(config), False

    def start(self, config: SupervisorConfig, supervision_id: str | None = None) -> dict[str, Any]:
        identifier = supervision_id or f"supervision-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        path = self.state_path(identifier)
        if path.exists():
            return self.load(identifier)
        state = {
            "schema_version": "1.0",
            "supervision_id": identifier,
            "status": "active",
            "created_at": _now(),
            "updated_at": _now(),
            "config": config.to_dict(),
            "theme_queue": [],
            "agent_reports": [],
            "fix_rounds": [],
            "notes": ["由 Codex 主 Agent 驱动；此状态文件不执行自主后台任务。"],
        }
        self.save(state)
        return state

    def load(self, supervision_id: str) -> dict[str, Any]:
        path = self.state_path(supervision_id)
        if not path.exists():
            raise FileNotFoundError(f"找不到监工任务：{supervision_id}")
        state = read_json(path)
        if not isinstance(state, dict) or state.get("schema_version") != "1.0":
            raise SupervisorConfigError("监工状态格式不兼容")
        return state

    def save(self, state: dict[str, Any]) -> Path:
        identifier = str(state.get("supervision_id", "")).strip()
        if not identifier:
            raise SupervisorConfigError("监工状态缺少 supervision_id")
        state["updated_at"] = _now()
        path = self.state_path(identifier)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, state)
        return path

    def add_theme(
        self,
        supervision_id: str,
        *,
        topic: str,
        reason_selected: str,
        utility_score: int = 0,
        popularity_signals: list[str] | None = None,
        ted_relevance_score: int = 0,
        narrative_beats: list[str] | None = None,
        showcase_reason: str = "",
        expected_artifacts: list[str] | None = None,
    ) -> dict[str, Any]:
        if not topic.strip() or not reason_selected.strip():
            raise SupervisorConfigError("主题和选题理由不能为空")
        if not 0 <= utility_score <= 100:
            raise SupervisorConfigError("utility_score 必须在 0 到 100 之间")
        if not 0 <= ted_relevance_score <= 100:
            raise SupervisorConfigError("ted_relevance_score 必须在 0 到 100 之间")
        beats = [str(item).strip() for item in (narrative_beats or []) if str(item).strip()]
        artifacts = [str(item).strip() for item in (expected_artifacts or []) if str(item).strip()]
        state = self.load(supervision_id)
        config = state["config"]
        critical_topics = {str(item).casefold() for item in config.get("ted_critical_topics", [])}
        threshold = int(config.get("capture", {}).get("ted_relevance_threshold", 70))
        level = "ted_critical" if topic.casefold() in critical_topics or ted_relevance_score >= threshold else "normal"
        item = {
            "theme_id": f"theme-{len(state['theme_queue']) + 1:03d}",
            "topic": topic.strip(),
            "reason_selected": reason_selected.strip(),
            "utility_score": utility_score,
            "popularity_signals": popularity_signals or [],
            "acceptance_level": level,
            "ted_relevance_score": ted_relevance_score,
            "narrative_beats": beats,
            "showcase_reason": showcase_reason.strip(),
            "expected_artifacts": artifacts,
            "status": "planned",
            "created_at": _now(),
        }
        state["theme_queue"].append(item)
        self.save(state)
        return item
