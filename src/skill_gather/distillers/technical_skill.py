from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
import re
from typing import Any

from ..models import TopicTask
from ..runs import read_json, write_json


def generate_technical_skill(
    task: TopicTask, run_root: Path, fusion: dict[str, Any]
) -> tuple[Path, Path, dict[str, Any]]:
    """Generate a conservative technical skill package from saved evidence only."""
    package = task.package
    if package is None:
        raise ValueError("主题任务缺少主题包索引")
    skill_rel = package.skill or f"{package.root}/SKILL.md"
    score_rel = package.score or f"{package.root}/score.json"
    skill_path = run_root / skill_rel
    score_path = run_root / score_rel
    skill_path.parent.mkdir(parents=True, exist_ok=True)

    conclusions = _relevant_conclusions(
        task.topic,
        [item for item in fusion.get("conclusions", []) if isinstance(item, dict)],
    )
    commands, configuration, materials = _github_materials(run_root / package.evidence)
    procedures = _rank_procedures(_procedural_conclusions(conclusions))[:8]
    if not conclusions:
        commands = []
        configuration = []
    references_dir = run_root / package.references
    reference_index = _write_reference_index(task, run_root, references_dir, fusion)
    description = f"Evidence-first workflow for {task.topic}. Use when implementing, configuring, debugging, or reviewing this technical topic."
    lines = ["---", f"name: {_skill_name(task.topic)}", f"description: {_yaml_string(description)}", "---", "", f"# {task.topic}", "", "## Workflow", ""]
    if procedures:
        for index, item in enumerate(procedures, start=1):
            citations = " ".join(
                f"[{ref.get('source_id', '')}:{ref.get('locator', '')}]"
                for ref in item.get("citations", [])[:3]
                if isinstance(ref, dict)
            )
            marker = "（待复核）" if item.get("low_confidence") or item.get("status") == "conflicted" else ""
            claim = str(item.get("claim", "")).strip()
            lines.extend([f"{index}. {claim} {marker} {citations}".rstrip(), f"   完成标准：{_completion_criterion(claim)}", ""])
    else:
        lines.extend(["1. 读取 `references/index.md`，确认当前证据是否包含目标版本和可执行 API。", "   完成标准：明确记录缺失的命令、参数或运行条件，并停止把当前产物标记为可直接使用。", ""])
    if commands:
        lines.extend(["## Commands", ""])
        lines.extend(f"- `{command}`" for command in commands)
        lines.append("")
    if configuration:
        lines.extend(["## Configuration", ""])
        lines.extend(f"- `{item}`" for item in configuration)
        lines.append("")
    lines.extend(["## Verification", ""])
    lines.extend(_verification_lines(procedures, fusion))
    lines.extend(["", "## Boundaries", ""])
    risks = fusion.get("risk_flags", [])
    conflicts = fusion.get("conflicts", [])
    if risks:
        lines.extend(f"- 风险标记：{flag}" for flag in risks)
    if conflicts:
        lines.append("- 存在未解决的来源冲突，不能将本 skill 视为无条件正确。")
    if not risks and not conflicts:
        lines.append("- 当前融合结果未发现结构化风险标记；仍需人工复核原始来源。")
    lines.extend(["", "## Evidence", "", "执行前读取 `references/index.md`。当步骤标记为“待复核”、涉及版本差异或需要完整代码上下文时，按索引打开对应 reference；引用格式为 `[来源ID:定位信息]`。"])
    skill_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    score = score_technical_package(
        task,
        fusion,
        skill_path=skill_path,
        references_dir=run_root / package.references,
        reference_index=reference_index,
        commands=commands,
        materials=materials,
    )
    write_json(score_path, score)
    return skill_path, score_path, score


