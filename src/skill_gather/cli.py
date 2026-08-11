from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from .adapters.bilibili import build_initial_manifest
from .automation import persist_release_gate
from .acceptance import run_offline_acceptance
from .config import ConfigError, load_config
from .evaluation import HUMAN_LABEL_STATUSES, build_quality_report
from .github_processing import process_github_sources
from .models import (
    EvidenceTimeline,
    JUDGE_DIFFICULTIES,
    SkillPackageMetadata,
    TopicBudget,
    TopicCachePolicy,
    VideoSourceManifest,
)
from .mvp_check import run_mvp_check
from .pipeline import PipelineConfigurationError, run_video_pipeline
from .reports import build_reliability_report, build_vision_report
from .runs import RunStore, read_json, write_json
from .search import SearchProviderError, search_topic
from .source import SourceInferenceError, infer_source
from .topic_processing import process_web_sources
from .distillers.technical_skill import apply_topic_human_review, generate_technical_skill, rescore_technical_package
from .integrations.newapi import NewApiClient
from .pipeline.topic_fusion import fuse_topic_evidence, write_fusion_artifacts
from .topic_videos import process_topic_videos
from .topics import TopicRunStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skill-gather")
    subcommands = parser.add_subparsers(dest="command", required=True)

    video = subcommands.add_parser("video", help="创建或恢复一个 B站视频处理 run")
    video.add_argument("url")
    video.add_argument("--config", required=True, help="配置文件路径")
    video.add_argument("--out", default="./skills", help="候选 skill 输出目录")
    video.add_argument("--runs", default="./runs", help="run 状态目录")
    video.add_argument(
        "--run-variant",
        default="",
        help="为同一视频创建独立实验 run，例如 sampled-12",
    )
    video.add_argument(
        "--judge-difficulty",
        choices=JUDGE_DIFFICULTIES,
        default="standard",
        help="judge 难度；off 表示跳过 LLM judge 并直接生成待复核候选包",
    )
    video.add_argument(
        "--vision-mode",
        choices=["full", "sampled", "off"],
        default="full",
        help="视觉实验模式：全帧、抽样或关闭远程视觉",
    )
    video.add_argument(
        "--vision-frame-limit",
        type=int,
        default=12,
        help="sampled 模式最多分析的帧数",
    )
    video.set_defaults(handler=handle_video)

    topic = subcommands.add_parser("topic", help="创建和查看主题研究任务")
    topic_commands = topic.add_subparsers(dest="topic_command", required=True)
    topic_create = topic_commands.add_parser("create", help="创建或恢复一个主题 run")
    topic_create.add_argument("topic", help="要研究的主题")
    topic_create.add_argument("--mode", choices=["normal", "technical"], default="normal")
    topic_create.add_argument("--output-language", default="zh-CN")
    topic_create.add_argument("--runs", default="./runs", help="主题 run 状态目录")
    topic_create.add_argument("--config", help="可选配置文件，用于读取主题预算默认值")
    topic_create.add_argument("--max-candidates", type=int)
    topic_create.add_argument("--max-selected-sources", type=int)
    topic_create.add_argument("--max-video-duration-sec", type=int)
    topic_create.add_argument("--max-model-calls", type=int)
    topic_create.add_argument("--max-estimated-cost-usd", type=float)
    topic_create.add_argument("--max-runtime-sec", type=int)
    topic_create.add_argument(
        "--judge-difficulty",
        choices=JUDGE_DIFFICULTIES,
    )
    cache_group = topic_create.add_mutually_exclusive_group()
    cache_group.add_argument("--reuse-cache", action="store_true", default=None)
    cache_group.add_argument("--no-reuse-cache", action="store_false", dest="reuse_cache")
    topic_create.add_argument("--refresh-cache", action="store_true", default=None)
    topic_create.set_defaults(handler=handle_topic_create)

    topic_inspect = topic_commands.add_parser("inspect", help="查看主题 run 的状态和主题包索引")
    topic_inspect.add_argument("run_id")
    topic_inspect.add_argument("--runs", default="./runs", help="主题 run 状态目录")
    topic_inspect.set_defaults(handler=handle_topic_inspect)

    topic_resume = topic_commands.add_parser("resume", help="恢复失败的主题 run")
    topic_resume.add_argument("run_id")
    topic_resume.add_argument("--runs", default="./runs", help="主题 run 状态目录")
    topic_resume.set_defaults(handler=handle_topic_resume)

    topic_rerun = topic_commands.add_parser("rerun", help="从指定阶段重跑主题任务")
    topic_rerun.add_argument("run_id")
    topic_rerun.add_argument("--runs", default="./runs")
    topic_rerun.add_argument("--stage", choices=["processing_sources", "generating", "scoring"], required=True)
    topic_rerun.set_defaults(handler=handle_topic_rerun)

    topic_review = topic_commands.add_parser("review", help="记录技术主题 skill 的人工复核")
    topic_review.add_argument("run_id")
    topic_review.add_argument("--runs", default="./runs")
    topic_review.add_argument("--label", choices=["usable", "needs_changes", "unusable"], required=True)
    topic_review.add_argument("--notes", default="")
    topic_review.set_defaults(handler=handle_topic_review)

    topic_search = topic_commands.add_parser("search", help="搜索主题候选来源")
    topic_search.add_argument("run_id")
    topic_search.add_argument("--runs", default="./runs", help="主题 run 状态目录")
    topic_search.add_argument("--config", default="config.json", help="配置文件路径")
    topic_search.add_argument("--fake", action="store_true", help="使用离线 fake 搜索 provider")
    topic_search.set_defaults(handler=handle_topic_search)

    topic_candidates = topic_commands.add_parser("candidates", help="列出主题候选来源")
    topic_candidates.add_argument("run_id")
    topic_candidates.add_argument("--runs", default="./runs", help="主题 run 状态目录")
    topic_candidates.set_defaults(handler=handle_topic_candidates)

    topic_select = topic_commands.add_parser("select", help="确认主题候选来源")
    topic_select.add_argument("run_id")
    topic_select.add_argument("candidate_ids", nargs="+", help="一个或多个候选 ID")
    topic_select.add_argument("--runs", default="./runs", help="主题 run 状态目录")
    topic_select.set_defaults(handler=handle_topic_select)

    topic_process = topic_commands.add_parser("process", help="处理已确认的网页、视频和技术模式 GitHub 来源")
    topic_process.add_argument("run_id")
    topic_process.add_argument("--runs", default="./runs", help="主题 run 状态目录")
    topic_process.add_argument("--timeout-sec", type=int, default=15, help="单个网页请求超时秒数")
    topic_process.add_argument("--config", default="config.json", help="已选视频时使用的配置文件路径")
    topic_process.add_argument("--vision-mode", choices=["full", "sampled", "off"], default="sampled")
    topic_process.add_argument("--vision-frame-limit", type=int, default=12)
    topic_process.set_defaults(handler=handle_topic_process)

    score = subcommands.add_parser("score", help="读取 skill 包评分")
    score.add_argument("skill_dir")
    score.set_defaults(handler=handle_score)

    inspect = subcommands.add_parser("inspect", help="查看 run 的人工复核摘要")
    inspect.add_argument("run_id")
    inspect.add_argument("--runs", default="./runs", help="run 状态目录")
    inspect.set_defaults(handler=handle_inspect)

    review = subcommands.add_parser("review", help="记录 run 的人工复核结果")
    review.add_argument("run_id")
    review.add_argument("--runs", default="./runs", help="run 状态目录")
    review.add_argument(
        "--label",
        required=True,
        choices=sorted(HUMAN_LABEL_STATUSES),
        help="人工标签：usable / needs_changes / unusable",
    )
    review.add_argument("--notes", default="", help="人工复核说明")
    review.set_defaults(handler=handle_review)

    calibrate = subcommands.add_parser("calibrate", help="比较规则、Judge 与人工标签")
    calibrate.add_argument("dataset", help="人工标注 JSON 数组路径")
    calibrate.set_defaults(handler=handle_calibrate)

    benchmark_report = subcommands.add_parser("benchmark-report", help="汇总冻结视频集的可靠性指标")
    benchmark_report.add_argument("benchmark", help="冻结视频集 JSON 路径")
    benchmark_report.add_argument("--runs", default="./runs", help="run 状态目录")
    benchmark_report.set_defaults(handler=handle_benchmark_report)

    vision_report = subcommands.add_parser("vision-report", help="比较视觉实验 run 的成本与字段正确率")
    vision_report.add_argument("run_dirs", nargs="+", help="两个或多个 run 目录")
    vision_report.add_argument("--expected-fields", help="预期命令/OCR 字段 JSON 数组")
    vision_report.set_defaults(handler=handle_vision_report)

    mvp_check = subcommands.add_parser("mvp-check", help="离线运行 v0.1 MVP 自检")
    mvp_check.add_argument(
        "--config",
        default="configs/skill-gather.example.json",
        help="配置文件路径",
    )
    mvp_check.set_defaults(handler=handle_mvp_check)

    acceptance = subcommands.add_parser("acceptance", help="运行 v1.0 固定 10 主题离线回归")
    acceptance.add_argument("--dataset", default="benchmarks/v1.0-topics.json")
    acceptance.add_argument("--config", default="configs/skill-gather.example.json")
    acceptance.add_argument("--runs", default="./runs/v1.0-offline-acceptance")
    acceptance.set_defaults(handler=handle_acceptance)

    model_check = subcommands.add_parser("model-check", help="探测配置模型的真实文本和视觉调用能力")
    model_check.add_argument("--config", default="config.json", help="配置文件路径")
    model_check.set_defaults(handler=handle_model_check)

    web = subcommands.add_parser("web", help="启动本地 Web 界面")
    web.add_argument("--config", default="config.json", help="配置文件路径")
    web.add_argument("--out", default="./skills", help="候选 skill 输出目录")
    web.add_argument("--runs", default="./runs", help="run 状态目录")
    web.add_argument("--host", default="127.0.0.1", help="监听地址")
    web.add_argument("--port", type=int, default=8765, help="监听端口")
    web.set_defaults(handler=handle_web)

    return parser


