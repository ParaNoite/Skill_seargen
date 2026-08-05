from __future__ import annotations

import argparse
import io
import json
import os
import threading
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .cli import handle_video
from .config import load_config
from .integrations.newapi import resolve_api_key
from .models import PIPELINE_STAGES
from .mvp_check import run_mvp_check
from .runs import RunStore, read_json, safe_slug
from .search import search_topic as execute_topic_search
from .source import infer_source
from .scoring import normalize_judge_difficulty
from .topics import TopicRunStore


class WebApp:
    def __init__(self, *, config: str, runs: str, out: str):
        self.config_path = config
        self.runs_path = runs
        self.out_path = out
        self.store = RunStore(runs)
        self.topic_store = TopicRunStore(runs)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

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
        return self.topic_store.load(run_id).to_dict()

    def create_topic(self, topic: str, *, mode: str = "normal", config_path: str | None = None) -> dict[str, Any]:
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
        )
        return task.to_dict()

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

    def start_video(self, url: str, api_key: str = "", judge_difficulty: str = "standard") -> dict[str, Any]:
        url = url.strip()
        if not url:
            raise ValueError("请输入 B 站公开视频 URL")

        judge_difficulty = normalize_judge_difficulty(judge_difficulty)
        config = load_config(self.config_path)
        self._apply_api_key(config.newapi.api_key_env, api_key)
        source = infer_source(url)
        state = self.store.start_or_resume(source.source, source.source_id)
        state.judge_difficulty = judge_difficulty
        self.store.save(state)
        with self._lock:
            current = self._jobs.get(state.run_id)
            if current and current.get("active"):
                raise RuntimeError("这个视频正在处理中")
            self._jobs[state.run_id] = {"active": True, "status": "queued", "error": ""}

        thread = threading.Thread(
            target=self._run_video,
            args=(state.run_id, url, judge_difficulty),
            name=f"skill-gather-{state.run_id}",
            daemon=True,
        )
        thread.start()
        return {"run_id": state.run_id, "status": "queued"}

    def run_mvp_check(self, api_key: str = "") -> dict[str, Any]:
        config = load_config(self.config_path)
        self._apply_api_key(config.newapi.api_key_env, api_key)
        return run_mvp_check(config)

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
        return {"models": models, "suggestions": _suggest_models(models)}

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

    @staticmethod
    def _apply_api_key(env_name: str, api_key: str) -> None:
        api_key = api_key.strip()
        if api_key:
            os.environ[str(env_name)] = api_key

    def _run_video(self, run_id: str, url: str, judge_difficulty: str) -> None:
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
            exit_code = handle_video(args, stdout, stderr)
            error = stderr.getvalue().strip()
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


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    config: str = "config.json",
    runs: str = "./runs",
    out: str = "./skills",
) -> ThreadingHTTPServer:
    app = WebApp(config=config, runs=runs, out=out)
    handler = _handler_for(app)
    return ThreadingHTTPServer((host, port), handler)


def serve(**kwargs: Any) -> None:
    server = create_server(**kwargs)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _handler_for(app: WebApp) -> type[BaseHTTPRequestHandler]:
    class RequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/api/runs":
                self._send_json({"runs": app.list_runs()})
                return
            if path == "/api/topics":
                self._send_json({"topics": app.list_topics()})
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
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_DELETE(self) -> None:
            path = urlsplit(self.path).path
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
                if path == "/api/runs":
                    result = app.start_video(
                        str(body.get("url", "")),
                        str(body.get("api_key", "")),
                        str(body.get("judge_difficulty", "standard")),
                    )
                    self._send_json(result, HTTPStatus.ACCEPTED)
                    return
                if path == "/api/topics":
                    result = app.create_topic(
                        str(body.get("topic", "")),
                        mode=str(body.get("mode", "normal")),
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
                    if suffix.endswith("/select"):
                        run_id = suffix.removesuffix("/select").strip("/")
                        candidate_ids = body.get("candidate_ids", [])
                        if not isinstance(candidate_ids, list):
                            raise ValueError("candidate_ids 必须是数组")
                        result = app.select_topic(run_id, [str(value) for value in candidate_ids])
                        self._send_json(result, HTTPStatus.OK)
                        return
                if path == "/api/mvp-check":
                    self._send_json(app.run_mvp_check(str(body.get("api_key", ""))))
                    return
                if path == "/api/models":
                    self._send_json(app.list_models(str(body.get("api_key", ""))))
                    return
            except RuntimeError as exc:
                self._send_error_json(HTTPStatus.CONFLICT, str(exc))
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
            data = files("skill_gather.web_assets").joinpath(name).read_bytes()
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