def score_technical_package(
    task: TopicTask,
    fusion: dict[str, Any],
    *,
    skill_path: Path,
    references_dir: Path,
    reference_index: Path,
    commands: list[str],
    materials: list[str],
) -> dict[str, Any]:
    conclusions = _relevant_conclusions(
        task.topic,
        [item for item in fusion.get("conclusions", []) if isinstance(item, dict)],
    )
    docs = 50 if (task.package and (skill_path.parent / "knowledge.md").exists()) else 0
    docs += 40 if reference_index.exists() else 0
    docs += 10 if materials else 0
    docs = min(100, docs)
    skill_text = skill_path.read_text(encoding="utf-8") if skill_path.exists() else ""
    procedures = _rank_procedures(_procedural_conclusions(conclusions))[:8]
    skill_checks = (skill_text.startswith("---\nname:"), "description:" in skill_text.split("---", 2)[1] if skill_text.startswith("---") else False, "## Workflow" in skill_text, "完成标准：" in skill_text, "`references/index.md`" in skill_text)
    skill = sum(20 for passed in skill_checks if passed)
    if conclusions:
        citation_coverage = sum(100 if item.get("citations") else 40 for item in conclusions) / len(conclusions)
        corroborated = sum(
            not item.get("low_confidence") and item.get("status") not in {"needs_review", "conflicted"}
            for item in conclusions
        )
        evidence = round(citation_coverage * (0.6 + 0.4 * corroborated / len(conclusions)))
    else:
        evidence = 0
    high_quality_procedures = [
        item
        for item in procedures
        if _procedure_action_quality(str(item.get("claim", ""))) >= 2
    ]
    if commands and len(high_quality_procedures) >= 3:
        executable = 100
    elif commands and high_quality_procedures:
        executable = 85
    elif commands:
        executable = 50
    else:
        executable = 90 if len(high_quality_procedures) >= 3 else 70 if high_quality_procedures else 0
    boundary = 60 if fusion.get("conflicts") or fusion.get("evidence_gaps") else 100
    if not procedures and not commands:
        skill = min(skill, 70)
    dimensions = {"documentation": docs, "skill": skill, "evidence": evidence, "executability": executable, "boundaries": boundary}
    final_score = round(sum(dimensions.values()) / len(dimensions))
    if not conclusions or skill == 0:
        status = "failed"
    elif fusion.get("conflicts") or fusion.get("evidence_gaps") or final_score < 75:
        status = "needs_review"
    else:
        status = "passed"
    return {
        "schema_version": "0.9",
        "generated_at": datetime.now(UTC).isoformat(),
        "final_score": final_score,
        "final_status": status,
        "dimensions": dimensions,
        "evidence_count": len(conclusions),
        "reference_count": len(list(references_dir.glob("*"))) if references_dir.exists() else 0,
        "risk_flags": list(fusion.get("risk_flags", [])),
        "human_review": None,
    }


