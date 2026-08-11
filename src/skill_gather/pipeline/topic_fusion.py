from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
import re
from typing import Any

from ..models import TopicTask
from ..runs import read_json, write_json


_NEGATIONS = ("禁止", "不能", "无需", "不要", "不可", "不应", "未", "无", "never", "not", "no ", "without", "disable")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|\r?\n+")
_VIDEO_BOILERPLATE = (
    "欢迎来到", "感谢观看", "感谢收看", "感谢同学", "下期再见", "点赞", "投币", "关注", "订阅", "一键三连",
    "如果该系列教程", "估计还是要间隔", "陷入新的深坑", "我太想进步",
)
_VIDEO_TECHNICAL_TERMS = (
    "godot", "state", "chart", "fsm", "github", "状态", "插件", "节点", "场景", "代码", "脚本",
    "配置", "安装", "下载", "事件", "信号", "转换", "条件", "函数", "变量", "项目", "运行",
)


@dataclass(slots=True)
class FusionEvidence:
    source_id: str
    candidate_id: str
    source_type: str
    title: str
    url: str
    evidence_path: str
    locator: str
    claim: str
    confidence: float
    quality_score: int
    risk_flags: list[str] = field(default_factory=list)

    def reference(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "candidate_id": self.candidate_id,
            "source_type": self.source_type,
            "title": self.title,
            "url": self.url,
            "evidence_path": self.evidence_path,
            "locator": self.locator,
            "claim": self.claim,
            "confidence": self.confidence,
        }


def fuse_topic_evidence(task: TopicTask, run_root: Path) -> dict[str, object]:
    """Fuse saved topic evidence without inventing claims absent from source artifacts."""
    package = task.package
    if package is None:
        raise ValueError("主题任务缺少主题包索引")

    evidence_dir = run_root / package.evidence
    records = _load_evidence_records(evidence_dir)
    evidence = [item for path, record in records for item in _extract_evidence(path, record, topic=task.topic)]
    if not evidence:
        raise ValueError("没有可用于主题融合的来源证据")

    groups: list[list[FusionEvidence]] = []
    conflicts: list[dict[str, object]] = []
    for item in evidence:
        conflict_group = _find_matching_group(groups, item, opposite_polarity=True)
        if conflict_group is not None:
            conflicts.append(_build_conflict(len(conflicts) + 1, conflict_group[0], item))
            continue
        group = _find_matching_group(groups, item, opposite_polarity=False)
        if group is None:
            groups.append([item])
        elif all(existing.candidate_id != item.candidate_id for existing in group):
            group.append(item)
        elif all(_claim_key(existing.claim) != _claim_key(item.claim) for existing in group):
            groups.append([item])

    conclusions = [_build_conclusion(index, group) for index, group in enumerate(groups, start=1)]
    for conclusion in conclusions:
        related_conflicts = [
            str(conflict["conflict_id"])
            for conflict in conflicts
            if any(_claim_similarity(str(conclusion["claim"]), str(item["claim"])) >= 0.76 for item in conflict["claims"])
        ]
        if related_conflicts:
            conclusion["status"] = "conflicted"
            conclusion["low_confidence"] = True
            conclusion["confidence"] = min(float(conclusion["confidence"]), 0.6)
            conclusion["conflict_ids"] = related_conflicts
    conclusions.sort(key=lambda item: (-int(item["supporting_source_count"]), -float(item["confidence"])))
    for index, conclusion in enumerate(conclusions, start=1):
        conclusion["conclusion_id"] = f"C{index}"

    source_summary = _source_summary(evidence)
    gaps = _evidence_gaps(task, source_summary, conclusions, conflicts)
    payload: dict[str, object] = {
        "schema_version": "0.8",
        "topic": task.topic,
        "generated_at": datetime.now(UTC).isoformat(),
        "fusion_method": "deterministic_conservative_v1",
        "source_summary": source_summary,
        "conclusions": conclusions,
        "conflicts": conflicts,
        "evidence_gaps": gaps,
        "risk_flags": _unique(
            (["unresolved_source_conflicts"] if conflicts else [])
            + (["single_source_conclusions"] if any(item["low_confidence"] for item in conclusions) else [])
            + (["evidence_gaps_present"] if gaps else [])
        ),
    }
    return payload


