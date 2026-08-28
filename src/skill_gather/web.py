from __future__ import annotations

import argparse
import io
import json
import os
import threading
import urllib.error
import urllib.request
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .catalog import CatalogStore, default_catalog_root
from .cli import handle_topic_process, handle_video
from .automation import choose_auto_sources, persist_video_release_gate
from .config import load_config
from .integrations.newapi import NewApiClient, resolve_api_key
from .models import PIPELINE_STAGES
from .planning import assess_ambiguity, build_semantic_plan
from .mvp_check import run_mvp_check
from .readiness_check import run_readiness_check
from .runs import RunStore, read_json, safe_slug
from .search import search_topic as execute_topic_search
from .source import infer_source
from .scoring import normalize_judge_difficulty
from .topics import TopicRunStore


class WebApp:
    def __init__(self, *, config: str, runs: str, out: str, catalog: str | Path | None = None):
        self.config_path = config
        self.runs_path = runs
        self.out_path = out
        self.store = RunStore(runs)
        self.topic_store = TopicRunStore(runs)
        self.catalog_store = CatalogStore(catalog or default_catalog_root())
        self._jobs: dict[str, dict[str, Any]] = {}
        self._topic_jobs: dict[str, dict[str, Any]] = {}
        self._job_cancellations: dict[str, threading.Event] = {}
        self._topic_cancellations: dict[str, threading.Event] = {}
        self._plan_cancellations: dict[str, threading.Event] = {}
        self._plan_jobs: dict[str, dict[str, Any]] = {}
        self._readiness_jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def list_catalog(
        self,
        *,
        query: str = "",
        category: str = "all",
        availability: str = "all",
    ) -> list[dict[str, Any]]:
        return self.catalog_store.list_items(
            query=query,
            category=category,
            availability=availability,
        )

    def get_catalog_item(self, item_id: str) -> dict[str, Any]:
        return self.catalog_store.get_item(item_id)

    def catalog_categories(self) -> list[dict[str, Any]]:
        return self.catalog_store.categories()

    def download_catalog_item(self, item_id: str) -> tuple[str, bytes]:
        return self.catalog_store.build_download(item_id)

    def assemble_agent(self, item_ids: list[str], agent_name: str = "skill-seargen-agent") -> tuple[str, bytes]:
        return self.catalog_store.build_agent_package(item_ids, agent_name)

    def list_agents(self) -> list[dict[str, Any]]:
        return self.catalog_store.list_agents()

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        return self.catalog_store.get_agent(agent_id)

    def download_agent(self, agent_id: str) -> tuple[str, bytes]:
        return self.catalog_store.build_agent_download(agent_id)

    def list_runs(self) -> list[dict[str, Any]]:
        if not self.store.root.exists():
            return []

        result: list[dict[str, Any]] = []
        state_paths = sorted(
            self.store.root.glob("*/run_state.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for state_path in state_paths:
            try:
                state = read_json(state_path)
                run_id = str(state["run_id"])
                manifest = self._optional_json(self.store.manifest_path(run_id))
            except (KeyError, OSError, json.JSONDecodeError, TypeError):
                continue
            result.append(self._run_summary(state, manifest))
        return result

    def list_topics(self) -> list[dict[str, Any]]:
        if not self.topic_store.root.exists():
            return []
        result: list[dict[str, Any]] = []
        for state_path in sorted(
            self.topic_store.root.glob("*/topic_state.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ):
            try:
                task = self.topic_store.load(state_path.parent.name)
            except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
                continue
            result.append(self._topic_summary(task.to_dict()))
        return result

    def get_topic(self, run_id: str) -> dict[str, Any]:
        if not run_id or safe_slug(run_id) != run_id:
            raise FileNotFoundError("无效的主题 run id")
        payload = self.topic_store.load(run_id).to_dict()
        fusion_path = payload.get("artifacts", {}).get("fusion")
        payload["fusion"] = self._optional_json(self.topic_store.run_path(run_id) / fusion_path) if fusion_path else {}
        with self._lock:
            job = self._topic_jobs.get(run_id)
            plan_job = self._plan_jobs.get(run_id)
        if job:
            payload["job"] = dict(job)
        if plan_job:
            payload["plan_job"] = dict(plan_job)
        return payload

    def create_topic(self, topic: str, *, mode: str = "normal", execution_mode: str = "manual", config_path: str | None = None) -> dict[str, Any]:
        topic = topic.strip()
        if not topic:
            raise ValueError("请输入研究主题")
        config = load_config(config_path or self.config_path)
        task = self.topic_store.start_or_resume(
            topic=topic,
            mode=mode,
            budget=config.topic_defaults.budget,
            cache=config.topic_defaults.cache,
            judge_difficulty=config.topic_defaults.judge_difficulty,
            execution_mode=execution_mode,
        )
        assessment = assess_ambiguity(topic, mode)
        if assessment.ambiguous and task.plan is None:
            task = self.topic_store.create_plan(task.run_id)
            client = NewApiClient.from_config(config.newapi)
            if client is not None:
                event = threading.Event()
                with self._lock:
                    self._plan_cancellations[task.run_id] = event
                    self._plan_jobs[task.run_id] = {"active": True, "status": "running"}
                threading.Thread(
                    target=self._enrich_plan,
                    args=(task.run_id, client, config.newapi.distiller_model, event),
                    name=f"skill-gather-plan-{task.run_id}",
                    daemon=True,
                ).start()
            elif execution_mode == "auto" and task.plan:
                task = self.topic_store.confirm_plan(task.run_id, task.plan.recommended_option_id)
        elif not assessment.ambiguous:
            task.plan_audit.append({"event": "plan_skipped", "reason": "deterministic_topic"})
            self.topic_store.save(task)
        return task.to_dict()

    def start_auto_topic(self, run_id: str, *, use_fake: bool = False, config_path: str | None = None) -> dict[str, Any]:
        task = self.topic_store.load(run_id)
        if task.execution_mode != "auto":
            raise ValueError("只有 auto 执行模式可以启动自动任务")
        if task.status == "awaiting_plan_confirmation" and task.plan:
            task = self.topic_store.confirm_plan(run_id, task.plan.recommended_option_id)
        if task.status in {"created", "awaiting_selection"}:
            task = self.topic_store.load(run_id)
            if task.status == "created":
                self.search_topic(run_id, use_fake=use_fake, config_path=config_path)
            task = self.topic_store.load(run_id)
            selected_ids = choose_auto_sources(task)
            if not selected_ids:
                raise ValueError("自动选源没有找到预算内的可用来源")
            task.plan_audit.append({"event": "auto_sources_selected", "candidate_ids": selected_ids})
            self.topic_store.save(task)
            self.select_topic(run_id, selected_ids)
        return self.process_topic(run_id, config_path=config_path)

    def get_plan(self, run_id: str) -> dict[str, Any]:
        task = self.topic_store.load(run_id)
        with self._lock:
            job = dict(self._plan_jobs.get(run_id, {}))
        return {"run_id": run_id, "plan": task.plan.to_dict() if task.plan else None, "audit": task.plan_audit, "job": job}

    def confirm_plan(self, run_id: str, option_id: str, edited: dict[str, object] | None = None) -> dict[str, Any]:
        with self._lock:
            event = self._plan_cancellations.get(run_id)
            if event:
                event.set()
            if run_id in self._plan_jobs:
                self._plan_jobs[run_id] = {"active": False, "status": "confirmed"}
        return self.topic_store.confirm_plan(run_id, option_id, edited=edited).to_dict()

    def interrupt_plan(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            event = self._plan_cancellations.get(run_id)
            if event:
                event.set()
            if run_id in self._plan_jobs:
                self._plan_jobs[run_id] = {"active": False, "status": "interrupted"}
        return self.topic_store.interrupt_plan(run_id).to_dict()

    def _enrich_plan(self, run_id: str, client: NewApiClient, model: str, event: threading.Event) -> None:
        task = self.topic_store.load(run_id)
        plan = build_semantic_plan(task, client, model)
        if event.is_set():
            return
        task = self.topic_store.load(run_id)
        if task.plan and task.plan.warning == "plan_interrupted":
            return
        task.plan = plan
        task.plan_audit.append({"event": "plan_enriched", "method": plan.generation_method})
        self.topic_store.save(task)
        if task.execution_mode == "auto" and task.status == "awaiting_plan_confirmation":
            self.topic_store.confirm_plan(run_id, plan.recommended_option_id)
        with self._lock:
            self._plan_jobs[run_id] = {"active": False, "status": "finished"}

    def pause_topic(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            event = self._topic_cancellations.get(run_id)
            if event:
                event.set()
        return self.topic_store.pause(run_id).to_dict()

    def retry_topic(self, run_id: str) -> dict[str, Any]:
        task = self.topic_store.load(run_id)
        if task.status == "paused":
            task = self.topic_store.resume_paused(run_id)
        elif task.status == "failed":
            task = self.topic_store.resume(run_id)
        else:
            raise ValueError("只有暂停或失败的主题任务可以重试")
        if task.execution_mode == "auto":
            if task.status == "processing_sources":
                return self.process_topic(run_id)
            if task.status in {"created", "awaiting_selection"}:
                return self.start_auto_topic(run_id)
        return task.to_dict()

    def pause_video(self, run_id: str) -> dict[str, Any]:
        state = self.store.load(run_id)
        if state.status not in {"created", "running"}:
            raise ValueError("只有排队或运行中的视频任务可以暂停")
        with self._lock:
            event = self._job_cancellations.get(run_id)
            if event:
                event.set()
            job = self._jobs.setdefault(run_id, {})
            job.update({"active": False, "status": "paused", "error": ""})
        state.status = "paused"
        self.store.save(state)
        return state.to_dict()

    def retry_video(self, run_id: str) -> dict[str, Any]:
        state = self.store.load(run_id)
        if state.status not in {"paused", "failed"}:
            raise ValueError("只有暂停或失败的视频任务可以重试")
        manifest = self._optional_json(self.store.manifest_path(run_id))
        url = str(manifest.get("url", ""))
        if not url:
            raise ValueError("视频任务缺少可重试的来源 URL")
        state.status = "created"
        self.store.save(state)
        return self.start_video(url, judge_difficulty=state.judge_difficulty, execution_mode=state.execution_mode)

    def list_work_items(self) -> list[dict[str, Any]]:
        return [{**item, "kind": "video"} for item in self.list_runs()] + [{**item, "kind": "topic"} for item in self.list_topics()]

    def get_work_item(self, run_id: str) -> dict[str, Any]:
        if self.topic_store.state_path(run_id).exists():
            return {**self.get_topic(run_id), "kind": "topic"}
        return {**self.get_run(run_id), "kind": "video"}

    def metrics(self) -> dict[str, Any]:
        items = self.list_work_items()
        counts: dict[str, int] = {}
        for item in items:
            status = str(item.get("status", "unknown"))
            counts[status] = counts.get(status, 0) + 1
        model_availability = "unconfigured"
        try:
            config = load_config(self.config_path)
            model_availability = "configured_unverified" if resolve_api_key(config.newapi.api_key_env) else "missing_api_key"
        except (OSError, ValueError):
            pass
        return {"total": len(items), "by_status": counts, "active_jobs": sum(1 for job in [*self._jobs.values(), *self._topic_jobs.values()] if job.get("active")), "model_availability": model_availability}

    def results(self, *, result_type: str = "all", status: str = "all", min_score: int = 0, risk: str = "", source: str = "") -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if result_type in {"all", "video"}:
            for item in self.list_runs():
                if item.get("status") not in {"completed", "failed"}:
                    continue
                detail = self.get_run(item["run_id"])
                score = int(detail.get("score", {}).get("final_score", 0) or 0)
                result_status = str(detail.get("score", {}).get("final_status", "failed"))
                risks = detail.get("risk_flags", [])
                if (status != "all" and result_status != status) or score < min_score or (risk and risk not in risks):
                    continue
                if source and source not in {detail.get("manifest", {}).get("source", ""), detail.get("manifest", {}).get("author", "")}:
                    continue
                results.append({**item, "kind": "video", "score": score, "result_status": result_status, "risk_flags": risks})
        if result_type in {"all", "topic", "skill"}:
            for item in self.list_topics():
                if item.get("status") not in {"completed", "failed"}:
                    continue
                if result_type == "skill" and item.get("mode") != "technical":
                    continue
                detail = self.get_topic(item["run_id"])
                score_payload: dict[str, Any] = {}
                score_path = detail.get("artifacts", {}).get("score")
                if score_path:
                    score_payload = self._optional_json(self.topic_store.run_path(item["run_id"]) / score_path)
                score = int(score_payload.get("final_score", 0) or 0)
                result_status = str(score_payload.get("final_status", "needs_review" if item.get("status") == "completed" else "failed"))
                risks = detail.get("fusion", {}).get("risk_flags", []) if isinstance(detail.get("fusion"), dict) else []
                selected_hosts = {candidate.get("host", "") for candidate in detail.get("selected_sources", [])}
                if (status != "all" and result_status != status) or score < min_score or (risk and risk not in risks) or (source and source not in selected_hosts):
                    continue
                results.append({**item, "kind": "topic", "score": score, "result_status": result_status, "risk_flags": risks, "artifacts": detail.get("artifacts", {})})
        return results

    def get_result(self, run_id: str) -> dict[str, Any]:
        if self.topic_store.state_path(run_id).exists():
            detail = self.get_topic(run_id)
            run_root = self.topic_store.run_path(run_id)
            artifacts = detail.get("artifacts", {})
            course = self._artifact_text(run_root, artifacts.get("course"))
            skill = self._artifact_text(run_root, artifacts.get("skill"))
            return {
                "run_id": run_id,
                "kind": "topic",
                "title": detail.get("topic", run_id),
                "course": course,
                "knowledge": self._artifact_text(run_root, artifacts.get("knowledge")),
                "skill": skill,
                "score": self._optional_json(run_root / artifacts["score"]) if artifacts.get("score") else {},
                "fusion": detail.get("fusion", {}),
                "risk_flags": detail.get("fusion", {}).get("risk_flags", []),
                "downloads": {"course": bool(course), "skill": bool(skill)},
            }
        detail = self.get_run(run_id)
        return {
            "run_id": run_id,
            "kind": "video",
            "title": detail.get("title", run_id),
            "course": "",
            "knowledge": "",
            "skill": "",
            "score": detail.get("score", {}),
            "evidence": detail.get("evidence", []),
            "risk_flags": detail.get("risk_flags", []),
            "downloads": {"course": False, "skill": False},
        }

    def download_result(self, run_id: str, artifact_kind: str) -> tuple[str, str, bytes]:
        if not self.topic_store.state_path(run_id).exists():
            raise FileNotFoundError(f"找不到主题 run：{run_id}")
        detail = self.get_topic(run_id)
        run_root = self.topic_store.run_path(run_id)
        artifacts = detail.get("artifacts", {})
        package_name = safe_slug(run_id)

        if artifact_kind == "course":
            course_path = self._artifact_path(run_root, artifacts.get("course"))
            if course_path is None or not course_path.is_file() or course_path.stat().st_size > 2 * 1024 * 1024:
                raise FileNotFoundError("课程文档尚未生成或不可下载")
            return f"{package_name}-course.md", "text/markdown; charset=utf-8", course_path.read_bytes()

        if artifact_kind == "skill":
            skill_path = self._artifact_path(run_root, artifacts.get("skill"))
            if skill_path is None or not skill_path.is_file():
                raise FileNotFoundError("Skill 包尚未生成")
            package_root = skill_path.parent.resolve()
            run_root_resolved = run_root.resolve()
            try:
                package_root.relative_to(run_root_resolved)
            except ValueError as exc:
                raise ValueError("Skill 包路径超出当前 run 目录") from exc

            buffer = io.BytesIO()
            total_size = 0
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(package_root.rglob("*")):
                    if not path.is_file():
                        continue
                    resolved = path.resolve()
                    try:
                        relative = resolved.relative_to(package_root)
                    except ValueError as exc:
                        raise ValueError("Skill 包包含指向包目录外的文件") from exc
                    total_size += resolved.stat().st_size
                    if total_size > 100 * 1024 * 1024:
                        raise ValueError("Skill 包超过 100 MB 下载上限")
                    archive.write(resolved, Path(package_name) / relative)
            return f"{package_name}-skill.zip", "application/zip", buffer.getvalue()

        raise ValueError(f"不支持的产物类型：{artifact_kind}")

    def search_topic(self, run_id: str, *, use_fake: bool = False, config_path: str | None = None) -> dict[str, Any]:
        config = load_config(config_path or self.config_path)
        task = self.topic_store.begin_search(run_id)
        candidates, batches, query_audit, intent = execute_topic_search(
            task,
            config,
            cache_path=Path(self.runs_path) / "search_cache.sqlite3",
            use_fake=use_fake,
        )
        warnings = [
            f"{batch.provider}: {warning}"
            for batch in batches
            for warning in batch.warnings
        ]
        task = self.topic_store.save_search_results(
            run_id,
            candidates,
            search_audit={
                "topic": task.topic,
                "mode": task.mode,
                "intent": intent.to_dict(),
                "queries": query_audit,
                "batches": [batch.to_dict() for batch in batches],
            },
            warnings=warnings,
        )
        return task.to_dict()

    def select_topic(self, run_id: str, candidate_ids: list[str]) -> dict[str, Any]:
        return self.topic_store.select_candidates(run_id, candidate_ids).to_dict()

    def resume_topic(self, run_id: str) -> dict[str, Any]:
        return self.topic_store.resume(run_id).to_dict()

    def process_topic(
        self,
        run_id: str,
        *,
        vision_mode: str = "sampled",
        vision_frame_limit: int = 12,
        config_path: str | None = None,
    ) -> dict[str, Any]:
        task = self.topic_store.load(run_id)
        if task.status != "processing_sources":
            raise ValueError("只有已确认来源的主题任务可以开始处理")
        with self._lock:
            current = self._topic_jobs.get(run_id)
            if current and current.get("active"):
                raise RuntimeError("这个主题正在处理中")
            self._topic_jobs[run_id] = {"active": True, "status": "queued", "error": ""}
            self._topic_cancellations[run_id] = threading.Event()

        thread = threading.Thread(
            target=self._run_topic,
            args=(run_id, vision_mode, vision_frame_limit, config_path or self.config_path),
            name=f"skill-gather-topic-{run_id}",
            daemon=True,
        )
        thread.start()
        return {"run_id": run_id, "status": "queued"}

    def _run_topic(self, run_id: str, vision_mode: str, vision_frame_limit: int, config_path: str) -> None:
        with self._lock:
            self._topic_jobs[run_id]["status"] = "running"
        stdout = io.StringIO()
        stderr = io.StringIO()
        args = argparse.Namespace(
            run_id=run_id,
            runs=self.runs_path,
            timeout_sec=15,
            config=config_path,
            vision_mode=vision_mode,
            vision_frame_limit=vision_frame_limit,
            cancel_event=self._topic_cancellations.get(run_id),
        )
        try:
            if args.cancel_event and args.cancel_event.is_set():
                exit_code = 0
                payload = {"run_id": run_id, "status": "paused"}
            else:
                exit_code = handle_topic_process(args, stdout, stderr)
                payload = {}
            error = stderr.getvalue().strip()
            try:
                if stdout.getvalue():
                    payload = json.loads(stdout.getvalue())
            except json.JSONDecodeError:
                pass
            if exit_code != 0 and not error:
                error = str(payload.get("failure_reason") or "主题处理未成功完成")
        except Exception as exc:  # Preserve the error for the local status UI.
            exit_code = 2
            error = str(exc)
            payload = {}
        with self._lock:
            self._topic_jobs[run_id] = {
                "active": False,
                "status": "paused" if payload.get("status") == "paused" else ("failed" if exit_code != 0 else "finished"),
                "error": error,
                "result": payload,
            }

    def get_run(self, run_id: str) -> dict[str, Any]:
        if not run_id or safe_slug(run_id) != run_id:
            raise FileNotFoundError("无效的 run id")

        state = self.store.load(run_id).to_dict()
        run_dir = self.store.run_path(run_id)
        manifest = self._optional_json(self.store.manifest_path(run_id))
        timeline = self._optional_json(self.store.evidence_timeline_path(run_id))
        score = self._optional_json(run_dir / "score.json")
        metadata = self._optional_json(run_dir / "metadata.json")
        human_review = self._optional_json(run_dir / "human_review.json")
        prefilter = self._optional_json(run_dir / "prefilter.json")
        with self._lock:
            job = dict(self._jobs.get(run_id, {}))
        return {
            **self._run_summary(state, manifest),
            "completed_stages": state.get("completed_stages", []),
            "failure_reason": state.get("failure_reason"),
            "artifacts": state.get("artifacts", {}),
            "manifest": manifest,
            "evidence": timeline.get("items", []),
            "evidence_meta": {
                "frame_budget": timeline.get("frame_budget", 0),
                "sampling_strategy": timeline.get("sampling_strategy", ""),
            },
            "score": score,
            "human_review": human_review,
            "prefilter": prefilter,
            "risk_flags": metadata.get("risk_flags", manifest.get("risk_flags", [])),
            "job": job,
            "judge_difficulty": score.get("judge_difficulty") or state.get("judge_difficulty", "standard"),
        }

    def start_video(self, url: str, api_key: str = "", judge_difficulty: str = "standard", execution_mode: str = "manual") -> dict[str, Any]:
        url = url.strip()
        if not url:
            raise ValueError("请输入 B 站公开视频 URL")

        judge_difficulty = normalize_judge_difficulty(judge_difficulty)
        if execution_mode not in {"manual", "auto"}:
            raise ValueError("执行模式必须是 manual 或 auto")
        config = load_config(self.config_path)
        self._apply_api_key(config.newapi.api_key_env, api_key)
        source = infer_source(url)
        state = self.store.start_or_resume(source.source, source.source_id)
        state.judge_difficulty = judge_difficulty
        state.execution_mode = execution_mode
        self.store.save(state)
        with self._lock:
            current = self._jobs.get(state.run_id)
            if current and current.get("active"):
                raise RuntimeError("这个视频正在处理中")
            self._jobs[state.run_id] = {"active": True, "status": "queued", "error": ""}
            self._job_cancellations[state.run_id] = threading.Event()

        thread = threading.Thread(
            target=self._run_video,
            args=(state.run_id, url, judge_difficulty, execution_mode),
            name=f"skill-gather-{state.run_id}",
            daemon=True,
        )
        thread.start()
        return {"run_id": state.run_id, "status": "queued"}

    def run_mvp_check(self, api_key: str = "") -> dict[str, Any]:
        config = load_config(self.config_path)
        self._apply_api_key(config.newapi.api_key_env, api_key)
        return run_mvp_check(config)

    def run_readiness_check(self, api_key: str = "", load_asr_model: bool = True) -> dict[str, Any]:
        config = load_config(self.config_path)
        self._apply_api_key(config.newapi.api_key_env, api_key)
        return run_readiness_check(config, load_asr_model=load_asr_model)

    def start_readiness_check(self, api_key: str = "", load_asr_model: bool = True) -> dict[str, Any]:
        import uuid

        job_id = f"readiness-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._readiness_jobs[job_id] = {
                "job_id": job_id,
                "active": True,
                "status": "queued",
                "index": 0,
                "total": 0,
                "progress": 0,
                "current": "",
                "checks": [],
                "result": None,
                "error": "",
            }

        thread = threading.Thread(
            target=self._run_readiness_check,
            args=(job_id, api_key, load_asr_model),
            name=f"skill-gather-{job_id}",
            daemon=True,
        )
        thread.start()
        return self.get_readiness_check(job_id)

    def get_readiness_check(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._readiness_jobs.get(job_id)
            if not job:
                raise FileNotFoundError("未找到真实功能检查任务")
            return dict(job)

    def _run_readiness_check(self, job_id: str, api_key: str, load_asr_model: bool) -> None:
        try:
            config = load_config(self.config_path)
            self._apply_api_key(config.newapi.api_key_env, api_key)

            def on_progress(event: dict[str, Any]) -> None:
                with self._lock:
                    job = self._readiness_jobs[job_id]
                    total = int(event.get("total") or 0)
                    index = int(event.get("index") or 0)
                    job.update({
                        "status": "running" if event.get("event") != "finished" else "finished",
                        "index": index,
                        "total": total,
                        "progress": round(index / total * 100) if total else 0,
                        "current": event.get("label", ""),
                        "checks": event.get("checks", job.get("checks", [])),
                    })
                    if event.get("event") == "finished":
                        job["result"] = event.get("result")

            result = run_readiness_check(
                config,
                load_asr_model=load_asr_model,
                on_progress=on_progress,
            )
            with self._lock:
                self._readiness_jobs[job_id].update({
                    "active": False,
                    "status": "finished",
                    "progress": 100,
                    "current": "已完成",
                    "result": result,
                    "checks": result.get("checks", []),
                    "error": "",
                })
        except Exception as exc:
            with self._lock:
                self._readiness_jobs[job_id].update({
                    "active": False,
                    "status": "failed",
                    "current": "检查失败",
                    "error": str(exc),
                })

    def list_models(self, api_key: str = "") -> dict[str, Any]:
        config = load_config(self.config_path)
        api_key = api_key.strip() or resolve_api_key(str(config.newapi.api_key_env))
        if not api_key:
            raise ValueError("请先填写 NewAPI API Key")

        request = urllib.request.Request(
            url=f"{config.newapi.base_url.rstrip('/')}/models",
            method="GET",
        )
        request.add_header("Authorization", f"Bearer {api_key}")
        request.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
            raise ValueError(f"IceAPI 模型列表请求失败：{body_text}") from exc
        except urllib.error.URLError as exc:
            raise ValueError(f"IceAPI 模型列表不可达：{exc.reason}") from exc

        raw = json.loads(payload)
        models = _extract_model_ids(raw)
        return {"models": models, "suggestions": _suggest_models(models), "catalog_only": True}

    def delete_run(self, run_id: str) -> dict[str, Any]:
        if not run_id or safe_slug(run_id) != run_id:
            raise FileNotFoundError("无效的 run id")

        self.store.load(run_id)
        with self._lock:
            job = self._jobs.get(run_id)
            if job and job.get("active"):
                raise RuntimeError("这个视频正在处理中，暂时不能清理")

        self.store.delete_run(run_id)
        with self._lock:
            self._jobs.pop(run_id, None)
        return {"run_id": run_id, "status": "deleted"}

    def archive_topic(self, run_id: str) -> dict[str, Any]:
        if not run_id or safe_slug(run_id) != run_id:
            raise FileNotFoundError("无效的主题 run id")

        task = self.topic_store.load(run_id)
        with self._lock:
            topic_job = self._topic_jobs.get(run_id)
            plan_job = self._plan_jobs.get(run_id)
            if (topic_job and topic_job.get("active")) or (plan_job and plan_job.get("active")):
                raise RuntimeError("这个主题正在处理，请先暂停或等待任务结束")
        if task.status in {"planning", "searching", "generating", "scoring"}:
            raise RuntimeError("这个主题正在处理，请先暂停或等待任务结束")

        archive_root = Path(self.runs_path).resolve().parent / ".run-archive" / "topics"
        destination = self.topic_store.archive(run_id, archive_root)
        with self._lock:
            self._topic_jobs.pop(run_id, None)
            self._topic_cancellations.pop(run_id, None)
            self._plan_jobs.pop(run_id, None)
            self._plan_cancellations.pop(run_id, None)
        return {
            "run_id": run_id,
            "status": "archived",
            "archive_path": str(destination.relative_to(archive_root.parent.parent)),
        }

    @staticmethod
    def _apply_api_key(env_name: str, api_key: str) -> None:
        api_key = api_key.strip()
        if api_key:
            os.environ[str(env_name)] = api_key

    def _run_video(self, run_id: str, url: str, judge_difficulty: str, execution_mode: str = "manual") -> None:
        with self._lock:
            self._jobs[run_id]["status"] = "running"

        stdout = io.StringIO()
        stderr = io.StringIO()
        args = argparse.Namespace(
            url=url,
            config=self.config_path,
            out=self.out_path,
            runs=self.runs_path,
            judge_difficulty=judge_difficulty,
        )
        try:
            event = self._job_cancellations.get(run_id)
            if event and event.is_set():
                return
            exit_code = handle_video(args, stdout, stderr)
            error = stderr.getvalue().strip()
            if event and event.is_set():
                state = self.store.load(run_id)
                state.status = "paused"
                self.store.save(state)
                return
            if exit_code == 0 and execution_mode == "auto":
                run_root = self.store.run_path(run_id)
                persist_video_release_gate(run_root, self._optional_json(run_root / "score.json"))
            if exit_code != 0 and not error:
                error = "处理未成功完成"
        except Exception as exc:  # Preserve the error for the local status UI.
            error = str(exc)
        with self._lock:
            self._jobs[run_id] = {
                "active": False,
                "status": "failed" if error else "finished",
                "error": error,
            }

    def _run_summary(self, state: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
        completed = state.get("completed_stages", [])
        progress = round(len(completed) / len(PIPELINE_STAGES) * 100)
        return {
            "run_id": state.get("run_id", ""),
            "source_id": state.get("source_id", ""),
            "title": manifest.get("title") or state.get("source_id", "未命名视频"),
            "author": manifest.get("author", ""),
            "url": manifest.get("url", ""),
            "status": state.get("status", "created"),
            "current_stage": state.get("current_stage", "manifest"),
            "progress": progress,
        }

    @staticmethod
    def _topic_summary(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": state.get("run_id", ""),
            "topic": state.get("topic", ""),
            "mode": state.get("mode", "normal"),
            "status": state.get("status", "created"),
            "current_stage": state.get("current_stage", "created"),
            "execution_mode": state.get("execution_mode", "manual"),
            "budget": state.get("budget", {}),
            "usage": state.get("usage", {}),
            "failure_reason": state.get("failure_reason"),
            "candidate_count": len(state.get("candidates", [])),
            "selected_source_count": len(state.get("selected_sources", [])),
            "updated_at": state.get("updated_at", ""),
        }

    @staticmethod
    def _optional_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            value = read_json(path)
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _artifact_text(run_root: Path, relative_path: Any) -> str:
        target = WebApp._artifact_path(run_root, relative_path)
        if target is None:
            return ""
        if not target.is_file() or target.stat().st_size > 2 * 1024 * 1024:
            return ""
        try:
            return target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""

    @staticmethod
    def _artifact_path(run_root: Path, relative_path: Any) -> Path | None:
        if not relative_path:
            return None
        root = run_root.resolve()
        target = (run_root / str(relative_path)).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return None
        return target


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    config: str = "config.json",
    runs: str = "./runs",
    out: str = "./skills",
    catalog: str | Path | None = None,
    assets_dir: str | Path | None = None,
) -> ThreadingHTTPServer:
    app = WebApp(config=config, runs=runs, out=out, catalog=catalog)
    asset_root = Path(assets_dir).resolve() if assets_dir else None
    if asset_root is not None:
        required_assets = ("index.html", "app.css", "app.js", "react-nav.js")
        missing = [name for name in required_assets if not (asset_root / name).is_file()]
        if missing:
            raise FileNotFoundError(f"前端静态资源不完整：{', '.join(missing)}")
    handler = _handler_for(app, asset_root)
    return ThreadingHTTPServer((host, port), handler)


def serve(**kwargs: Any) -> None:
    server = create_server(**kwargs)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _handler_for(app: WebApp, asset_root: Path | None = None) -> type[BaseHTTPRequestHandler]:
    class RequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/api/catalog":
                try:
                    self._send_json({"items": app.list_catalog(
                        query=query.get("query", [""])[0],
                        category=query.get("category", ["all"])[0],
                        availability=query.get("availability", ["all"])[0],
                    )})
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            if path == "/api/catalog/categories":
                try:
                    self._send_json({"categories": app.catalog_categories()})
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            if path.startswith("/api/catalog/") and path.endswith("/download"):
                item_id = unquote(path.removeprefix("/api/catalog/").removesuffix("/download").strip("/"))
                try:
                    filename, payload = app.download_catalog_item(item_id)
                except PermissionError as exc:
                    self._send_error_json(HTTPStatus.FORBIDDEN, str(exc))
                    return
                except FileNotFoundError as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                    return
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                    return
                self._send_download(filename, payload)
                return
            if path.startswith("/api/catalog/"):
                item_id = unquote(path.removeprefix("/api/catalog/"))
                try:
                    self._send_json(app.get_catalog_item(item_id))
                except FileNotFoundError as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            if path == "/api/work-items":
                self._send_json({"items": app.list_work_items()})
                return
            if path.startswith("/api/work-items/"):
                run_id = unquote(path.removeprefix("/api/work-items/"))
                try:
                    self._send_json(app.get_work_item(run_id))
                except FileNotFoundError as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                return
            if path == "/api/metrics":
                self._send_json(app.metrics())
                return
            if path == "/api/results":
                self._send_json({"results": app.results(
                    result_type=query.get("type", ["all"])[0],
                    status=query.get("status", ["all"])[0],
                    min_score=int(query.get("min_score", ["0"])[0]),
                    risk=query.get("risk", [""])[0],
                    source=query.get("source", [""])[0],
                )})
                return
            if path == "/api/agents":
                try:
                    self._send_json({"agents": app.list_agents()})
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            if path.startswith("/api/agents/") and path.endswith("/download"):
                agent_id = unquote(path.removeprefix("/api/agents/").removesuffix("/download").strip("/"))
                try:
                    filename, payload = app.download_agent(agent_id)
                except (FileNotFoundError, ValueError) as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                    return
                self._send_download(filename, payload)
                return
            if path.startswith("/api/agents/"):
                agent_id = unquote(path.removeprefix("/api/agents/"))
                try:
                    self._send_json(app.get_agent(agent_id))
                except FileNotFoundError as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                except ValueError as exc:
                    self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            if path.startswith("/api/results/"):
                result_path = path.removeprefix("/api/results/")
                parts = result_path.split("/")
                if len(parts) == 3 and parts[1] == "download":
                    run_id = unquote(parts[0])
                    artifact_kind = unquote(parts[2])
                    try:
                        filename, content_type, payload = app.download_result(run_id, artifact_kind)
                        self._send_download(filename, payload, content_type=content_type)
                    except FileNotFoundError as exc:
                        self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                    except ValueError as exc:
                        self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                    return
                run_id = unquote(result_path)
                try:
                    self._send_json(app.get_result(run_id))
                except FileNotFoundError as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                return
            if path == "/api/runs":
                self._send_json({"runs": app.list_runs()})
                return
            if path == "/api/topics":
                self._send_json({"topics": app.list_topics()})
                return
            if path.startswith("/api/readiness-check/"):
                job_id = unquote(path.removeprefix("/api/readiness-check/").strip("/"))
                try:
                    self._send_json(app.get_readiness_check(job_id))
                except FileNotFoundError as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                return
            if path.startswith("/api/topics/") and path.endswith("/plan"):
                run_id = unquote(path.removeprefix("/api/topics/").removesuffix("/plan").strip("/"))
                try:
                    self._send_json(app.get_plan(run_id))
                except FileNotFoundError as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                return
            if path.startswith("/api/topics/"):
                run_id = unquote(path.removeprefix("/api/topics/"))
                try:
                    payload = app.get_topic(run_id)
                except FileNotFoundError as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                    return
                self._send_json(payload)
                return
            if path.startswith("/api/runs/"):
                run_id = unquote(path.removeprefix("/api/runs/"))
                try:
                    payload = app.get_run(run_id)
                except FileNotFoundError as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                    return
                self._send_json(payload)
                return
            if path in {"/", "/index.html"}:
                self._send_asset("index.html", "text/html; charset=utf-8")
                return
            if path == "/app.css":
                self._send_asset("app.css", "text/css; charset=utf-8")
                return
            if path == "/app.js":
                self._send_asset("app.js", "text/javascript; charset=utf-8")
                return
            if path == "/react-nav.js":
                self._send_asset("react-nav.js", "text/javascript; charset=utf-8")
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_DELETE(self) -> None:
            path = urlsplit(self.path).path
            if path.startswith("/api/topics/"):
                run_id = unquote(path.removeprefix("/api/topics/"))
                try:
                    payload = app.archive_topic(run_id)
                except RuntimeError as exc:
                    self._send_error_json(HTTPStatus.CONFLICT, str(exc))
                    return
                except FileNotFoundError as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                    return
                self._send_json(payload)
                return
            if path.startswith("/api/runs/"):
                run_id = unquote(path.removeprefix("/api/runs/"))
                try:
                    payload = app.delete_run(run_id)
                except RuntimeError as exc:
                    self._send_error_json(HTTPStatus.CONFLICT, str(exc))
                    return
                except FileNotFoundError as exc:
                    self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
                    return
                self._send_json(payload)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            try:
                body = self._read_json_body()
                if path == "/api/assemble":
                    item_ids = body.get("item_ids", [])
                    if not isinstance(item_ids, list) or not all(isinstance(value, str) for value in item_ids):
                        raise ValueError("item_ids 必须是字符串数组")
                    filename, payload = app.assemble_agent(item_ids, str(body.get("agent_name", "skill-seargen-agent")))
                    self._send_download(filename, payload)
                    return
                if path == "/api/runs":
                    result = app.start_video(
                        str(body.get("url", "")),
                        str(body.get("api_key", "")),
                        str(body.get("judge_difficulty", "standard")),
                        str(body.get("execution_mode", "manual")),
                    )
                    self._send_json(result, HTTPStatus.ACCEPTED)
                    return
                if path.startswith("/api/runs/"):
                    suffix = path.removeprefix("/api/runs/")
                    if suffix.endswith("/pause"):
                        self._send_json(app.pause_video(suffix.removesuffix("/pause").strip("/")))
                        return
                    if suffix.endswith("/retry"):
                        self._send_json(app.retry_video(suffix.removesuffix("/retry").strip("/")), HTTPStatus.ACCEPTED)
                        return
                if path == "/api/topics":
                    result = app.create_topic(
                        str(body.get("topic", "")),
                        mode=str(body.get("mode", "normal")),
                        execution_mode=str(body.get("execution_mode", "manual")),
                        config_path=str(body.get("config", "")) or None,
                    )
                    self._send_json(result, HTTPStatus.CREATED)
                    return
                if path.startswith("/api/topics/"):
                    suffix = path.removeprefix("/api/topics/")
                    if suffix.endswith("/search"):
                        run_id = suffix.removesuffix("/search").strip("/")
                        result = app.search_topic(run_id, use_fake=bool(body.get("fake", False)), config_path=str(body.get("config", "")) or None)
                        self._send_json(result, HTTPStatus.OK)
                        return
                    if suffix.endswith("/plan/confirm"):
                        run_id = suffix.removesuffix("/plan/confirm").strip("/")
                        edited = body.get("edited")
                        if edited is not None and not isinstance(edited, dict):
                            raise ValueError("edited 必须是对象")
                        self._send_json(app.confirm_plan(run_id, str(body.get("option_id", "")), edited=edited))
                        return
                    if suffix.endswith("/plan/interrupt"):
                        run_id = suffix.removesuffix("/plan/interrupt").strip("/")
                        self._send_json(app.interrupt_plan(run_id))
                        return
                    if suffix.endswith("/auto"):
                        run_id = suffix.removesuffix("/auto").strip("/")
                        result = app.start_auto_topic(run_id, use_fake=bool(body.get("fake", False)), config_path=str(body.get("config", "")) or None)
                        self._send_json(result, HTTPStatus.ACCEPTED)
                        return
                    if suffix.endswith("/pause"):
                        run_id = suffix.removesuffix("/pause").strip("/")
                        self._send_json(app.pause_topic(run_id))
                        return
                    if suffix.endswith("/retry"):
                        run_id = suffix.removesuffix("/retry").strip("/")
                        self._send_json(app.retry_topic(run_id))
                        return
                    if suffix.endswith("/select"):
                        run_id = suffix.removesuffix("/select").strip("/")
                        candidate_ids = body.get("candidate_ids", [])
                        if not isinstance(candidate_ids, list):
                            raise ValueError("candidate_ids 必须是数组")
                        result = app.select_topic(run_id, [str(value) for value in candidate_ids])
                        self._send_json(result, HTTPStatus.OK)
                        return
                    if suffix.endswith("/resume"):
                        run_id = suffix.removesuffix("/resume").strip("/")
                        result = app.resume_topic(run_id)
                        self._send_json(result, HTTPStatus.OK)
                        return
                    if suffix.endswith("/process"):
                        run_id = suffix.removesuffix("/process").strip("/")
                        result = app.process_topic(
                            run_id,
                            vision_mode=str(body.get("vision_mode", "sampled")),
                            vision_frame_limit=int(body.get("vision_frame_limit", 12)),
                            config_path=str(body.get("config", "")) or None,
                        )
                        self._send_json(result, HTTPStatus.ACCEPTED)
                        return
                if path == "/api/mvp-check":
                    self._send_json(app.run_mvp_check(str(body.get("api_key", ""))))
                    return
                if path == "/api/readiness-check/start":
                    self._send_json(app.start_readiness_check(
                        str(body.get("api_key", "")),
                        bool(body.get("load_asr_model", True)),
                    ), HTTPStatus.ACCEPTED)
                    return
                if path == "/api/readiness-check":
                    self._send_json(app.run_readiness_check(
                        str(body.get("api_key", "")),
                        bool(body.get("load_asr_model", True)),
                    ))
                    return
                if path == "/api/models":
                    self._send_json(app.list_models(str(body.get("api_key", ""))))
                    return
            except RuntimeError as exc:
                self._send_error_json(HTTPStatus.CONFLICT, str(exc))
                return
            except PermissionError as exc:
                self._send_error_json(HTTPStatus.FORBIDDEN, str(exc))
                return
            except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def _read_json_body(self) -> dict[str, Any]:
            if self.headers.get_content_type() != "application/json":
                raise ValueError("请求必须使用 application/json")
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64 * 1024:
                raise ValueError("请求内容为空或过大")
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("请求内容必须是 JSON 对象")
            return value

        def _send_asset(self, name: str, content_type: str) -> None:
            data = (asset_root / name).read_bytes() if asset_root else files("skill_gather.web_assets").joinpath(name).read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_download(self, filename: str, payload: bytes, *, content_type: str = "application/zip") -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _send_error_json(self, status: HTTPStatus, message: str) -> None:
            self._send_json({"error": message}, status)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return RequestHandler


def _extract_model_ids(payload: Any) -> list[str]:
    values = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        raise ValueError("IceAPI 模型列表响应格式不正确")

    models: list[str] = []
    for item in values:
        model_id = item.get("id") if isinstance(item, dict) else item
        model_id = str(model_id or "").strip()
        if model_id and model_id not in models:
            models.append(model_id)
    return sorted(models, key=str.lower)


def _suggest_models(models: list[str]) -> dict[str, list[str]]:
    asr_terms = ("whisper", "asr", "audio", "transcribe", "speech")
    vision_terms = ("vision", "vl", "4o", "o1", "o3", "gemini", "claude", "qwen-vl", "qvq")
    text_terms = ("gpt", "claude", "gemini", "qwen", "deepseek", "glm", "yi", "moonshot", "doubao", "hunyuan")

    def matches(model: str, terms: tuple[str, ...]) -> bool:
        lower = model.lower()
        return any(term in lower for term in terms)

    asr = [model for model in models if matches(model, asr_terms)]
    vision = [model for model in models if matches(model, vision_terms)]
    text = [model for model in models if matches(model, text_terms) and model not in asr]
    return {
        "asr": asr[:20],
        "vision": vision[:30],
        "text": text[:30],
    }
