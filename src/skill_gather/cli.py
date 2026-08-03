from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from .adapters.bilibili import build_initial_manifest
from .config import ConfigError, load_config
from .evaluation import HUMAN_LABEL_STATUSES, build_quality_report
from .models import EvidenceTimeline, SkillPackageMetadata, VideoSourceManifest
from .mvp_check import run_mvp_check
from .pipeline import PipelineConfigurationError, run_video_pipeline
from .reports import build_reliability_report, build_vision_report
from .runs import RunStore, read_json, write_json
from .source import SourceInferenceError, infer_source


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
        choices=["lenient", "standard", "strict", "off"],
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
        server = create_server(
            host=args.host,
            port=args.port,
            config=args.config,
            runs=args.runs,
            out=args.out,
        )
    except (ConfigError, FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        print(str(exc), file=stderr)
        return 2

    print(f"Video Skill Gather: http://{args.host}:{server.server_port}", file=stdout)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def handle_mvp_check(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    try:
        config = load_config(args.config)
    except (ConfigError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc), file=stderr)
        return 2

    payload = run_mvp_check(config)
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=stdout)
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
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=stdout)
    return exit_code


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
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=stdout)
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
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=stdout)
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
    print(json.dumps({**payload, "path": str(path)}, ensure_ascii=False, indent=2), file=stdout)
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
    print(json.dumps(report, ensure_ascii=False, indent=2), file=stdout)
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
    print(json.dumps(report, ensure_ascii=False, indent=2), file=stdout)
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
    print(json.dumps(report, ensure_ascii=False, indent=2), file=stdout)
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