def write_fusion_artifacts(task: TopicTask, run_root: Path, fusion: dict[str, object]) -> tuple[Path, Path]:
    package = task.package
    if package is None:
        raise ValueError("主题任务缺少主题包索引")
    if not package.fusion:
        package.fusion = f"{package.root}/fusion.json"
    if not package.knowledge:
        package.knowledge = f"{package.root}/knowledge.md"

    fusion_path = run_root / package.fusion
    knowledge_path = run_root / package.knowledge
    write_json(fusion_path, fusion)
    knowledge_path.write_text(_render_knowledge(task, fusion), encoding="utf-8")
    return fusion_path, knowledge_path


def _load_evidence_records(evidence_dir: Path) -> list[tuple[Path, dict[str, object]]]:
    records: list[tuple[Path, dict[str, object]]] = []
    if not evidence_dir.exists():
        return records
    for path in sorted(evidence_dir.glob("*.json")):
        try:
            value = read_json(path)
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(value, dict) and value.get("source_type") in {"web", "video", "github"}:
            records.append((path, value))
    return records


def _extract_evidence(path: Path, record: dict[str, object], *, topic: str = "") -> list[FusionEvidence]:
    source_type = str(record.get("source_type", ""))
    candidate_id = str(record.get("candidate_id", ""))
    evidence_path = path.as_posix()
    if "topic_package/" in evidence_path:
        evidence_path = "topic_package/" + evidence_path.split("topic_package/", 1)[1]
    if source_type == "web":
        return _extract_web(record, candidate_id, evidence_path, topic=topic)
    if source_type == "video":
        return _extract_video(record, candidate_id, evidence_path)
    return _extract_github(record, candidate_id, evidence_path)


def _extract_web(record: dict[str, object], candidate_id: str, path: str, *, topic: str = "") -> list[FusionEvidence]:
    quality = _bounded_int(record.get("quality_score"), 50)
    common = {
        "source_id": str(record.get("source_id") or f"web:{candidate_id}"),
        "candidate_id": candidate_id,
        "source_type": "web",
        "title": str(record.get("title", "")),
        "url": str(record.get("url", "")),
        "evidence_path": path,
        "quality_score": quality,
        "risk_flags": _string_list(record.get("risk_flags")),
    }
    sentences = _claim_sentences(str(record.get("text", "")), topic=topic)
    return [FusionEvidence(**common, locator=f"sentence:{index}", claim=claim, confidence=quality / 100) for index, claim in enumerate(sentences, 1)]


def _extract_video(record: dict[str, object], candidate_id: str, path: str) -> list[FusionEvidence]:
    manifest = record.get("manifest") if isinstance(record.get("manifest"), dict) else {}
    timeline = record.get("timeline") if isinstance(record.get("timeline"), dict) else {}
    items = timeline.get("items", []) if isinstance(timeline, dict) else []
    title = str(manifest.get("title", ""))
    url = str(manifest.get("url", ""))
    risks = _string_list(manifest.get("risk_flags"))
    common = {
        "source_id": f"video:{candidate_id}",
        "candidate_id": candidate_id,
        "source_type": "video",
        "title": title,
        "url": url,
        "evidence_path": path,
        "risk_flags": risks,
    }
    raw_items = items if isinstance(items, list) else []
    result = [
        FusionEvidence(
            **common,
            locator=locator,
            claim=claim,
            confidence=confidence,
            quality_score=round(confidence * 100),
        )
        for locator, claim, confidence in _video_asr_passages(raw_items)
    ]
    for index, raw in enumerate(raw_items, 1):
        evidence_type = str(raw.get("type", "")) if isinstance(raw, dict) else ""
        if (
            not isinstance(raw, dict)
            or evidence_type == "asr"
            or evidence_type.startswith("metadata_")
            or not str(raw.get("claim", "")).strip()
        ):
            continue
        confidence = _bounded_float(raw.get("confidence"), 0.5)
        result.append(
            FusionEvidence(
                **common,
                locator=str(raw.get("timestamp") or f"item:{index}"), claim=_clean_claim(str(raw["claim"])),
                confidence=confidence, quality_score=round(confidence * 100),
            )
        )
    return result


