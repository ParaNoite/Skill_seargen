from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import RunState


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z._-]+", "-", value).strip("-")
    return slug or "unknown"


class RunStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def run_path(self, run_id: str) -> Path:
        return self.root / safe_slug(run_id)

    def state_path(self, run_id: str) -> Path:
        return self.run_path(run_id) / "run_state.json"

    def start_or_resume(self, source: str, source_id: str) -> RunState:
        run_id = f"{source}-{safe_slug(source_id)}"
        path = self.state_path(run_id)
        if path.exists():
            return RunState.from_dict(read_json(path))

        state = RunState(run_id=run_id, source_id=source_id)
        self.save(state)
        return state

    def save(self, state: RunState) -> None:
        path = self.state_path(state.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, state.to_dict())

    def load(self, run_id: str) -> RunState:
        path = self.state_path(run_id)
        if not path.exists():
            raise FileNotFoundError(f"找不到 run：{run_id}")
        return RunState.from_dict(read_json(path))


def read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: str | Path, value: dict[str, Any]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="\n") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write("\n")
