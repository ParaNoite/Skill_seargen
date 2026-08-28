"""Constrained OpenAI Agents SDK runner for the TED browser-game subagent."""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents import Agent, OpenAIChatCompletionsModel, Runner, function_tool, set_tracing_disabled
from openai import AsyncOpenAI

from .config import load_config
from .integrations.newapi import NewApiClient


DEFAULT_MODEL = "claude-haiku-4-5-20251001"
GAME_ROOT = Path("frontend/ted-games")
GAME_PREFIXES = ("baseline-2d/", "skill-3d/")
SKILL_PATHS = (
    Path(".agent-lab/key-skill-runs/topic-Three.js-3D-runner-touch-resize-game-over-restart-collision-technical-d8521328/topic_package/SKILL.md"),
    Path("catalog/packages/browser-game-prototype/SKILL.md"),
    Path("catalog/packages/game-loop-design/SKILL.md"),
)


class GameHatchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GameHatchResult:
    model: str
    final_output: str
    report_path: Path


def _repo_path(relative_path: str | Path) -> Path:
    return Path.cwd() / relative_path


def _game_file(relative_path: str) -> Path:
    normalized = relative_path.replace("\\", "/").lstrip("/")
    if not normalized.startswith(GAME_PREFIXES):
        raise GameHatchError("只能访问 baseline-2d 或 skill-3d 目录")
    path = (_repo_path(GAME_ROOT) / normalized).resolve()
    root = _repo_path(GAME_ROOT).resolve()
    if root not in path.parents:
        raise GameHatchError("游戏文件路径越界")
    return path


def _skill_bundle() -> str:
    parts: list[str] = []
    for relative_path in SKILL_PATHS:
        path = _repo_path(relative_path)
        if not path.is_file():
            raise GameHatchError(f"缺少已批准 Skill: {relative_path}")
        parts.append(f"\n--- {relative_path.as_posix()} ---\n{path.read_text(encoding='utf-8')}")
    return "".join(parts)


def _run_checks() -> str:
    completed = subprocess.run(
        ["node", "--test", "frontend/ted-games/tests/games.test.mjs"],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    output = (completed.stdout + completed.stderr).strip()
    return json.dumps(
        {"exit_code": completed.returncode, "output": output[-6000:]},
        ensure_ascii=False,
    )


def _instructions() -> str:
    return """你是 TED 开场浏览器游戏的受限孵化子 Agent。
目标是维护两款已指定的作品：baseline-2d 与 skill-3d；不得创建地牢或替换开场游戏。
先读取必要游戏文件和 Skill，再最小化修改。每次修改后必须调用 run_game_checks。
不得访问目录外的文件，不得执行任意 shell 命令，不得输出、请求或写入 API key。
最终必须用中文说明：修改的文件、测试结果、未解决风险。

已批准的 Skill 内容：
""" + _skill_bundle()


def _tools() -> list[Any]:
    @function_tool
    def read_game_file(path: str) -> str:
        """读取一个批准的 TED 游戏文件。path 必须以 baseline-2d/ 或 skill-3d/ 开头。"""
        file_path = _game_file(path)
        if not file_path.is_file():
            return "文件不存在"
        return file_path.read_text(encoding="utf-8")[:20000]

    @function_tool
    def write_game_file(path: str, content: str) -> str:
        """写入一个批准的 TED 游戏 HTML、CSS 或 JavaScript 文件。"""
        file_path = _game_file(path)
        if file_path.suffix not in {".html", ".css", ".js"}:
            return "拒绝：只能写入 .html、.css 或 .js 游戏文件"
        file_path.write_text(content, encoding="utf-8")
        return f"已写入 {path}"

    @function_tool
    def run_game_checks() -> str:
        """运行固定的 TED 游戏 Node 验收测试，并返回退出码和末尾输出。"""
        return _run_checks()

    return [read_game_file, write_game_file, run_game_checks]


def run_game_hatch(
    task: str,
    *,
    config_path: str | Path = "configs/skill-gather.example.json",
    model: str = DEFAULT_MODEL,
    report_dir: str | Path = ".agent-lab/game-hatch",
) -> GameHatchResult:
    config = load_config(config_path)
    client_config = NewApiClient.from_config(config.newapi)
    if client_config is None:
        raise GameHatchError(f"未设置 API key 环境变量: {config.newapi.api_key_env}")

    set_tracing_disabled(True)
    model_client = AsyncOpenAI(api_key=client_config.api_key, base_url=config.newapi.base_url)
    agent = Agent(
        name="ted-game-hatch-evaluator",
        instructions=_instructions(),
        model=OpenAIChatCompletionsModel(model=model, openai_client=model_client),
        tools=_tools(),
    )
    result = asyncio.run(Runner.run(agent, task, max_turns=12))
    report_root = _repo_path(report_dir)
    report_root.mkdir(parents=True, exist_ok=True)
    report_path = report_root / "latest.json"
    report_path.write_text(
        json.dumps(
            {"model": model, "task": task, "final_output": str(result.final_output)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return GameHatchResult(model=model, final_output=str(result.final_output), report_path=report_path)