def _video_asr_passages(
    items: list[object],
    *,
    max_passages: int = 12,
    max_chars: int = 360,
) -> list[tuple[str, str, float]]:
    passages: list[tuple[str, str, float]] = []
    claims: list[str] = []
    confidences: list[float] = []
    locator = ""

    def flush() -> None:
        nonlocal claims, confidences, locator
        if claims:
            passages.append((locator, _join_asr_claims(claims), round(sum(confidences) / len(confidences), 6)))
        claims = []
        confidences = []
        locator = ""

    for raw in items:
        if not isinstance(raw, dict) or str(raw.get("type", "")) != "asr":
            continue
        claim = _clean_claim(str(raw.get("claim", "")))
        if not claim:
            continue
        if claims and len(_join_asr_claims([*claims, claim])) > max_chars:
            flush()
        if not locator:
            locator = str(raw.get("timestamp") or "00:00:00")
        claims.append(claim)
        confidences.append(_bounded_float(raw.get("confidence"), 0.5))
    flush()

    passages = [
        (locator, claim, confidence)
        for locator, raw_claim, confidence in passages
        for claim in [_trim_video_boilerplate(raw_claim)]
        if claim and not _is_video_boilerplate(claim)
    ]

    if len(passages) <= max_passages:
        return passages
    indexes = [round(index * (len(passages) - 1) / (max_passages - 1)) for index in range(max_passages)]
    return [passages[index] for index in indexes]


def _join_asr_claims(claims: list[str]) -> str:
    if not claims:
        return ""
    result = claims[0]
    for claim in claims[1:]:
        separator = "，" if re.search(r"[\u4e00-\u9fff]$", result) and re.match(r"^[\u4e00-\u9fff]", claim) else " "
        result += separator + claim
    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", result))
    letter_count = len(re.findall(r"[A-Za-z\u4e00-\u9fff]", result))
    if letter_count and chinese_count / letter_count >= 0.5 and not re.search(r"[。！？!?]$", result):
        result += "。"
    return _clean_claim(result)


def _is_video_boilerplate(claim: str) -> bool:
    lowered = claim.lower()
    return any(marker in lowered for marker in _VIDEO_BOILERPLATE) and not any(
        term in lowered for term in _VIDEO_TECHNICAL_TERMS
    )


def _trim_video_boilerplate(claim: str) -> str:
    lowered = claim.lower()
    cut_positions = [lowered.find(marker) for marker in _VIDEO_BOILERPLATE if lowered.find(marker) > 0]
    if not cut_positions:
        return claim
    trimmed = claim[: min(cut_positions)].rstrip("，,；; \t")
    if re.search(r"[\u4e00-\u9fff]", trimmed) and not re.search(r"[。！？!?]$", trimmed):
        trimmed += "。"
    return trimmed


def _extract_github(record: dict[str, object], candidate_id: str, path: str) -> list[FusionEvidence]:
    quality = _bounded_int(record.get("quality_score"), 50)
    findings = record.get("findings") if isinstance(record.get("findings"), dict) else {}
    result: list[FusionEvidence] = []
    for category, raw_items in findings.items():
        for index, raw in enumerate(raw_items if isinstance(raw_items, list) else [], 1):
            if not isinstance(raw, dict) or not str(raw.get("excerpt", "")).strip():
                continue
            result.append(
                FusionEvidence(
                    source_id=str(record.get("source_id") or f"github:{candidate_id}"), candidate_id=candidate_id,
                    source_type="github", title=str(record.get("repo", "")), url=str(record.get("url", "")),
                    evidence_path=path, locator=f"{category}:{raw.get('path', index)}",
                    claim=_clean_claim(str(raw["excerpt"])), confidence=_bounded_float(record.get("confidence"), quality / 100),
                    quality_score=quality, risk_flags=_string_list(record.get("risk_flags")),
                )
            )
    if not result:
        files = record.get("files", []) if isinstance(record.get("files"), list) else []
        for index, raw in enumerate(files, 1):
            if not isinstance(raw, dict) or not str(raw.get("excerpt", "")).strip():
                continue
            result.append(
                FusionEvidence(
                    source_id=str(record.get("source_id") or f"github:{candidate_id}"), candidate_id=candidate_id,
                    source_type="github", title=str(record.get("repo", "")), url=str(record.get("url", "")),
                    evidence_path=path, locator=f"file:{raw.get('path', index)}",
                    claim=_clean_claim(str(raw["excerpt"])), confidence=_bounded_float(record.get("confidence"), quality / 100),
                    quality_score=quality, risk_flags=_string_list(record.get("risk_flags")),
                )
            )
    return result


