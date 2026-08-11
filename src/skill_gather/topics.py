from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .models import (
    TOPIC_RUN_STATUSES,
    TopicBudget,
    TopicCachePolicy,
    TopicPackage,
    TopicSourceCandidate,
    TopicTask,
    TopicUsage,
)
from .planning import apply_plan, build_deterministic_plan
from .runs import read_json, safe_slug, write_json


_ALLOWED_TRANSITIONS = {
    "created": {"planning", "searching", "failed"},
    "planning": {"awaiting_plan_confirmation", "searching", "failed"},
    "awaiting_plan_confirmation": {"searching", "failed"},
    "searching": {"awaiting_selection", "failed"},
    "awaiting_selection": {"processing_sources", "failed"},
    "processing_sources": {"generating", "failed"},
    "generating": {"scoring", "failed"},
    "scoring": {"completed", "failed"},
    "completed": set(),
    "paused": {"planning", "searching", "processing_sources", "generating", "scoring", "failed"},
    "failed": set(),
}


class TopicRunStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def run_id_for(self, topic: str, mode: str, output_language: str) -> str:
        identity = "\x00".join((topic.strip(), mode, output_language))
        digest = sha256(identity.encode("utf-8")).hexdigest()[:8]
        return f"topic-{safe_slug(topic)}-{safe_slug(mode)}-{digest}"

    def run_path(self, run_id: str) -> Path:
        return self.root / safe_slug(run_id)

    def state_path(self, run_id: str) -> Path:
        return self.run_path(run_id) / "topic_state.json"

    def package_path(self, run_id: str) -> Path:
        return self.run_path(run_id) / "topic_package"

    def start_or_resume(
        self,
        topic: str,
        mode: str = "normal",
        output_language: str = "zh-CN",
        budget: TopicBudget | None = None,
        cache: TopicCachePolicy | None = None,
        judge_difficulty: str = "standard",
        execution_mode: str = "manual",
    ) -> TopicTask:
        run_id = self.run_id_for(topic, mode, output_language)
        if self.state_path(run_id).exists():
            return self.load(run_id)

        package_root = "topic_package"
        task = TopicTask(
            run_id=run_id,
            topic=topic,
            mode=mode,
            output_language=output_language,
            budget=budget or TopicBudget(),
            cache=cache or TopicCachePolicy(),
            judge_difficulty=judge_difficulty,
            execution_mode=execution_mode,
            package=TopicPackage(
                root=package_root,
                sources=f"{package_root}/sources.json",
                evidence=f"{package_root}/evidence",
                references=f"{package_root}/references",
                knowledge=None,
                fusion=f"{package_root}/fusion.json",
                skill=f"{package_root}/SKILL.md" if mode == "technical" else None,
                score=f"{package_root}/score.json" if mode == "technical" else None,
            ),
            artifacts={
                "topic_package": package_root,
                "sources": f"{package_root}/sources.json",
                "evidence": f"{package_root}/evidence",
            },
        )
        self._create_package_layout(task)
        self.save(task)
        return task

    def create_plan(self, run_id: str, *, plan: Any | None = None, warning: str = "") -> TopicTask:
        task = self.load(run_id)
        task.plan = plan or build_deterministic_plan(task, warning=warning)
        task.plan_audit.append({"event": "plan_generated", "method": task.plan.generation_method, "ambiguous": task.plan.ambiguous})
        if task.plan.ambiguous:
            task.status = "awaiting_plan_confirmation"
            task.current_stage = "awaiting_plan_confirmation"
        else:
            task.status = "created"
            task.current_stage = "created"
        self.save(task)
        return task

    def confirm_plan(self, run_id: str, option_id: str, *, edited: dict[str, object] | None = None) -> TopicTask:
        task = self.load(run_id)
        if task.plan is None or task.status != "awaiting_plan_confirmation":
            raise ValueError("当前主题没有等待确认的语义计划")
        task.plan = apply_plan(task, task.plan, option_id, edited=edited)
        task.plan_audit.append({"event": "plan_confirmed", "option_id": option_id, "edited": bool(edited)})
        task.status = "created"
        task.current_stage = "created"
        self.save(task)
        return task

    def interrupt_plan(self, run_id: str, reason: str = "用户中断计划生成") -> TopicTask:
        task = self.load(run_id)
        if task.plan is None:
            task.plan = build_deterministic_plan(task, warning="plan_interrupted")
        task.plan.warning = "plan_interrupted"
        task.plan_audit.append({"event": "plan_interrupted", "reason": reason})
        if task.execution_mode == "auto":
            task.plan = apply_plan(task, task.plan, task.plan.recommended_option_id)
            task.status = "created"
            task.current_stage = "created"
        else:
            task.plan.audit_status = "needs_confirmation"
            task.status = "awaiting_plan_confirmation"
            task.current_stage = "awaiting_plan_confirmation"
        self.save(task)
        return task

    def pause(self, run_id: str) -> TopicTask:
        task = self.load(run_id)
        if task.status in {"completed", "failed", "paused"}:
            raise ValueError("当前主题任务不能暂停")
        task.paused_from = task.status
        task.status = "paused"
        self.save(task)
        return task

    def resume_paused(self, run_id: str) -> TopicTask:
        task = self.load(run_id)
        if task.status != "paused":
            raise ValueError("当前主题任务不是暂停状态")
        task.status = task.paused_from or "created"
        task.current_stage = task.status
        task.paused_from = None
        self.save(task)
        return task

    def save(self, task: TopicTask) -> None:
        now = datetime.now(UTC).isoformat()
        if not task.created_at:
            task.created_at = now
        task.updated_at = now
        path = self.state_path(task.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, task.to_dict())

    def load(self, run_id: str) -> TopicTask:
        path = self.state_path(run_id)
        if not path.exists():
            raise FileNotFoundError(f"找不到主题 run：{run_id}")
        task = TopicTask.from_dict(read_json(path))
        self._normalize_optional_artifacts(task)
        return task

    def _normalize_optional_artifacts(self, task: TopicTask) -> None:
        """Keep optional package entries aligned with files on disk, including old runs."""
        package = task.package
        if package is None or not package.knowledge:
            task.artifacts.pop("knowledge", None)
        else:
            knowledge_path = self.run_path(task.run_id) / package.knowledge
            if knowledge_path.exists():
                task.artifacts.setdefault("knowledge", package.knowledge)
            else:
                package.knowledge = None
                task.artifacts.pop("knowledge", None)

        if package is not None:
            if not package.fusion:
                package.fusion = f"{package.root}/fusion.json"
            fusion_path = self.run_path(task.run_id) / package.fusion
            if fusion_path.exists():
                task.artifacts.setdefault("fusion", package.fusion)
            else:
                task.artifacts.pop("fusion", None)

        for video_run in task.video_runs:
            if not video_run.child_run_id or video_run.vision_status:
                continue
            vision_path = self.run_path(task.run_id) / "video_runs" / video_run.child_run_id / "vision_ocr.json"
            if not vision_path.exists():
                continue
            try:
                vision = read_json(vision_path)
            except (OSError, ValueError, TypeError):
                continue
            video_run.vision_status = str(vision.get("status", ""))
            video_run.vision_reason = str(vision.get("reason", ""))

    def advance(self, run_id: str, status: str) -> TopicTask:
        if status not in TOPIC_RUN_STATUSES:
            raise ValueError("无效的主题任务状态")
        task = self.load(run_id)
        if status not in _ALLOWED_TRANSITIONS[task.status]:
            raise ValueError(f"不能从 {task.status} 推进到 {status}")
        task.status = status
        task.current_stage = status
        self.save(task)
        return task

    def fail(self, run_id: str, reason: str) -> TopicTask:
        task = self.load(run_id)
        if task.status in {"completed", "failed"}:
            raise ValueError(f"不能将 {task.status} 状态标记为失败")
        task.status = "failed"
        task.failure_stage = task.current_stage
        task.failure_reason = reason.strip() or "未说明的失败原因"
        self.save(task)
        return task

    def resume(self, run_id: str) -> TopicTask:
        task = self.load(run_id)
        if task.status != "failed":
            raise ValueError("只有失败的主题 run 可以恢复")
        task.status = task.failure_stage or "created"
        task.current_stage = task.status
        self.save(task)
        return task

    def rerun(self, run_id: str, stage: str) -> TopicTask:
        if stage not in {"processing_sources", "generating", "scoring"}:
            raise ValueError("重跑阶段必须是 processing_sources、generating 或 scoring")
        task = self.load(run_id)
        previous_status = task.status
        task.status = stage
        task.current_stage = stage
        task.failure_reason = None
        task.failure_stage = None
        audit_path = self.run_path(run_id) / "rerun_audit.json"
        audit = read_json(audit_path) if audit_path.exists() else {"run_id": run_id, "entries": []}
        entries = audit.get("entries", []) if isinstance(audit, dict) else []
        entries.append(
            {
                "requested_at": datetime.now(UTC).isoformat(),
                "from_status": previous_status,
                "stage": stage,
            }
        )
        write_json(audit_path, {"run_id": run_id, "entries": entries})
        task.artifacts["rerun_audit"] = "rerun_audit.json"
        self.save(task)
        return task

    def record_usage(self, run_id: str, usage: TopicUsage) -> TopicTask:
        task = self.load(run_id)
        if task.status in {"completed", "failed"}:
            raise ValueError(f"不能为 {task.status} 状态记录主题用量")
        task.usage = usage
        violation = _budget_violation(task.budget, usage)
        if violation:
            task.status = "failed"
            task.failure_stage = task.current_stage
            task.failure_reason = f"预算超限：{violation}"
        self.save(task)
        return task

    def begin_search(self, run_id: str) -> TopicTask:
        task = self.load(run_id)
        if task.status not in {"created", "searching", "awaiting_selection"}:
            raise ValueError(f"当前状态 {task.status} 不能开始搜索")
        task.status = "searching"
        task.current_stage = "searching"
        task.failure_reason = None
        task.failure_stage = None
        self.save(task)
        return task

    def save_search_results(
        self,
        run_id: str,
        candidates: list[TopicSourceCandidate],
        *,
        search_audit: dict,
        warnings: list[str],
    ) -> TopicTask:
        task = self.load(run_id)
        if task.status != "searching":
            raise ValueError("主题任务不在搜索阶段")
        task.candidates = candidates
        task.selected_sources = []
        task.usage = TopicUsage(
            candidate_count=len(candidates),
            selected_source_count=0,
            processed_video_duration_sec=task.usage.processed_video_duration_sec,
            model_calls=task.usage.model_calls,
            estimated_cost_usd=task.usage.estimated_cost_usd,
            elapsed_runtime_sec=task.usage.elapsed_runtime_sec,
        )
        package = task.package
        if package is None:
            raise ValueError("主题任务缺少主题包索引")
        root = self.run_path(run_id)
        sources_path = root / package.sources
        write_json(
            sources_path,
            {
                "topic": task.topic,
                "candidates": [candidate.to_dict() for candidate in candidates],
                "selected_sources": [],
                "warnings": warnings,
            },
        )
        audit_path = root / "search_audit.json"
        write_json(audit_path, search_audit)
        task.artifacts["search_audit"] = "search_audit.json"
        task.artifacts["search_warnings"] = " | ".join(warnings)
        if not candidates:
            task.status = "failed"
            task.current_stage = "searching"
            task.failure_stage = "searching"
            task.failure_reason = "所有搜索 provider 均未返回可用候选"
        else:
            task.status = "awaiting_selection"
            task.current_stage = "awaiting_selection"
        self.save(task)
        return task

    def select_candidates(self, run_id: str, candidate_ids: list[str]) -> TopicTask:
        task = self.load(run_id)
        if task.status != "awaiting_selection":
            raise ValueError("只有等待选择的主题任务可以确认候选")
        selected_ids = list(dict.fromkeys(candidate_id.strip() for candidate_id in candidate_ids if candidate_id.strip()))
        if not selected_ids:
            raise ValueError("至少选择一个候选来源")
        if len(selected_ids) > task.budget.max_selected_sources:
            raise ValueError(f"最多只能选择 {task.budget.max_selected_sources} 个来源")
        candidates = {candidate.candidate_id: candidate for candidate in task.candidates}
        unknown = [candidate_id for candidate_id in selected_ids if candidate_id not in candidates]
        if unknown:
            raise ValueError(f"找不到候选来源：{', '.join(unknown)}")
        confirmed_at = datetime.now(UTC).isoformat()
        selected: list[TopicSourceCandidate] = []
        for candidate in task.candidates:
            candidate.selected = candidate.candidate_id in selected_ids
            candidate.confirmed_at = confirmed_at if candidate.selected else None
            if candidate.selected:
                selected.append(candidate)
        task.selected_sources = selected
        task.usage = TopicUsage(
            candidate_count=len(task.candidates),
            selected_source_count=len(selected),
            processed_video_duration_sec=task.usage.processed_video_duration_sec,
            model_calls=task.usage.model_calls,
            estimated_cost_usd=task.usage.estimated_cost_usd,
            elapsed_runtime_sec=task.usage.elapsed_runtime_sec,
        )
        package = task.package
        if package is None:
            raise ValueError("主题任务缺少主题包索引")
        write_json(
            self.run_path(run_id) / package.sources,
            {
                "topic": task.topic,
                "candidates": [candidate.to_dict() for candidate in task.candidates],
                "selected_sources": [candidate.to_dict() for candidate in selected],
                "warnings": task.artifacts.get("search_warnings", "").split(" | ") if task.artifacts.get("search_warnings") else [],
            },
        )
        task.status = "processing_sources"
        task.current_stage = "processing_sources"
        self.save(task)
        return task

    def _create_package_layout(self, task: TopicTask) -> None:
        package = task.package
        if package is None:
            raise ValueError("主题任务缺少主题包索引")
        root = self.run_path(task.run_id)
        (root / package.references).mkdir(parents=True, exist_ok=True)
        (root / package.evidence).mkdir(parents=True, exist_ok=True)
        write_json(
            root / package.sources,
            {"topic": task.topic, "candidates": [], "selected_sources": []},
        )


def _budget_violation(budget: TopicBudget, usage: TopicUsage) -> str | None:
    limits = (
        ("candidate_count", usage.candidate_count, "max_candidates", budget.max_candidates),
        ("selected_source_count", usage.selected_source_count, "max_selected_sources", budget.max_selected_sources),
        (
            "processed_video_duration_sec",
            usage.processed_video_duration_sec,
            "max_video_duration_sec",
            budget.max_video_duration_sec,
        ),
        ("model_calls", usage.model_calls, "max_model_calls", budget.max_model_calls),
        (
            "estimated_cost_usd",
            usage.estimated_cost_usd,
            "max_estimated_cost_usd",
            budget.max_estimated_cost_usd,
        ),
        ("elapsed_runtime_sec", usage.elapsed_runtime_sec, "max_runtime_sec", budget.max_runtime_sec),
    )
    for usage_name, used, limit_name, limit in limits:
        if used > limit:
            return f"{usage_name}={used} 超过 {limit_name}={limit}"
    return None
