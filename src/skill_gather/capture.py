"""Structured browser evidence capture and offline TED showcase generation."""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from .runs import read_json, write_json


CAPTURE_EVENTS = {
    "story_context",
    "source_selection",
    "evidence_generation",
    "course_skill_result",
    "judge_result",
    "interactive_success",
    "bug_reproduction",
    "bug_fixed",
}
INTERNAL_EVENTS = {"bug_reproduction", "bug_fixed"}
SHOWCASE_EVENTS = CAPTURE_EVENTS - INTERNAL_EVENTS
SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(authorization|cookie|set-cookie|x-api-key|api[_-]?key|access[_-]?token|refresh[_-]?token|password)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{20,})"),
)
QUERY_RE = re.compile(r"([?&][^=&#]+)=([^&#]*)")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "-", str(value)).strip("-")
    return cleaned or "capture"


def _relative_safe(path: str | Path, run_root: Path) -> str:
    raw = str(path).strip()
    if not raw:
        return ""
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(run_root.resolve())
        except ValueError as exc:
            raise ValueError("捕获产物路径必须位于监督 run 目录内") from exc
    normalized = candidate.as_posix()
    if normalized in {"", "."} or normalized.startswith("../") or "/../" in normalized:
        raise ValueError("捕获产物路径不能越过监督 run 目录")
    return normalized


def _public_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return "[已隐藏]"
    if parsed.scheme and parsed.netloc:
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return QUERY_RE.sub(r"\1[已隐藏]", raw.split("#", 1)[0])