def _claim_sentences(text: str, limit: int = 12, *, topic: str = "") -> list[str]:
    candidates: list[tuple[int, str]] = []
    for index, part in enumerate(_SENTENCE_SPLIT.split(text)):
        claim = _clean_claim(part)
        if 18 <= len(claim) <= 360 and all(existing != claim for _, existing in candidates):
            candidates.append((index, claim))
        if len(candidates) == 500:
            break
    ranked = sorted(candidates, key=lambda item: (-_sentence_priority(item[1], topic), item[0]))[:limit]
    claims = [claim for _, claim in sorted(ranked, key=lambda item: item[0])]
    if not claims and text.strip():
        claims.append(_clean_claim(text[:360]))
    return claims


def _sentence_priority(claim: str, topic: str) -> int:
    lower = claim.lower()
    ascii_terms = {
        term.lower()
        for term in re.findall(r"[A-Za-z][A-Za-z0-9_.+-]{2,}", topic)
        if term.lower() not in {"godot", "technical", "plugin"}
    }
    chinese_runs = re.findall(r"[\u4e00-\u9fff]{2,}", topic)
    chinese_terms = {
        run[index : index + 2]
        for run in chinese_runs
        for index in range(len(run) - 1)
        if run[index : index + 2] not in {"模式", "配置", "数据", "插件", "技术"}
    }
    action_markers = (
        " install ", " use ", " run ", " set ", " call ", " create ", " configure ",
        "安装", "使用", "运行", "设置", "调用", "创建", "配置", "启用", "加载", "添加", "赋值",
        "建立", "实现", "定义", "注册",
    )
    score = 0
    if any(term in lower for term in ascii_terms):
        score += 5
    if any(term in claim for term in chinese_terms):
        score += 3
    if any(marker in f" {lower} " for marker in action_markers):
        score += 3
    if "`" in claim or re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\b", claim):
        score += 2
    return score


def _find_matching_group(groups: list[list[FusionEvidence]], item: FusionEvidence, *, opposite_polarity: bool) -> list[FusionEvidence] | None:
    for group in groups:
        representative = group[0]
        if _is_negative(representative.claim) == _is_negative(item.claim):
            if opposite_polarity:
                continue
        elif not opposite_polarity:
            continue
        if _claim_similarity(representative.claim, item.claim) >= (0.68 if opposite_polarity else 0.76):
            return group
    return None


def _claim_similarity(left: str, right: str) -> float:
    left_key = _claim_key(left)
    right_key = _claim_key(right)
    shorter, longer = sorted((left_key, right_key), key=len)
    if len(shorter) >= 24 and shorter in longer:
        return 1.0
    return SequenceMatcher(None, left_key, right_key).ratio()


def _claim_key(value: str) -> str:
    value = value.lower().replace("必须", "must")
    for marker in _NEGATIONS:
        value = value.replace(marker, "")
    value = re.sub(r"不(?!须)", "", value)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def _is_negative(value: str) -> bool:
    lowered = value.lower().replace("必须", "must")
    return any(marker in lowered for marker in _NEGATIONS) or re.search(r"不(?!须)", lowered) is not None


def _build_conclusion(index: int, group: list[FusionEvidence]) -> dict[str, object]:
    ordered = sorted(group, key=lambda item: (item.confidence, item.quality_score), reverse=True)
    references = [item.reference() for item in ordered[:3]]
    source_count = len({item.candidate_id for item in group})
    source_types = sorted({item.source_type for item in group})
    base = sum(item.confidence * max(item.quality_score, 1) for item in group) / sum(max(item.quality_score, 1) for item in group)
    confidence = min(0.99, base + (0.08 if source_count >= 2 else 0))
    low_confidence = source_count < 2 or confidence < 0.65
    return {
        "conclusion_id": f"C{index}", "claim": ordered[0].claim,
        "supporting_source_count": source_count, "supporting_source_types": source_types,
        "confidence": round(confidence, 2), "low_confidence": low_confidence,
        "status": "needs_review" if low_confidence else "supported", "citations": references,
    }


