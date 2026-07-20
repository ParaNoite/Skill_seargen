from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from .config import ConfigError, load_config
from .models import SkillPackageMetadata, VideoSourceManifest
from .runs import RunStore, read_json
from .source import SourceInferenceError, infer_source


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skill-gather")
    subcommands = parser.add_subparsers(dest="command", required=True)

    video = subcommands.add_parser("video", help="创建或恢复一个 B站视频处理 run")
    video.add_argument("url")
    video.add_argument("--config", required=True, help="配置文件路径")
    video.add_argument("--out", default="./skills", help="候选 skill 输出目录")
    video.add_argument("--runs", default="./runs", help="run 状态目录")
    video.set_defaults(handler=handle_video)

    score = subcommands.add_parser("score", help="读取 skill 包评分")
    score.add_argument("skill_dir")
    score.set_defaults(handler=handle_score)

    inspect = subcommands.add_parser("inspect", help="查看 run 的人工复核摘要")
    inspect.add_argument("run_id")
    inspect.add_argument("--runs", default="./runs", help="run 状态目录")
    inspect.set_defaults(handler=handle_inspect)

    return parser


def handle_video(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    try:
        load_config(args.config)
        source = infer_source(args.url)
    except (ConfigError, FileNotFoundError, json.JSONDecodeError, SourceInferenceError) as exc:
        print(str(exc), file=stderr)
        return 2

    store = RunStore(args.runs)
    state = store.start_or_resume(source.source, source.source_id)
    manifest_path = store.manifest_path(state.run_id)
    if not manifest_path.exists():
        manifest = VideoSourceManifest(
            source=source.source,
            source_id=source.source_id,
            url=args.url,
            title="",
            author="",
            duration_sec=0,
            subtitle_available=False,
            media_access="public",
            risk_flags=["metadata_pending"],
        )
        manifest_path = store.save_manifest(state.run_id, manifest)
    if state.status == "created":
        state.status = "running"
    state.artifacts["manifest"] = str(manifest_path)
    store.save(state)

    payload = {
        "run_id": state.run_id,
        "source": source.source,
        "source_id": source.source_id,
        "status": state.status,
        "current_stage": state.current_stage,
        "out": args.out,
        "message": "项目已初始化到可恢复 run；已写入基础 manifest。",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=stdout)
    return 0


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

    manifest_path = store.manifest_path(state.run_id)
    if manifest_path.exists():
        manifest = VideoSourceManifest.from_dict(read_json(manifest_path))
        print(
            f"manifest: {manifest.source} {manifest.source_id} {manifest.url}",
            file=stdout,
        )
        if manifest.risk_flags:
            print(f"manifest 风险: {', '.join(manifest.risk_flags)}", file=stdout)
    else:
        print("manifest: 尚未生成", file=stdout)

    if store.evidence_timeline_path(state.run_id).exists():
        print("证据摘要: 已生成 EvidenceTimeline", file=stdout)
    else:
        print("证据摘要: 尚未生成 EvidenceTimeline", file=stdout)

    print("评分: 尚未生成", file=stdout)
    return 0


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