def contains_sensitive(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return any(pattern.search(text) for pattern in SENSITIVE_PATTERNS)


def redact_capture_metadata(value: Any) -> Any:
    """Redact secrets recursively without mutating source artifacts."""

    if isinstance(value, dict):
        return {str(key): redact_capture_metadata(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_capture_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [redact_capture_metadata(item) for item in value]
    if not isinstance(value, str):
        return value
    redacted = value
    for pattern in SENSITIVE_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1) if match.lastindex else '敏感信息'}=[已隐藏]", redacted)
    return QUERY_RE.sub(r"\1[已隐藏]", redacted.split("#", 1)[0])


@dataclass(slots=True)
class CaptureRecord:
    capture_id: str
    supervision_id: str
    theme_id: str
    topic: str
    stage: str
    event: str
    source: str = "playwright"
    screenshot_path: str = ""
    trace_path: str = ""
    page_url: str = ""
    narrative_beats: list[str] = field(default_factory=list)
    showcase_reason: str = ""
    narrative_score: int = 0
    information_score: int = 0
    visual_score: int = 0
    evidence_score: int = 0
    artifact_paths: list[str] = field(default_factory=list)
    artifact_preview_path: str = ""
    selected_for_showcase: bool = False
    redaction_applied: bool = True
    redaction_blocked: bool = False
    created_at: str = field(default_factory=_now)

    @property
    def showcase_score(self) -> int:
        return max(0, min(100, self.narrative_score + self.information_score + self.visual_score + self.evidence_score))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["showcase_score"] = self.showcase_score
        return payload

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CaptureRecord":
        known = {field for field in cls.__dataclass_fields__}
        return cls(**{key: item for key, item in value.items() if key in known})


@dataclass(slots=True)
class CaptureIndex:
    supervision_id: str
    records: list[CaptureRecord] = field(default_factory=list)
    generated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        showcase_ids = [record.capture_id for record in self.records if record.selected_for_showcase]
        return {
            "schema_version": "1.0",
            "supervision_id": self.supervision_id,
            "generated_at": self.generated_at,
            "records": [record.to_dict() for record in self.records],
            "showcase_capture_ids": showcase_ids,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CaptureIndex":
        return cls(
            supervision_id=str(value.get("supervision_id", "")),
            records=[CaptureRecord.from_dict(item) for item in value.get("records", []) if isinstance(item, dict)],
            generated_at=str(value.get("generated_at", _now())),
        )


def index_path(run_root: str | Path) -> Path:
    return Path(run_root) / "capture-index.json"


def load_capture_index(run_root: str | Path, supervision_id: str = "") -> CaptureIndex:
    path = index_path(run_root)
    if not path.exists():
        return CaptureIndex(supervision_id=supervision_id)
    return CaptureIndex.from_dict(read_json(path))


def _write_artifact_preview(run_root: Path, record: CaptureRecord) -> str:
    if not record.artifact_paths:
        return ""
    stage_root = run_root / "captures" / _slug(record.theme_id) / _slug(record.stage)
    stage_root.mkdir(parents=True, exist_ok=True)
    sections: list[str] = []
    for relative in record.artifact_paths:
        source = run_root / relative
        if not source.is_file():
            continue
        try:
            content = source.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        safe_content = html.escape(redact_capture_metadata(content))
        sections.append(f"<section><h2>{html.escape(Path(relative).name)}</h2><pre>{safe_content}</pre></section>")
    if not sections:
        return ""
    output = stage_root / "artifact-preview.html"
    body = "\n".join(sections)
    output.write_text(
        "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>产物预览</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;color:#1f2937}"
        "section{margin:24px 0}pre{white-space:pre-wrap;background:#f3f4f6;padding:18px;border-radius:8px;line-height:1.55}</style>"
        f"<body><h1>监工产物预览</h1>{body}</body></html>",
        encoding="utf-8",
    )
    return output.relative_to(run_root).as_posix()


def register_capture(
    run_root: str | Path,
    *,
    supervision_id: str,
    theme_id: str,
    topic: str,
    stage: str,
    event: str,
    source: str = "playwright",
    screenshot_path: str = "",
    trace_path: str = "",
    page_url: str = "",
    narrative_beats: Iterable[str] = (),
    showcase_reason: str = "",
    narrative_score: int = 0,
    information_score: int = 0,
    visual_score: int = 0,
    evidence_score: int = 0,
    artifact_paths: Iterable[str] = (),
    page_text: str = "",
    strict_redaction: bool = True,
) -> CaptureRecord:
    if event not in CAPTURE_EVENTS:
        raise ValueError(f"不支持的截图事件：{event}")
    if source not in {"playwright", "artifact_preview"}:
        raise ValueError("capture source 必须是 playwright 或 artifact_preview")
    if not 0 <= narrative_score <= 40 or not 0 <= information_score <= 25:
        raise ValueError("叙事分数或信息分数超出范围")
    if not 0 <= visual_score <= 20 or not 0 <= evidence_score <= 15:
        raise ValueError("视觉分数或证据分数超出范围")
    root = Path(run_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    normalized_screenshot = _relative_safe(screenshot_path, root) if screenshot_path else ""
    normalized_trace = _relative_safe(trace_path, root) if trace_path else ""
    normalized_artifacts = [_relative_safe(path, root) for path in artifact_paths if str(path).strip()]
    blocked = bool(strict_redaction and (contains_sensitive(page_text) or contains_sensitive(page_url)))
    record = CaptureRecord(
        capture_id=f"capture-{len(load_capture_index(root, supervision_id).records) + 1:04d}",
        supervision_id=supervision_id,
        theme_id=_slug(theme_id),
        topic=redact_capture_metadata(topic),
        stage=_slug(stage),
        event=event,
        source=source,
        screenshot_path=normalized_screenshot,
        trace_path=normalized_trace,
        page_url=_public_url(page_url),
        narrative_beats=[redact_capture_metadata(item) for item in narrative_beats if str(item).strip()],
        showcase_reason=redact_capture_metadata(showcase_reason),
        narrative_score=narrative_score,
        information_score=information_score,
        visual_score=visual_score,
        evidence_score=evidence_score,
        artifact_paths=normalized_artifacts,
        redaction_applied=True,
        redaction_blocked=blocked,
    )
    if blocked:
        record.selected_for_showcase = False
    else:
        record.artifact_preview_path = _write_artifact_preview(root, record)
    index = load_capture_index(root, supervision_id)
    duplicate = next(
        (
            existing
            for existing in index.records
            if (existing.theme_id, existing.stage, existing.event, existing.screenshot_path, existing.trace_path)
            == (record.theme_id, record.stage, record.event, record.screenshot_path, record.trace_path)
        ),
        None,
    )
    if duplicate is not None:
        return duplicate
    index.records.append(record)
    index.generated_at = _now()
    write_json(index_path(root), index.to_dict())
    return record


def select_showcase_frames(
    run_root: str | Path,
    *,
    ted_relevance_score: int,
    threshold: int = 70,
    max_frames: int = 5,
    theme_id: str = "",
) -> CaptureIndex:
    root = Path(run_root)
    index = load_capture_index(root)
    selected_theme = _slug(theme_id) if theme_id else ""
    for record in index.records:
        if not selected_theme or record.theme_id == selected_theme:
            record.selected_for_showcase = False
    if ted_relevance_score < threshold:
        write_json(index_path(root), index.to_dict())
        return index
    candidates = [
        record
        for record in index.records
        if record.event in SHOWCASE_EVENTS
        and record.screenshot_path
        and not record.redaction_blocked
        and record.showcase_score >= 70
        and (not selected_theme or record.theme_id == selected_theme)
    ]
    candidates.sort(key=lambda item: (-item.showcase_score, item.created_at, item.capture_id))
    selected: list[CaptureRecord] = []
    covered_beats: set[str] = set()
    for record in candidates:
        beats = set(record.narrative_beats)
        if len(selected) < max_frames and (not beats or not beats.issubset(covered_beats)):
            record.selected_for_showcase = True
            selected.append(record)
            covered_beats.update(beats)
    for record in candidates:
        if len(selected) >= max_frames:
            break
        if not record.selected_for_showcase:
            record.selected_for_showcase = True
            selected.append(record)
    index.generated_at = _now()
    write_json(index_path(root), index.to_dict())
    return index


def render_offline_showcase(run_root: str | Path, index: CaptureIndex | None = None) -> tuple[Path, Path]:
    root = Path(run_root)
    index = index or load_capture_index(root)
    selected = [record for record in index.records if record.selected_for_showcase and record.event in SHOWCASE_EVENTS]
    markdown_lines = ["# TED 监工展示素材", "", f"监督任务：`{index.supervision_id}`", ""]
    html_cards: list[str] = []
    for position, record in enumerate(selected, 1):
        title = f"{position}. {record.event} / {record.stage}"
        markdown_lines.extend([
            f"## {title}",
            f"- 主题：{record.topic}",
            f"- 叙事节点：{', '.join(record.narrative_beats) or '未指定'}",
            f"- 展示理由：{record.showcase_reason or '关键状态画面'}",
            f"- 评分：{record.showcase_score}/100",
            f"- 截图：`{record.screenshot_path}`",
            f"- Trace：`{record.trace_path or '未记录'}`",
            "",
        ])
        image = html.escape(record.screenshot_path)
        preview = html.escape(record.artifact_preview_path)
        html_cards.append(
            f"<article><h2>{html.escape(title)}</h2><p><b>主题：</b>{html.escape(record.topic)}</p>"
            f"<p><b>叙事节点：</b>{html.escape(', '.join(record.narrative_beats) or '未指定')}　"
            f"<b>评分：</b>{record.showcase_score}/100</p>"
            f"<p>{html.escape(record.showcase_reason or '关键状态画面')}</p>"
            f"<img src=\"{image}\" alt=\"{html.escape(record.event)}\">"
            + (f"<p><a href=\"{preview}\">打开产物预览</a></p>" if preview else "")
            + "</article>"
        )
    md_path = root / "capture-index.md"
    html_path = root / "showcase.html"
    md_path.write_text("\n".join(markdown_lines).rstrip() + "\n", encoding="utf-8")
    html_path.write_text(
        "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>TED 监工展示素材</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:1200px;margin:32px auto;padding:0 20px;color:#172033}"
        "article{border:1px solid #d7dce5;border-radius:8px;padding:20px;margin:24px 0}"
        "img{display:block;max-width:100%;height:auto;border:1px solid #cbd5e1;border-radius:6px}"
        "a{color:#075985}</style><body><h1>TED 监工展示素材</h1>"
        f"<p>监督任务：{html.escape(index.supervision_id)}</p>{''.join(html_cards)}</body></html>",
        encoding="utf-8",
    )
    return md_path, html_path
