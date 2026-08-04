from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from .models import (
    TOPIC_RUN_STATUSES,
    TopicBudget,
    TopicCachePolicy,
    TopicPackage,
    TopicTask,
    TopicUsage,
)
from .runs import read_json, safe_slug, write_json


_ALLOWED_TRANSITIONS = {
    "created": {"searching", "failed"},
    "searching": {"awaiting_selection", "failed"},
    "awaiting_selection": {"processing_sources", "failed"},
    "processing_sources": {"generating", "failed"},
    "generating": {"scoring", "failed"},
    "scoring": {"completed", "failed"},
    "completed": set(),
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
            package=TopicPackage(
                root=package_root,
                sources=f"{package_root}/sources.json",
                evidence=f"{package_root}/evidence",
                references=f"{package_root}/references",
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
        return TopicTask.from_dict(read_json(path))

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