def handle_web(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    from .web import create_server

    try:
        load_config(args.config)
        server = _create_web_server(create_server, args)
    except (ConfigError, FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        print(str(exc), file=stderr)
        return 2

    print(f"skill_seargen: http://{args.host}:{server.server_port}", file=stdout)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _create_web_server(create_server: Any, args: argparse.Namespace) -> Any:
    kwargs = {"host": args.host, "config": args.config, "runs": args.runs, "out": args.out}
    try:
        return create_server(port=args.port, **kwargs)
    except OSError as exc:
        if args.port == 0 or getattr(exc, "winerror", None) not in {10013, 10048}:
            raise
        return create_server(port=0, **kwargs)


def handle_acceptance(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    try:
        report = run_offline_acceptance(
            args.dataset,
            config_path=args.config,
            runs_path=args.runs,
        )
    except (ConfigError, FileNotFoundError, json.JSONDecodeError, ValueError, OSError) as exc:
        print(str(exc), file=stderr)
        return 2
    _print_json(report, stdout)
    return 0 if report["status"] == "passed" else 1


def handle_model_check(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    try:
        config = load_config(args.config)
        client = NewApiClient.from_config(config.newapi)
        if client is None:
            raise ValueError("未找到 NewAPI API Key，无法执行真实模型探测")
        probes = [
            client.probe_model(config.newapi.vision_model, "vision"),
            client.probe_model(config.newapi.distiller_model, "text"),
            client.probe_model(config.newapi.judge_model, "text"),
        ]
    except (ConfigError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(str(exc), file=stderr)
        return 2
    _print_json({"probes": probes}, stdout)
    return 0 if all(bool(probe["available"]) for probe in probes) else 1


def handle_mvp_check(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    try:
        config = load_config(args.config)
    except (ConfigError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc), file=stderr)
        return 2

    payload = run_mvp_check(config)
    _print_json(payload, stdout)
    return 0 if payload.get("status") == "passed" else 1


def handle_video(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    try:
        config = load_config(args.config)
        source = infer_source(args.url)
    except (ConfigError, FileNotFoundError, json.JSONDecodeError, SourceInferenceError) as exc:
        print(str(exc), file=stderr)
        return 2

    store = RunStore(args.runs)
    state = store.start_or_resume(
        source.source,
        source.source_id,
        getattr(args, "run_variant", ""),
    )
    manifest_path = store.manifest_path(state.run_id)
    if not manifest_path.exists():
        manifest = build_initial_manifest(args.url, source)
        manifest_path = store.save_manifest(state.run_id, manifest)
    else:
        manifest = VideoSourceManifest.from_dict(read_json(manifest_path))

    try:
        state = run_video_pipeline(
            config=config,
            store=store,
            state=state,
            manifest=manifest,
            out_dir=args.out,
            judge_difficulty=getattr(args, "judge_difficulty", "standard"),
            vision_mode=getattr(args, "vision_mode", "full"),
            vision_frame_limit=getattr(args, "vision_frame_limit", 12),
        )
    except PipelineConfigurationError as exc:
        print(str(exc), file=stderr)
        return 2

    payload = {
        "run_id": state.run_id,
        "source": source.source,
        "source_id": source.source_id,
        "status": state.status,
        "current_stage": state.current_stage,
        "out": args.out,
        "message": _video_message(state),
    }
    if state.failure_reason:
        payload["failure_reason"] = state.failure_reason
    if state.artifacts.get("package"):
        payload["package"] = state.artifacts["package"]
    exit_code = 1 if state.status == "failed" else 0
    cli_result_path = store.run_path(state.run_id) / "cli_result.json"
    write_json(
        cli_result_path,
        {
            "command": "video",
            "status": state.status,
            "exit_code": exit_code,
            "recorded_at": datetime.now(UTC).isoformat(),
        },
    )
    state.artifacts["cli_result"] = str(cli_result_path)
    store.save(state)
    _print_json(payload, stdout)
    return exit_code


def handle_topic_create(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    try:
        budget = TopicBudget()
        cache = TopicCachePolicy()
        judge_difficulty = "standard"
        if args.config:
            defaults = load_config(args.config).topic_defaults
            budget = defaults.budget
            cache = defaults.cache
            judge_difficulty = defaults.judge_difficulty

        budget_values = budget.to_dict()
        for name in budget_values:
            value = getattr(args, name)
            if value is not None:
                budget_values[name] = value
        cache_values = cache.to_dict()
        if args.reuse_cache is not None:
            cache_values["reuse_cache"] = args.reuse_cache
        if args.refresh_cache is not None:
            cache_values["refresh_cache"] = args.refresh_cache
        if args.judge_difficulty:
            judge_difficulty = args.judge_difficulty

        store = TopicRunStore(args.runs)
        task = store.start_or_resume(
            topic=args.topic,
            mode=args.mode,
            output_language=args.output_language,
            budget=TopicBudget.from_dict(budget_values),
            cache=TopicCachePolicy.from_dict(cache_values),
            judge_difficulty=judge_difficulty,
        )
    except (ConfigError, FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(str(exc), file=stderr)
        return 2

    _print_json(task.to_dict(), stdout)
    return 0


def handle_topic_inspect(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    try:
        task = TopicRunStore(args.runs).load(args.run_id)
    except FileNotFoundError as exc:
        print(str(exc), file=stderr)
        return 1

    _print_json(task.to_dict(), stdout)
    return 0


def handle_topic_resume(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    try:
        task = TopicRunStore(args.runs).resume(args.run_id)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=stderr)
        return 1

    _print_json(task.to_dict(), stdout)
    return 0


def handle_topic_rerun(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    try:
        task = TopicRunStore(args.runs).rerun(args.run_id, args.stage)
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=stderr)
        return 2
    _print_json(task.to_dict(), stdout)
    return 0


def handle_topic_review(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    store = TopicRunStore(args.runs)
    try:
        task = store.load(args.run_id)
        package = task.package
        if package is None:
            raise ValueError("主题任务缺少主题包索引")
        score_path = store.run_path(args.run_id) / (package.score or f"{package.root}/score.json")
        score = apply_topic_human_review(score_path, label=args.label, notes=args.notes.strip())
        task.artifacts["score"] = score_path.relative_to(store.run_path(args.run_id)).as_posix()
        store.save(task)
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=stderr)
        return 2
    _print_json({"run_id": args.run_id, "score": score, "path": str(score_path)}, stdout)
    return 0


def handle_topic_search(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    store = TopicRunStore(args.runs)
    try:
        task = store.begin_search(args.run_id)
        config = load_config(args.config)
        candidates, batches, query_audit, intent = search_topic(
            task,
            config,
            cache_path=Path(args.runs) / "search_cache.sqlite3",
            use_fake=args.fake,
        )
        warnings = [
            f"{batch.provider}: {warning}"
            for batch in batches
            for warning in batch.warnings
        ]
        task = store.save_search_results(
            args.run_id,
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
    except (ConfigError, FileNotFoundError, json.JSONDecodeError, ValueError, SearchProviderError) as exc:
        try:
            store.fail(args.run_id, str(exc))
        except (FileNotFoundError, ValueError):
            pass
        print(str(exc), file=stderr)
        return 2
    _print_json(task.to_dict(), stdout)
    return 0 if task.status == "awaiting_selection" else 1


def handle_topic_candidates(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    try:
        task = TopicRunStore(args.runs).load(args.run_id)
    except FileNotFoundError as exc:
        print(str(exc), file=stderr)
        return 1
    payload = {
        "run_id": task.run_id,
        "topic": task.topic,
        "status": task.status,
        "candidates": [candidate.to_dict() for candidate in task.candidates],
        "selected_sources": [candidate.to_dict() for candidate in task.selected_sources],
    }
    _print_json(payload, stdout)
    return 0


def handle_topic_select(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    try:
        task = TopicRunStore(args.runs).select_candidates(args.run_id, args.candidate_ids)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=stderr)
        return 2
    _print_json(task.to_dict(), stdout)
    return 0


def handle_topic_process(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    store = TopicRunStore(args.runs)
    task = None
    try:
        if args.timeout_sec <= 0 or args.vision_frame_limit <= 0:
            raise ValueError("网页抓取超时和视觉帧数上限必须是正整数")
        task = store.load(args.run_id)
        if task.status not in {"processing_sources", "generating", "scoring"}:
            raise ValueError("主题任务必须处于 processing_sources、generating 或 scoring 阶段")
        run_root = store.run_path(args.run_id)
        if task.status in {"generating", "scoring"}:
            fusion_path = run_root / (task.package.fusion if task.package and task.package.fusion else "topic_package/fusion.json")
            fusion = read_json(fusion_path)
            task = _complete_topic_generation(store, task, run_root, fusion)
            _print_json({"run_id": task.run_id, "status": task.status, "skill": task.artifacts.get("skill"), "score": task.artifacts.get("score")}, stdout)
            return 0
        web_result = process_web_sources(task, run_root, timeout_sec=args.timeout_sec)
        if _topic_processing_paused(args, store, task.run_id, stdout):
            return 0
        selected_videos = [candidate for candidate in task.selected_sources if candidate.source_type == "video"]
        selected_github = [
            candidate
            for candidate in task.selected_sources
            if task.mode == "technical" and candidate.source_type == "github"
        ]
        video_result = None
        github_result = None
        if selected_videos:
            video_result = process_topic_videos(
                task,
                run_root,
                load_config(args.config),
                vision_mode=args.vision_mode,
                vision_frame_limit=args.vision_frame_limit,
            )
            if _topic_processing_paused(args, store, task.run_id, stdout):
                return 0
            processed_video_ids = {candidate.candidate_id for candidate in selected_videos}
            web_result.skipped = [
                item for item in web_result.skipped if item.get("candidate_id") not in processed_video_ids
            ]
        if selected_github:
            github_token_env = "GITHUB_TOKEN"
            config_path = Path(args.config)
            if config_path.exists():
                github_token_env = load_config(args.config).search.github_token_env
            github_result = process_github_sources(
                task,
                run_root,
                timeout_sec=args.timeout_sec,
                token_env=github_token_env,
            )
            if _topic_processing_paused(args, store, task.run_id, stdout):
                return 0
            processed_github_ids = {candidate.candidate_id for candidate in selected_github}
            web_result.skipped = [
                item for item in web_result.skipped if item.get("candidate_id") not in processed_github_ids
            ]
        _save_processed_topic_sources(store, task)
        task.artifacts["web_processing_audit"] = "web_processing_audit.json"
        task.artifacts["web_evidence"] = task.package.evidence if task.package else ""
        if video_result is not None:
            task.artifacts["video_processing_audit"] = "video_processing_audit.json"
            task.artifacts["video_runs"] = "video_runs"
        if github_result is not None:
            task.artifacts["github_processing_audit"] = "github_processing_audit.json"
            task.artifacts["github_evidence"] = task.package.evidence if task.package else ""
        store.save(task)
        task = store.record_usage(args.run_id, task.usage)
        successful_video_count = len(video_result.successful) if video_result is not None else 0
        successful_github_count = len(github_result.evidence) if github_result is not None else 0
        if task.status == "failed":
            pass
        elif not web_result.evidence and not successful_video_count and not successful_github_count:
            failure_details = _unique(
                [
                    str(item.get("reason", ""))
                    for item in (web_result.failures + (github_result.failures if github_result is not None else []))
                    if item.get("reason")
                ]
                + [
                    str(entry.failure_reason)
                    for entry in (video_result.failed if video_result is not None else [])
                    if entry.failure_reason
                ]
            )
            reason = "没有成功提取可用的网页正文、视频证据或 GitHub 证据；请检查处理审计文件"
            if failure_details:
                reason += "。失败原因：" + "；".join(failure_details[:3])
            task = store.fail(args.run_id, reason)
        else:
            store.advance(args.run_id, "generating")
            fusion = fuse_topic_evidence(task, run_root)
            fusion_path, knowledge_path = write_fusion_artifacts(task, run_root, fusion)
            task = store.load(args.run_id)
            task.package.knowledge = knowledge_path.relative_to(run_root).as_posix() if task.package else None
            task.package.fusion = fusion_path.relative_to(run_root).as_posix() if task.package else None
            task.artifacts["knowledge"] = knowledge_path.relative_to(run_root).as_posix()
            task.artifacts["fusion"] = fusion_path.relative_to(run_root).as_posix()
            store.save(task)
            task = _complete_topic_generation(store, task, run_root, fusion)
    except (ConfigError, FileNotFoundError, json.JSONDecodeError, ValueError, OSError) as exc:
        if task is not None and task.status not in {"completed", "failed"}:
            try:
                task = store.fail(args.run_id, str(exc))
            except (FileNotFoundError, ValueError):
                pass
        print(str(exc), file=stderr)
        return 2

    payload = {
        "run_id": task.run_id,
        "status": task.status,
        "current_stage": task.current_stage,
        "successful_web_sources": len(web_result.evidence),
        "failed_web_sources": len(web_result.failures),
        "successful_video_sources": len(video_result.successful) if video_result is not None else 0,
        "failed_video_sources": len(video_result.failed) if video_result is not None else 0,
        "successful_github_sources": len(github_result.evidence) if github_result is not None else 0,
        "failed_github_sources": len(github_result.failures) if github_result is not None else 0,
        "skipped_sources": (
            len(web_result.skipped)
            + (len(video_result.skipped) if video_result is not None else 0)
            + (len(github_result.skipped) if github_result is not None else 0)
        ),
        "knowledge": (
            task.artifacts.get("knowledge")
            if task.package and task.package.knowledge and (run_root / task.package.knowledge).exists()
            else None
        ),
        "fusion": task.artifacts.get("fusion"),
        "skill": task.artifacts.get("skill"),
        "score": task.artifacts.get("score"),
    }
    if task.failure_reason:
        payload["failure_reason"] = task.failure_reason
    _print_json(payload, stdout)
    return 0 if task.status == "completed" else 1


def _topic_processing_paused(args: argparse.Namespace, store: TopicRunStore, run_id: str, stdout: TextIO) -> bool:
    event = getattr(args, "cancel_event", None)
    if event is None or not event.is_set():
        return False
    task = store.load(run_id)
    if task.status != "paused":
        task = store.pause(run_id)
    _print_json({"run_id": run_id, "status": "paused", "current_stage": task.current_stage}, stdout)
    return True


def _complete_topic_generation(store: TopicRunStore, task: Any, run_root: Path, fusion: dict[str, Any]) -> Any:
    if task.mode == "technical":
        if task.package is None:
            raise ValueError("主题任务缺少主题包索引")
        if task.status == "generating":
            skill_path, score_path, score = generate_technical_skill(task, run_root, fusion)
            task = store.load(task.run_id)
            if task.package is None:
                raise ValueError("主题任务缺少主题包索引")
            task.package.skill = skill_path.relative_to(run_root).as_posix()
        else:
            score_path, score = rescore_technical_package(task, run_root, fusion)
        task.package.score = score_path.relative_to(run_root).as_posix()
        task.artifacts.update({"skill": task.package.skill or "", "score": task.package.score})
        persist_release_gate(task, run_root, fusion, score)
        store.save(task)
    elif task.execution_mode == "auto":
        persist_release_gate(task, run_root, fusion, {})
        store.save(task)
    if task.status == "generating":
        task = store.advance(task.run_id, "scoring")
    return store.advance(task.run_id, "completed")


def _save_processed_topic_sources(store: TopicRunStore, task: Any) -> None:
    package = task.package
    if package is None:
        raise ValueError("主题任务缺少主题包索引")
    path = store.run_path(task.run_id) / package.sources
    previous = read_json(path) if path.exists() else {}
    write_json(
        path,
        {
            "topic": task.topic,
            "candidates": [candidate.to_dict() for candidate in task.candidates],
            "selected_sources": [candidate.to_dict() for candidate in task.selected_sources],
            "warnings": previous.get("warnings", []),
        },
    )


def _video_message(state: Any) -> str:
    if state.status == "completed":
        if state.artifacts.get("package"):
            return "已生成候选 skill 包；请用 inspect 复核证据、风险与评分。"
        return "视频处理已完成；请用 inspect 复核 run 摘要。"
    if state.status == "failed":
        if state.artifacts.get("package"):
            return "处理未达到候选阈值，已生成失败审计包；请用 inspect 复核原因。"
        return "处理失败；请用 inspect 查看失败原因和已保留产物。"
    return "视频处理已启动或恢复；请用 inspect 查看当前进度。"


def handle_score(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    metadata_path = Path(args.skill_dir) / "metadata.json"
    if not metadata_path.exists():
        payload = {
            "final_status": "failed",
            "reason": "缺少 metadata.json，无法评分。",
        }
        _print_json(payload, stdout)
        return 1

    try:
        metadata = SkillPackageMetadata.from_dict(read_json(metadata_path))
    except json.JSONDecodeError as exc:
        print(f"metadata.json 不是合法 JSON：{exc}", file=stderr)
        return 2

    payload = {
        "skill_dir": str(args.skill_dir),
        "package_status": metadata.package_status,
        "scores": metadata.scores.to_dict(),
        "risk_flags": metadata.risk_flags,
    }
    _print_json(payload, stdout)
    return 0


def handle_inspect(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    store = RunStore(args.runs)
    try:
        state = store.load(args.run_id)
    except FileNotFoundError as exc:
        print(str(exc), file=stderr)
        return 1

    print(f"Run: {state.run_id}", file=stdout)
    print(f"状态: {state.status}", file=stdout)
    print(f"当前阶段: {state.current_stage}", file=stdout)
    print(f"已完成阶段: {', '.join(state.completed_stages) or '无'}", file=stdout)
    if state.failure_reason:
        print(f"失败原因: {state.failure_reason}", file=stdout)

    risk_flags: list[str] = []
    manifest_path = store.manifest_path(state.run_id)
    if manifest_path.exists():
        manifest = VideoSourceManifest.from_dict(read_json(manifest_path))
        print(
            f"manifest: {manifest.source} {manifest.source_id} {manifest.url}",
            file=stdout,
        )
        if manifest.risk_flags:
            risk_flags.extend(manifest.risk_flags)
            print(f"manifest 风险: {', '.join(manifest.risk_flags)}", file=stdout)
    else:
        print("manifest: 尚未生成", file=stdout)

    metadata_path = store.run_path(state.run_id) / "metadata.json"
    metadata: SkillPackageMetadata | None = None
    if metadata_path.exists():
        metadata = SkillPackageMetadata.from_dict(read_json(metadata_path))
        risk_flags.extend(metadata.risk_flags)

    _print_evidence_summary(store, state.run_id, stdout)
    unique_risks = _unique(risk_flags)
    print(f"风险: {', '.join(unique_risks) if unique_risks else '无'}", file=stdout)
    _print_score_summary(store, state.run_id, metadata, stdout)
    _print_human_review(store, state.run_id, stdout)
    _print_artifacts(state.artifacts, stdout)
    return 0


def handle_review(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    store = RunStore(args.runs)
    try:
        store.load(args.run_id)
    except FileNotFoundError as exc:
        print(str(exc), file=stderr)
        return 1

    payload = {
        "run_id": args.run_id,
        "label": args.label,
        "expected_status": HUMAN_LABEL_STATUSES[args.label],
        "notes": str(args.notes).strip(),
        "reviewed_at": datetime.now(UTC).isoformat(),
    }
    path = store.run_path(args.run_id) / "human_review.json"
    write_json(path, payload)
    _print_json({**payload, "path": str(path)}, stdout)
    return 0


def handle_calibrate(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    try:
        raw = read_json(args.dataset)
        if not isinstance(raw, list):
            raise ValueError("标注集必须是 JSON 数组。")
        report = build_quality_report(raw)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(str(exc), file=stderr)
        return 2
    _print_json(report, stdout)
    return 0


def handle_benchmark_report(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    try:
        benchmark = read_json(args.benchmark)
        if not isinstance(benchmark, dict):
            raise ValueError("冻结视频集必须是 JSON 对象。")
        report = build_reliability_report(benchmark, args.runs)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(str(exc), file=stderr)
        return 2
    _print_json(report, stdout)
    return 0


def handle_vision_report(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    try:
        expected_fields: list[str] = []
        if args.expected_fields:
            raw = read_json(args.expected_fields)
            if not isinstance(raw, list):
                raise ValueError("expected-fields 必须是 JSON 数组。")
            expected_fields = [str(value) for value in raw]
        report = build_vision_report(args.run_dirs, expected_fields)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(str(exc), file=stderr)
        return 2
    _print_json(report, stdout)
    return 0


def _print_human_review(store: RunStore, run_id: str, stdout: TextIO) -> None:
    review = _read_optional_json(store.run_path(run_id) / "human_review.json")
    if not review:
        print("人工复核: 尚未记录", file=stdout)
        return
    text = f"人工复核: {review.get('label', 'unknown')}"
    notes = str(review.get("notes", "")).strip()
    if notes:
        text += f"; {_truncate(notes, max_length=120)}"
    print(text, file=stdout)


def _print_evidence_summary(store: RunStore, run_id: str, stdout: TextIO) -> None:
    path = store.evidence_timeline_path(run_id)
    if not path.exists():
        print("证据摘要: 尚未生成 EvidenceTimeline", file=stdout)
        return

    timeline = EvidenceTimeline.from_dict(read_json(path))
    print(
        "证据摘要: "
        f"{len(timeline.items)} 条 EvidenceTimeline item；"
        f"frame_budget={timeline.frame_budget}；"
        f"strategy={timeline.sampling_strategy}",
        file=stdout,
    )
    if not timeline.items:
        print("  - 无可展示证据", file=stdout)
        return

    max_items = 5
    for item in timeline.items[:max_items]:
        confidence = f"{item.confidence:.2f}"
        print(
            f"  - {item.timestamp} [{item.type}] {_truncate(item.claim)} "
            f"(confidence={confidence})",
            file=stdout,
        )
    remaining = len(timeline.items) - max_items
    if remaining > 0:
        print(f"  - ... 还有 {remaining} 条证据未展示", file=stdout)


def _print_score_summary(
    store: RunStore,
    run_id: str,
    metadata: SkillPackageMetadata | None,
    stdout: TextIO,
) -> None:
    score_record = _read_optional_json(store.run_path(run_id) / "score.json")
    if metadata is not None:
        scores = metadata.scores
        print(f"评分: {scores.final_score} ({scores.final_status})", file=stdout)
        print(
            "评分细节: "
            f"rule={scores.rule_score}, "
            f"judge={scores.llm_judge_score}, "
            f"policy={scores.conflict_policy}",
            file=stdout,
        )
    elif score_record:
        print(
            f"评分: {score_record.get('final_score', 0)} "
            f"({score_record.get('final_status', 'failed')})",
            file=stdout,
        )
        print(
            "评分细节: "
            f"rule={score_record.get('rule_score', 0)}, "
            f"judge={score_record.get('llm_judge_score', 0)}, "
            f"policy={score_record.get('conflict_policy', 'conservative')}",
            file=stdout,
        )
    else:
        print("评分: 尚未生成", file=stdout)
        return

    judge = score_record.get("judge", {}) if isinstance(score_record, dict) else {}
    if isinstance(judge, dict) and judge:
        status = str(judge.get("status", "unknown"))
        score = judge.get("score")
        prefix = f"LLM judge: {status}"
        if score is not None:
            prefix += f" score={score}"
        rationale = str(judge.get("rationale") or judge.get("reason") or "").strip()
        if rationale:
            prefix += f"; {_truncate(rationale, max_length=120)}"
        print(prefix, file=stdout)
        judge_risks = judge.get("risk_flags", [])
        if isinstance(judge_risks, list) and judge_risks:
            print(f"judge 风险: {', '.join(str(flag) for flag in judge_risks)}", file=stdout)


def _print_artifacts(artifacts: dict[str, str], stdout: TextIO) -> None:
    if not artifacts:
        print("产物: 尚未记录", file=stdout)
        return

    print("产物:", file=stdout)
    preferred_order = [
        "manifest",
        "media_probe",
        "media_extract",
        "frame_extract",
        "frame_index",
        "asr",
        "vision_ocr",
        "evidence_timeline",
        "distillation",
        "score",
        "candidate_out_dir",
        "package",
    ]
    emitted: set[str] = set()
    for key in preferred_order:
        if key in artifacts:
            print(f"  - {key}: {artifacts[key]}", file=stdout)
            emitted.add(key)
    for key in sorted(set(artifacts) - emitted):
        print(f"  - {key}: {artifacts[key]}", file=stdout)


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = read_json(path)
    return value if isinstance(value, dict) else {}


def _print_json(value: Any, stdout: TextIO) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    encoding = getattr(stdout, "encoding", None)
    if encoding:
        try:
            text.encode(encoding)
        except (LookupError, UnicodeEncodeError):
            text = json.dumps(value, ensure_ascii=True, indent=2)
    print(text, file=stdout)


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _truncate(value: str, max_length: int = 80) -> str:
    clean = " ".join(value.split())
    if len(clean) <= max_length:
        return clean
    return clean[: max_length - 1].rstrip() + "…"


def main(
    argv: list[str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    return args.handler(args, out, err)