def rescore_technical_package(task: TopicTask, run_root: Path, fusion: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    """Recompute a technical package score without rewriting the generated skill."""
    package = task.package
    if package is None:
        raise ValueError("主题任务缺少主题包索引")
    skill_path = run_root / (package.skill or f"{package.root}/SKILL.md")
    score_path = run_root / (package.score or f"{package.root}/score.json")
    reference_index = run_root / package.references / "index.md"
    _commands, _configuration, materials = _github_materials(run_root / package.evidence)
    score = score_technical_package(
        task,
        fusion,
        skill_path=skill_path,
        references_dir=run_root / package.references,
        reference_index=reference_index,
        commands=_commands,
        materials=materials,
    )
    write_json(score_path, score)
    return score_path, score


def apply_topic_human_review(score_path: Path, *, label: str, notes: str) -> dict[str, Any]:
    score = read_json(score_path) if score_path.exists() else {}
    previous_status = str(score.get("final_status", "failed"))
    score["human_review"] = {"label": label, "notes": notes, "reviewed_at": datetime.now(UTC).isoformat()}
    if label == "usable":
        score["final_status"] = "needs_review" if previous_status == "failed" else "passed"
    else:
        score["final_status"] = {"needs_changes": "needs_review", "unusable": "failed"}[label]
    write_json(score_path, score)
    return score


def _github_materials(evidence_dir: Path) -> tuple[list[str], list[str], list[str]]:
    commands: list[str] = []
    configuration: list[str] = []
    materials: list[str] = []
    for path in sorted(evidence_dir.glob("github-*.json")) if evidence_dir.exists() else []:
        try:
            record = read_json(path)
        except (OSError, ValueError, TypeError):
            continue
        findings = record.get("findings", {}) if isinstance(record, dict) else {}
        for key, target in (("commands", commands), ("configuration", configuration)):
            values = findings.get(key, []) if isinstance(findings, dict) else []
            for value in values if isinstance(values, list) else []:
                if isinstance(value, dict):
                    explicit = str(value.get("command") or "").strip()
                    text = explicit or _extract_command(str(value.get("excerpt") or ""))
                else:
                    text = _extract_command(str(value)) if key == "commands" else str(value).strip()
                if text and text not in target:
                    target.append(text[:240])
        if isinstance(findings, dict) and findings.get("skill_materials"):
            materials.extend(str(item) for item in findings["skill_materials"] if str(item))
    return commands[:12], configuration[:12], materials[:12]


def _relevant_conclusions(topic: str, conclusions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ascii_terms = {
        term.lower()
        for term in re.findall(r"[A-Za-z][A-Za-z0-9_.+-]{2,}", topic)
        if term.lower() not in {
            "godot", "technical", "plugin", "save", "game", "best", "practices",
            "setup", "implementation", "patterns", "error",
        }
    }
    chinese_runs = re.findall(r"[\u4e00-\u9fff]{2,}", topic)
    chinese_terms = {
        run[index : index + 2]
        for run in chinese_runs
        for index in range(len(run) - 1)
        if run[index : index + 2] not in {"模式", "配置", "数据", "插件", "技术"}
    }
    terms = ascii_terms | chinese_terms
    if not terms:
        return conclusions
    return [
        item
        for item in conclusions
        if _useful_claim(str(item.get("claim", "")))
        and _dimension_matches(topic, str(item.get("claim", "")))
        and _version_matches(topic, str(item.get("claim", "")))
        and any(term in str(item.get("claim", "")).lower() for term in terms)
    ]


def _procedural_conclusions(conclusions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in conclusions:
        if _is_procedural_claim(str(item.get("claim", ""))):
            result.append(item)
    return result


def _rank_procedures(conclusions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        conclusions,
        key=lambda item: (
            -_procedure_priority(str(item.get("claim", ""))),
            bool(item.get("low_confidence")),
            _procedure_style_penalty(str(item.get("claim", ""))),
            _explicit_step_order(str(item.get("claim", ""))),
            len(str(item.get("claim", ""))),
        ),
    )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in ranked:
        claim = str(item.get("claim", ""))
        if _procedure_priority(claim) <= 0:
            continue
        key = _procedure_key(claim)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _is_procedural_claim(claim: str) -> bool:
    lower = _without_step_prefix(claim.strip().lower())
    if not claim.strip() or claim.count("(") != claim.count(")"):
        return False
    if any(marker in lower for marker in ("in this article", "this tutorial", "this section", "本文", "本节介绍", "本章")):
        return False
    if lower.startswith("velocity_computed ("):
        return False
    if re.match(r"^(?:void|bool|int|float|string|variant|array|dictionary)\s+\w+\s*\(", lower):
        return False
    if lower.startswith(("if ", "if(", "you call ", "you just check ")):
        return False
    if re.match(r"^使用.+(?:是|作为).*(?:选项|方式|方法)", claim.strip()):
        return False
    if re.search(r"(?:^|\.)(?:print|printerr|printerror|printwarning|push_warning)\s*\(", lower):
        return False
    if re.search(r"\b(?:var|const|let)\s+[A-Za-z_][A-Za-z0-9_]*\s*(?::=|=)", claim):
        return True
    imperative = (
        "install ", "use ", "run ", "set ", "call ", "create ", "configure ", "enable ",
        "disable ", "load ", "add ", "define ", "export ", "import ", "check ", "put ",
        "register ", "navigate ", "in project settings, navigate ", "in the project settings, navigate ",
        "安装", "使用", "运行", "设置", "调用", "创建", "配置", "启用", "禁用", "加载", "添加", "定义", "导出", "导入", "检查", "将", "把", "建立", "实现", "注册",
    )
    if lower.startswith(imperative) or lower.startswith("to use ") or " to use " in lower:
        return True
    if any(marker in lower for marker in ("essential to call", "you can then write ", "watch out for")):
        return True
    if "必须使用" in claim and _api_calls(claim):
        return True
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*\s*\([^\r\n]*\)\s*;?", claim.strip()))


def _procedure_priority(claim: str) -> int:
    lower = _without_step_prefix(claim.strip().lower())
    if _is_disable_step(lower):
        return 0
    if re.search(r"\.new\s*\(", lower):
        return 7
    if lower.startswith(("create ", "define ", "建立", "实现")):
        return 6
    if lower.startswith(("configure ", "enable ", "add ", "register ", "navigate ", "in project settings, navigate ", "in the project settings, navigate ")) or re.search(r"(?:request|enabled|open)\s*\(", lower):
        return 5
    if re.search(r"(?:connect|callback|status|check)\s*\(", lower):
        return 4
    if re.search(r"\b(?:var|const|let)\s+[A-Za-z_][A-Za-z0-9_]*\s*(?::=|=)", claim):
        return 3
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*\s*\([^\r\n]*\)\s*;?", claim.strip()):
        return 3
    if lower.startswith(("use ", "call ", "connect ", "set ", "put ", "place ", "from any script")):
        return 2
    return 1


def _is_disable_step(lower: str) -> bool:
    if lower.startswith(("disable ", "禁用")):
        return True
    if re.search(r"(?:avoidance_enabled|set_[a-z0-9_]*enabled)\s*\([^)]*\bfalse\b", lower):
        return True
    if re.search(r"set_[a-z0-9_]*callback\s*\([^\r\n]*callable\(\s*\)\s*\)", lower):
        return True
    return bool(re.search(r"[a-z0-9_]*enabled\s*=\s*false\b", lower))


def _procedure_action_quality(claim: str) -> int:
    clean = claim.strip()
    lower = clean.lower()
    if _procedure_priority(clean) <= 0:
        return 0
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*\s*\([^\r\n]*\)\s*;?", clean):
        return 2
    if _api_calls(clean) and (
        re.search(r"\b(?:var|const|let)\s+[A-Za-z_][A-Za-z0-9_]*\s*(?::=|=)", clean)
        or lower.startswith(("call ", "connect ", "set ", "to use "))
    ):
        return 2
    if re.search(r"\bautoloads?\b", lower) and re.search(r"\b(?:register|add|navigate)\b", lower):
        return 2
    if re.search(r"\b[a-z0-9_]*enabled\b.*\btrue\b", lower):
        return 2
    if lower.startswith(("create ", "define ")) and re.search(r"\b(?:script|signal|node|resource|class)\b", lower):
        return 2
    return 1


def _procedure_style_penalty(claim: str) -> int:
    lower = claim.lower()
    if re.search(r"\bautoloads?\b", lower) and re.search(r"\b(?:register|add|navigate)\b", lower):
        return 0 if "navigate" in lower else 1
    calls = _api_calls(claim)
    if "configfile" in lower and re.search(r"(?:create|创建)", lower) and not calls:
        return 1
    if any(re.search(r"\.[A-Za-z0-9]*[A-Z][A-Za-z0-9]*$", call) for call in calls):
        return 1
    return 0


def _without_step_prefix(value: str) -> str:
    return re.sub(r"^第[一二三四五六七八九十]+步\s*[:：]\s*", "", value)


def _explicit_step_order(claim: str) -> int:
    match = re.match(r"^第([一二三四五六七八九十]+)步\s*[:：]", claim.strip())
    if not match:
        return 99
    values = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    return values.get(match.group(1), 99)


def _procedure_key(claim: str) -> str:
    lower = claim.lower()
    if re.search(r"\bautoloads?\b", lower) and re.search(r"\b(?:register|add|navigate)\b", lower):
        return "autoload-register"
    if "configfile" in lower and (
        re.search(r"(?:create|创建)", lower)
        or re.search(r"configfile\.new\s*\(", lower)
    ):
        return "configfile-new"
    if "configfile" in lower and ("aes-256" in lower or "encrypt" in lower or "加密" in claim):
        return "configfile-save-encrypted"
    calls = _api_calls(claim)
    value = calls[0] if calls else claim
    return re.sub(r"[^a-z0-9]", "", value.lower())[:160]


def _dimension_matches(topic: str, claim: str) -> bool:
    topic_lower = topic.lower()
    claim_lower = claim.lower()
    topic_2d = bool(re.search(r"[a-z_]*2d\b", topic_lower))
    topic_3d = bool(re.search(r"[a-z_]*3d\b", topic_lower))
    claim_2d = bool(re.search(r"[a-z_]*2d\b", claim_lower))
    claim_3d = bool(re.search(r"[a-z_]*3d\b", claim_lower))
    if topic_2d and not topic_3d and claim_3d and not claim_2d:
        return False
    if topic_3d and not topic_2d and claim_2d and not claim_3d:
        return False
    return True


def _version_matches(topic: str, claim: str) -> bool:
    if not re.search(r"\bgodot\s*4\b", topic.lower()):
        return True
    lower = claim.lower()
    if re.search(r"\.connect\(\s*[\"'][^\"']+[\"']\s*,\s*self\s*,", lower):
        return False
    if re.search(r"\.emit_signal\(\s*\)", lower):
        return False
    return True


def _skill_name(topic: str) -> str:
    tokens = [token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9]{1,}", topic)]
    slug = "-".join(tokens)[:56].strip("-")
    if not slug:
        slug = f"technical-{sha256(topic.encode('utf-8')).hexdigest()[:8]}"
    return f"{slug}-workflow"[:64].rstrip("-")


def _yaml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _api_calls(claim: str) -> list[str]:
    values = re.findall(r"\b((?:[A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*)\s*\(", claim)
    return list(dict.fromkeys(value for value in values if value.lower() not in {"if", "for", "while", "print"}))


def _completion_criterion(claim: str) -> str:
    calls = _api_calls(claim)
    if calls:
        names = "、".join(f"`{call}()`" for call in calls[:3])
        return f"实现中存在 {names} 调用，运行结果可观察，且行为与引用描述一致。"
    return "该动作已在目标项目中完成，并能通过运行结果或测试复核。"


def _verification_lines(
    procedures: list[dict[str, Any]], fusion: dict[str, Any]
) -> list[str]:
    calls = list(
        dict.fromkeys(
            call
            for item in procedures
            for call in _api_calls(str(item.get("claim", "")))
        )
    )
    lines: list[str] = []
    if calls:
        rendered = ", ".join(f"`{call}()`" for call in calls[:6])
        lines.append(
            f"- Run the target scenario and confirm that {rendered} execute without runtime errors."
        )
    if procedures:
        lines.append(
            "- Check every Workflow completion criterion and record the observed result."
        )
    else:
        lines.append(
            "- Verification is blocked until executable steps are recovered from the references."
        )
    if fusion.get("conflicts") or fusion.get("evidence_gaps"):
        lines.append(
            "- Resolve every conflict and evidence gap before marking this skill as production-ready."
        )
    return lines


def _write_reference_index(
    task: TopicTask,
    run_root: Path,
    references_dir: Path,
    fusion: dict[str, Any],
) -> Path:
    references_dir.mkdir(parents=True, exist_ok=True)
    records: dict[str, dict[str, str]] = {}
    for item in fusion.get("source_summary", []):
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id", "")).strip()
        if source_id:
            records[source_id] = {
                "candidate_id": str(item.get("candidate_id", "")).strip(),
                "title": str(item.get("title", "")).strip(),
                "url": str(item.get("url", "")).strip(),
            }

    for conclusion in fusion.get("conclusions", []):
        if not isinstance(conclusion, dict):
            continue
        for citation in conclusion.get("citations", []):
            if not isinstance(citation, dict):
                continue
            source_id = str(citation.get("source_id", "")).strip()
            if not source_id:
                continue
            record = records.setdefault(source_id, {})
            for key in ("candidate_id", "title", "url"):
                value = str(citation.get(key, "")).strip()
                if value and not record.get(key):
                    record[key] = value
            if not record.get("candidate_id"):
                evidence_path = str(citation.get("evidence_path", "")).strip()
                if evidence_path:
                    record["candidate_id"] = Path(evidence_path).stem

    lines = [f"# Reference Index: {task.topic}", ""]
    if not records:
        lines.append("No source mapping is available. Do not treat this package as verified.")
    for source_id, record in sorted(records.items()):
        candidate_id = record.get("candidate_id", "")
        reference_path = references_dir / f"{candidate_id}.txt"
        title = record.get("title") or candidate_id or "Untitled source"
        if candidate_id and reference_path.exists():
            lines.append(f"- **{source_id}**: [{title}]({reference_path.name})")
        else:
            lines.append(f"- **{source_id}**: {title} (local reference unavailable)")
        if record.get("url"):
            lines.append(f"  - Source: {record['url']}")

    index_path = references_dir / "index.md"
    index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return index_path


def _useful_claim(claim: str) -> bool:
    clean = claim.strip()
    if clean.startswith(("![", "[")):
        return False
    if _api_calls(clean) and _is_procedural_claim(clean):
        return True
    if _explicit_step_order(clean) < 99 and _is_procedural_claim(clean):
        return True
    return len(clean) >= 30 or len(re.findall(r"[\u4e00-\u9fff]", clean)) >= 8


def _extract_command(value: str) -> str:
    text = value.strip()
    fenced = re.search(r"`([^`\r\n]+)`", text)
    if fenced:
        candidate = fenced.group(1).strip()
        if _looks_like_command(candidate):
            return candidate
    for line in text.splitlines():
        candidate = line.strip().lstrip("$> ")
        if _looks_like_command(candidate):
            return candidate
    return ""


def _looks_like_command(value: str) -> bool:
    first = value.split(maxsplit=1)[0].lower() if value else ""
    return first in {
        "cargo", "cmake", "docker", "dotnet", "git", "godot", "make", "npm", "npx",
        "pip", "pip3", "poetry", "python", "python3", "uv", "yarn",
    }
