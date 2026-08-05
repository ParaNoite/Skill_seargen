from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Callable

from .models import TopicSourceCandidate, TopicTask
from .runs import write_json
from .web_text import WebPage, WebTextError, fetch_public_page, score_web_page


@dataclass(slots=True)
class WebProcessingResult:
    evidence: list[dict[str, object]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


def process_web_sources(
    task: TopicTask,
    run_root: Path,
    *,
    timeout_sec: int = 15,
    fetcher: Callable[..., WebPage] = fetch_public_page,
) -> WebProcessingResult:
    """Create webpage evidence from explicitly selected normal or technical sources."""
    if task.mode not in {"normal", "technical"}:
        raise ValueError("网页来源处理只支持 normal 或 technical 主题模式。")
    package = task.package
    if package is None:
        raise ValueError("主题任务缺少主题包索引")
    evidence_dir = run_root / package.evidence
    references_dir = run_root / package.references
    evidence_dir.mkdir(parents=True, exist_ok=True)
    references_dir.mkdir(parents=True, exist_ok=True)
    result = WebProcessingResult()

    for candidate in task.selected_sources:
        if candidate.source_type != "web":
            result.skipped.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "url": candidate.canonical_url or candidate.url,
                    "reason": f"{candidate.source_type} 来源不属于 v0.5 网页处理范围",
                }
            )
            continue
        try:
            page = fetcher(candidate.canonical_url or candidate.url, timeout_sec=timeout_sec)
            quality_score, new_flags = score_web_page(
                page.text,
                candidate_quality=candidate.quality_score,
            )
            candidate.quality_score = quality_score
            candidate.risk_flags = _unique(candidate.risk_flags + new_flags)
            source_number = len(result.evidence) + 1
            evidence_record: dict[str, object] = {
                "source_id": f"S{source_number}",
                "candidate_id": candidate.candidate_id,
                "source_type": "web",
                "url": page.final_url,
                "title": page.title or candidate.title or page.final_url,
                "fetched_at": datetime.now(UTC).isoformat(),
                "content_type": page.content_type,
                "quality_score": quality_score,
                "risk_flags": candidate.risk_flags,
                "snapshot_truncated": page.truncated,
                "text": page.text,
            }
            basename = _safe_filename(candidate.candidate_id or f"web-{source_number}")
            write_json(evidence_dir / f"{basename}.json", evidence_record)
            (references_dir / f"{basename}.txt").write_text(page.text + "\n", encoding="utf-8")
            result.evidence.append(evidence_record)
        except WebTextError as exc:
            result.failures.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "url": candidate.canonical_url or candidate.url,
                    "reason": str(exc),
                }
            )

    audit = {
        "topic": task.topic,
        "processed_at": datetime.now(UTC).isoformat(),
        "successful_sources": [
            {key: record[key] for key in ("source_id", "candidate_id", "url", "quality_score", "risk_flags")}
            for record in result.evidence
        ],
        "failed_sources": result.failures,
        "skipped_sources": result.skipped,
    }
    write_json(run_root / "web_processing_audit.json", audit)
    return result


def write_knowledge_markdown(task: TopicTask, run_root: Path, result: WebProcessingResult) -> Path:
    package = task.package
    if package is None:
        raise ValueError("主题包缺少 knowledge.md 路径")
    _ensure_knowledge_path(task)
    if not result.evidence:
        raise ValueError("没有可用于生成 knowledge.md 的网页证据")

    lines = [f"# {task.topic}", "", "## 摘要", ""]
    lines.append(f"本总结整合了 {len(result.evidence)} 个已确认的公开网页来源。结论仅基于保存的网页文本快照，不能替代原始页面的完整上下文。")
    lines.extend(["", "## 关键要点", ""])
    for record in result.evidence:
        lines.append(f"- **{record['title']}**：{_excerpt(str(record['text']))} [{record['source_id']}]")

    steps = _find_steps(result.evidence)
    lines.extend(["", "## 步骤或方法", ""])
    if steps:
        lines.extend(f"- {step}" for step in steps)
    else:
        lines.append("- 网页文本中未识别到足够明确、可逐步执行的方法；请结合下列原始来源复核。")

    lines.extend(["", "## 结论分歧", ""])
    lines.append("- 未检测到可由规则稳定判定的相互矛盾结论。该结果仅表示自动检测未发现冲突，不代表来源之间不存在细微差异。")

    lines.extend(["", "## 证据不足与处理限制", ""])
    if result.failures:
        lines.extend(f"- {item['url']}：未能提取正文，原因：{item['reason']}" for item in result.failures)
    if result.skipped:
        lines.extend(f"- {item['url']}：{item['reason']}。" for item in result.skipped)
    for record in result.evidence:
        flags = record["risk_flags"]
        if flags:
            lines.append(f"- [{record['source_id']}] 风险标记：{', '.join(str(flag) for flag in flags)}。")
    if not result.failures and not result.skipped and not any(record["risk_flags"] for record in result.evidence):
        lines.append("- 未发现额外的来源处理限制；仍建议在实际执行前阅读原始页面。")

    lines.extend(["", "## 来源", ""])
    for record in result.evidence:
        lines.append(f"- [{record['source_id']}] {record['title']}：{record['url']}")

    path = run_root / package.knowledge
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _excerpt(value: str, limit: int = 180) -> str:
    clean = re.sub(r"\s+", " ", value).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _find_steps(evidence: list[dict[str, object]]) -> list[str]:
    markers = ("步骤", "首先", "然后", "接着", "安装", "配置", "使用", "方法")
    steps: list[str] = []
    for record in evidence:
        sentences = re.split(r"(?<=[。！？.!?])\s*", str(record["text"]))
        for sentence in sentences:
            compact = re.sub(r"\s+", " ", sentence).strip()
            if len(compact) >= 20 and any(marker in compact for marker in markers):
                steps.append(f"{_excerpt(compact, 200)} [{record['source_id']}]")
            if len(steps) == 6:
                return steps
    return steps


def _safe_filename(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "-", value).strip("-") or "web-source"


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _ensure_knowledge_path(task: TopicTask) -> None:
    """Migrate v0.4 topic packages lazily without invalidating their selected sources."""
    if task.package is not None and not task.package.knowledge:
        task.package.knowledge = f"{task.package.root}/knowledge.md"