def _build_conflict(index: int, left: FusionEvidence, right: FusionEvidence) -> dict[str, object]:
    return {
        "conflict_id": f"X{index}", "status": "unresolved",
        "summary": "来源对相近结论给出了相反表述，需人工复核原始上下文。",
        "claims": [left.reference(), right.reference()],
    }


def _source_summary(evidence: list[FusionEvidence]) -> list[dict[str, object]]:
    grouped: dict[str, list[FusionEvidence]] = {}
    for item in evidence:
        grouped.setdefault(item.candidate_id, []).append(item)
    result = []
    for candidate_id, items in sorted(grouped.items()):
        first = items[0]
        quality = round(sum(item.quality_score for item in items) / len(items))
        risks = _unique(flag for item in items for flag in item.risk_flags)
        result.append({
            "source_id": first.source_id, "candidate_id": candidate_id, "source_type": first.source_type,
            "title": first.title, "url": first.url, "quality_score": quality,
            "trust_level": "high" if quality >= 75 and not risks else "medium" if quality >= 50 else "low",
            "evidence_item_count": len(items), "risk_flags": risks,
        })
    return result


def _evidence_gaps(task: TopicTask, sources: list[dict[str, object]], conclusions: list[dict[str, object]], conflicts: list[dict[str, object]]) -> list[dict[str, str]]:
    gaps: list[dict[str, str]] = []
    successful_ids = {str(item["candidate_id"]) for item in sources}
    for candidate in task.selected_sources:
        if candidate.candidate_id not in successful_ids:
            gaps.append({"code": "selected_source_without_evidence", "candidate_id": candidate.candidate_id, "detail": f"已选 {candidate.source_type} 来源未生成可融合证据。"})
    if len({str(item["source_type"]) for item in sources}) < 2:
        gaps.append({"code": "single_source_type", "candidate_id": "", "detail": "当前结论只覆盖一种来源类型，缺少跨类型佐证。"})
    if any(bool(item["low_confidence"]) for item in conclusions):
        gaps.append({"code": "low_confidence_conclusions", "candidate_id": "", "detail": "存在仅由单一来源或弱证据支持的结论。"})
    if conflicts:
        gaps.append({"code": "unresolved_conflicts", "candidate_id": "", "detail": "存在尚未解决的来源冲突。"})
    return gaps


def _render_knowledge(task: TopicTask, fusion: dict[str, object]) -> str:
    conclusions = fusion.get("conclusions", [])
    conflicts = fusion.get("conflicts", [])
    gaps = fusion.get("evidence_gaps", [])
    sources = fusion.get("source_summary", [])
    lines = [f"# {task.topic}", "", "## 融合摘要", "", f"本主题基于 {len(sources)} 个已确认来源生成；结论采用保守融合，引用均指向本地证据记录。", "", "## 关键结论", ""]
    for item in conclusions:
        citations = " ".join(f"[{ref['source_id']}:{ref['locator']}]" for ref in item["citations"])
        marker = "（低置信，待复核）" if item["low_confidence"] else ""
        lines.append(f"- {item['claim']} {citations}{marker}")
    lines.extend(["", "## 冲突记录", ""])
    if conflicts:
        for conflict in conflicts:
            refs = " / ".join(f"{item['claim']} [{item['source_id']}:{item['locator']}]" for item in conflict["claims"])
            lines.append(f"- {conflict['summary']} {refs}")
    else:
        lines.append("- 未检测到规则可识别的直接冲突；这不等同于所有来源完全一致。")
    lines.extend(["", "## 证据缺口", ""])
    lines.extend(f"- {item['detail']}" for item in gaps) if gaps else lines.append("- 未发现额外的结构化证据缺口。")
    lines.extend(["", "## 来源可信度", ""])
    for source in sources:
        risks = f"；风险：{', '.join(source['risk_flags'])}" if source["risk_flags"] else ""
        lines.append(f"- [{source['source_id']}] {source['title'] or source['url']}：{source['trust_level']}，质量 {source['quality_score']}/100{risks}")
    lines.extend(["", "## 引用说明", "", "引用格式为 `[来源ID:定位信息]`；完整文本、时间戳或文件片段保存在 `topic_package/evidence/`。"])
    return "\n".join(lines).rstrip() + "\n"


def _clean_claim(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\r\n-*#")


def _bounded_int(value: Any, default: int) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return default


def _bounded_float(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _unique(values: Any) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))
