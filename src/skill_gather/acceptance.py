from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .config import load_config
from .search import search_topic
from .topics import TopicRunStore


REQUIRED_COVERAGE = {
    "normal_tutorial",
    "technical_workflow",
    "chinese_material",
    "english_material",
    "video_source",
    "web_source",
    "github_source",
    "low_quality_source",
    "source_conflict",
    "insufficient_evidence",
}


@dataclass(frozen=True, slots=True)
class AcceptanceCase:
    case_id: str
    topic: str
    mode: str
    coverage: tuple[str, ...]
    expected_min_status: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AcceptanceCase":
        mode = str(value.get("mode", ""))
        if mode not in {"normal", "technical"}:
            raise ValueError("验收主题 mode 必须是 normal 或 technical")
        coverage = tuple(str(item) for item in value.get("coverage", []))
        if not coverage:
            raise ValueError("每个验收主题必须声明 coverage")
        return cls(
            case_id=str(value["case_id"]),
            topic=str(value["topic"]),
            mode=mode,
            coverage=coverage,
            expected_min_status=str(value.get("expected_min_status", "needs_review")),
        )


def run_offline_acceptance(
    dataset_path: str | Path,
    *,
    config_path: str | Path,
    runs_path: str | Path,
) -> dict[str, Any]:
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    raw_cases = dataset.get("cases", []) if isinstance(dataset, dict) else []
    cases = [AcceptanceCase.from_dict(item) for item in raw_cases]
    if len(cases) != 10:
        raise ValueError(f"v1.0 固定验收集必须正好包含 10 个主题，当前为 {len(cases)} 个")
    ids = [case.case_id for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("v1.0 固定验收集的 case_id 必须唯一")
    covered = {label for case in cases for label in case.coverage}
    missing = sorted(REQUIRED_COVERAGE - covered)
    if missing:
        raise ValueError("v1.0 固定验收集缺少覆盖项：" + ", ".join(missing))

    config = load_config(config_path)
    # Offline acceptance must never inherit optional remote LLM enhancements.
    config = replace(
        config,
        search=replace(
            config.search,
            use_newapi_query_expansion=False,
            use_newapi_candidate_assessment=False,
        ),
    )
    store = TopicRunStore(runs_path)
    results: list[dict[str, Any]] = []
    for case in cases:
        task = store.start_or_resume(
            topic=case.topic,
            mode=case.mode,
            budget=config.topic_defaults.budget,
            cache=config.topic_defaults.cache,
            judge_difficulty=config.topic_defaults.judge_difficulty,
        )
        task = store.begin_search(task.run_id)
        candidates, batches, query_audit, intent = search_topic(
            task,
            config,
            cache_path=Path(runs_path) / "search_cache.sqlite3",
            use_fake=True,
        )
        task = store.save_search_results(
            task.run_id,
            candidates,
            search_audit={
                "topic": task.topic,
                "mode": task.mode,
                "intent": intent.to_dict(),
                "queries": query_audit,
                "batches": [batch.to_dict() for batch in batches],
                "acceptance_case_id": case.case_id,
            },
            warnings=[
                f"{batch.provider}: {warning}"
                for batch in batches
                for warning in batch.warnings
            ],
        )
        results.append(
            {
                "case_id": case.case_id,
                "run_id": task.run_id,
                "mode": case.mode,
                "status": task.status,
                "candidate_count": len(task.candidates),
                "expected_min_status": case.expected_min_status,
                "coverage": list(case.coverage),
                "passed": task.status == "awaiting_selection" and bool(task.candidates),
            }
        )

    passed = sum(bool(item["passed"]) for item in results)
    return {
        "schema_version": "1.0",
        "validation": "offline_fake_search",
        "synthetic": True,
        "case_count": len(results),
        "passed_count": passed,
        "failed_count": len(results) - passed,
        "coverage": sorted(covered),
        "results": results,
        "status": "passed" if passed == len(results) else "failed",
        "limitations": [
            "本报告只验证固定验收集契约、主题 run、离线搜索和候选审计。",
            "它不能替代真实网页、Bilibili、GitHub、ASR、视觉模型和 NewAPI 链路验收。",
        ],
    }
