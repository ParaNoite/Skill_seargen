from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

from .models import TopicSourceCandidate, TopicTask
from .runs import safe_slug, write_json


_ROOT_FILE_NAMES = {
    "readme",
    "readme.md",
    "skill.md",
    "agents.md",
    "claude.md",
    "package.json",
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "dockerfile",
}
_TEXT_EXTENSIONS = {".md", ".txt", ".py", ".js", ".ts", ".json", ".toml", ".yaml", ".yml", ".gd", ".cs"}
_NETWORK_RETRY_DELAYS = (0.25, 0.5)
_NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError)


@dataclass(slots=True)
class GitHubFile:
    path: str
    text: str
    html_url: str = ""


@dataclass(slots=True)
class GitHubRepositorySnapshot:
    repo: str
    html_url: str
    default_branch: str
    files: list[GitHubFile] = field(default_factory=list)
    truncated: bool = False
    fallback_reason: str = ""


@dataclass(slots=True)
class GitHubProcessingResult:
    evidence: list[dict[str, object]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


class GitHubProcessingError(ValueError):
    pass


def process_github_sources(
    task: TopicTask,
    run_root: Path,
    *,
    timeout_sec: int = 15,
    fetcher=None,
    token_env: str = "GITHUB_TOKEN",
) -> GitHubProcessingResult:
    package = task.package
    if package is None:
        raise ValueError("主题任务缺少主题包索引")
    evidence_dir = run_root / package.evidence
    references_dir = run_root / package.references
    evidence_dir.mkdir(parents=True, exist_ok=True)
    references_dir.mkdir(parents=True, exist_ok=True)

    result = GitHubProcessingResult()
    if fetcher is not None:
        fetch_repo = fetcher
    elif token_env == "GITHUB_TOKEN":
        fetch_repo = fetch_public_github_repository
    else:
        fetch_repo = lambda url, *, timeout_sec: fetch_public_github_repository(
            url,
            timeout_sec=timeout_sec,
            token_env=token_env,
        )

    for candidate in task.selected_sources:
        if candidate.source_type != "github":
            continue
        if task.mode != "technical":
            result.skipped.append(_item(candidate, "普通模式不处理 GitHub 来源；请使用 technical 模式"))
            continue
        try:
            snapshot = fetch_repo(candidate.canonical_url or candidate.url, timeout_sec=timeout_sec)
            record = build_github_evidence(task, candidate, snapshot, len(result.evidence) + 1)
            basename = _safe_component(candidate.candidate_id or snapshot.repo)
            write_json(evidence_dir / f"github-{basename}.json", record)
            (references_dir / f"github-{basename}.md").write_text(_render_reference_markdown(record), encoding="utf-8")
            candidate.quality_score = max(candidate.quality_score, int(record["quality_score"]))
            candidate.risk_flags = _unique(candidate.risk_flags + list(record["risk_flags"]))
            result.evidence.append(record)
        except GitHubProcessingError as exc:
            result.failures.append(_item(candidate, str(exc)))
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                reason = f"GitHub API 速率限制（HTTP 403）；请设置 {token_env} 后重试，或稍后再试"
            elif exc.code == 401:
                reason = f"GitHub token 无效（HTTP 401）；请检查 {token_env}"
            else:
                reason = f"GitHub 公开仓库读取失败：HTTP {exc.code}"
            result.failures.append(_item(candidate, reason))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            result.failures.append(_item(candidate, f"GitHub 公开仓库读取失败：{exc}"))

    write_json(
        run_root / "github_processing_audit.json",
        {
            "topic": task.topic,
            "processed_at": datetime.now(UTC).isoformat(),
            "successful_sources": [
                {
                    "source_id": record["source_id"],
                    "candidate_id": record["candidate_id"],
                    "repo": record["repo"],
                    "quality_score": record["quality_score"],
                    "confidence": record["confidence"],
                    "risk_flags": record["risk_flags"],
                }
                for record in result.evidence
            ],
            "failed_sources": result.failures,
            "skipped_sources": result.skipped,
        },
    )
    return result


def fetch_public_github_repository(
    url: str,
    *,
    timeout_sec: int = 15,
    max_files: int = 12,
    max_bytes: int = 24_000,
    token_env: str = "GITHUB_TOKEN",
) -> GitHubRepositorySnapshot:
    owner, repo = parse_github_repo(url)
    repo_api = f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}"
    token = os.getenv(token_env, "").strip()
    try:
        metadata = _get_json(repo_api, timeout_sec=timeout_sec, token=token)
    except urllib.error.HTTPError as exc:
        if exc.code == 403 and not token:
            return _fetch_public_readme_fallback(
                owner,
                repo,
                timeout_sec=timeout_sec,
                max_bytes=max_bytes,
                fallback_reason="github_api_rate_limit_fallback",
                original_error=exc,
            )
        raise
    if bool(metadata.get("private")):
        raise GitHubProcessingError("私有 GitHub 仓库不在 v0.7 范围内")
    default_branch = str(metadata.get("default_branch") or "main")
    full_name = str(metadata.get("full_name") or f"{owner}/{repo}")
    html_url = str(metadata.get("html_url") or f"https://github.com/{owner}/{repo}")
    try:
        tree_url = f"{repo_api}/git/trees/{quote(default_branch, safe='')}?recursive=1"
        tree_payload = _get_json(tree_url, timeout_sec=timeout_sec, token=token)
        tree = tree_payload.get("tree", []) if isinstance(tree_payload, dict) else []
        if not isinstance(tree, list):
            raise GitHubProcessingError("GitHub 仓库树响应格式无效")
        paths = select_interesting_paths([item for item in tree if isinstance(item, dict)], max_files=max_files)
        files: list[GitHubFile] = []
        for path in paths:
            contents_url = f"{repo_api}/contents/{quote(path)}?ref={quote(default_branch, safe='')}"
            text = _get_text(
                contents_url,
                timeout_sec=timeout_sec,
                max_bytes=max_bytes,
                accept="application/vnd.github.raw+json",
                token=token,
            )
            files.append(GitHubFile(path=path, text=text, html_url=f"{html_url}/blob/{default_branch}/{path}"))
    except _NETWORK_ERRORS as exc:
        return _fetch_public_readme_fallback(
            owner,
            repo,
            timeout_sec=timeout_sec,
            max_bytes=max_bytes,
            fallback_reason="github_network_readme_fallback",
            original_error=exc,
            preferred_branch=default_branch,
        )
    return GitHubRepositorySnapshot(
        repo=full_name,
        html_url=html_url,
        default_branch=default_branch,
        files=files,
        truncated=bool(tree_payload.get("truncated")) or len(paths) >= max_files,
    )


def _fetch_public_readme_fallback(
    owner: str,
    repo: str,
    *,
    timeout_sec: int,
    max_bytes: int,
    fallback_reason: str,
    original_error: BaseException,
    preferred_branch: str = "",
) -> GitHubRepositorySnapshot:
    html_url = f"https://github.com/{owner}/{repo}"
    branches = list(dict.fromkeys(branch for branch in (preferred_branch, "main", "master") if branch))
    for branch in branches:
        for path in ("README.md", "README", "readme.md", "Readme.md"):
            raw_url = f"https://raw.githubusercontent.com/{quote(owner)}/{quote(repo)}/{branch}/{path}"
            try:
                text = _get_text(raw_url, timeout_sec=timeout_sec, max_bytes=max_bytes)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    continue
                raise
            if text.strip():
                return GitHubRepositorySnapshot(
                    repo=f"{owner}/{repo}",
                    html_url=html_url,
                    default_branch=branch,
                    files=[GitHubFile(path=path, text=text, html_url=f"{html_url}/blob/{branch}/{path}")],
                    truncated=True,
                    fallback_reason=fallback_reason,
                )
    raise original_error


def parse_github_repo(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    if (parsed.hostname or "").lower() != "github.com":
        raise GitHubProcessingError("只支持 github.com 上的公开仓库 URL")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise GitHubProcessingError("GitHub URL 缺少 owner/repo")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        raise GitHubProcessingError("GitHub 仓库 owner 或 repo 格式无效")
    return owner, repo


def select_interesting_paths(tree: list[dict[str, object]], *, max_files: int) -> list[str]:
    candidates = [str(item.get("path") or "") for item in tree if item.get("type") == "blob"]
    candidates = [path for path in candidates if path and _is_interesting_path(path)]
    priority = {"readme": 0, "skill": 1, "docs": 2, "examples": 3, "config": 4, "entry": 5, "other": 6}
    candidates.sort(key=lambda path: (priority[_path_role(path)], path.count("/"), path.lower()))
    return candidates[:max_files]


def build_github_evidence(
    task: TopicTask,
    candidate: TopicSourceCandidate,
    snapshot: GitHubRepositorySnapshot,
    source_number: int,
) -> dict[str, object]:
    if not snapshot.files:
        raise GitHubProcessingError("未找到 README、docs、examples、配置或入口等可分析文本文件")

    findings: dict[str, list[dict[str, str]]] = {
        "installation": [],
        "commands": [],
        "api": [],
        "examples": [],
        "configuration": [],
        "constraints": [],
        "skill_materials": [],
    }
    file_summaries: list[dict[str, object]] = []
    for file in snapshot.files:
        signals = _signals_for_file(file.path, file.text)
        for key, snippets in signals.items():
            for snippet in snippets:
                findings[key].append({"path": file.path, "excerpt": snippet})
        file_summaries.append(
            {
                "path": file.path,
                "role": _path_role(file.path),
                "url": file.html_url,
                "excerpt": _excerpt(file.text, 260),
                "signals": {key: len(value) for key, value in signals.items() if value},
            }
        )

    risk_flags = list(candidate.risk_flags)
    if snapshot.truncated:
        risk_flags.append("github_file_selection_truncated")
    if snapshot.fallback_reason:
        risk_flags.append(snapshot.fallback_reason)
    if findings["skill_materials"]:
        risk_flags.append("github_existing_skill_material_downgraded")
    if not findings["installation"] and not findings["commands"]:
        risk_flags.append("github_usage_evidence_weak")

    quality_score = _score_github_evidence(candidate.quality_score, findings, snapshot.files)
    limitations = [
        "仅分析公开 GitHub 仓库中的少量文本文件，不 clone 仓库，不执行代码。",
        "已有 Codex / Anthropic skill 格式材料只作为参考降级处理，不直接等同于最终生成结果。",
    ]
    if snapshot.fallback_reason == "github_api_rate_limit_fallback":
        limitations.append("GitHub API 触发匿名速率限制，本次仅通过公开 raw 地址读取 README。")
    elif snapshot.fallback_reason == "github_network_readme_fallback":
        limitations.append("GitHub 仓库树或文件读取发生网络超时，本次降级为仅读取公开 README。")
    record = {
        "source_id": f"G{source_number}",
        "candidate_id": candidate.candidate_id,
        "source_type": "github",
        "topic": task.topic,
        "repo": snapshot.repo,
        "url": snapshot.html_url,
        "default_branch": snapshot.default_branch,
        "analyzed_at": datetime.now(UTC).isoformat(),
        "quality_score": quality_score,
        "confidence": round(quality_score / 100, 2),
        "risk_flags": _unique(risk_flags),
        "files": file_summaries,
        "findings": findings,
        "limitations": limitations,
    }
    return record


def _get_json(url: str, *, timeout_sec: int, token: str = "") -> dict[str, object]:
    text = _get_text(
        url,
        timeout_sec=timeout_sec,
        max_bytes=8_000_000,
        accept="application/vnd.github+json",
        token=token,
        truncate=False,
    )
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise GitHubProcessingError("GitHub 响应不是 JSON 对象")
    return payload


def _get_text(
    url: str,
    *,
    timeout_sec: int,
    max_bytes: int,
    accept: str = "text/plain, application/json",
    token: str = "",
    truncate: bool = True,
) -> str:
    headers = {"User-Agent": "skill-seargen/0.8", "Accept": accept}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(len(_NETWORK_RETRY_DELAYS) + 1):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                data = response.read(max_bytes + 1)
            break
        except urllib.error.HTTPError:
            raise
        except _NETWORK_ERRORS:
            if attempt >= len(_NETWORK_RETRY_DELAYS):
                raise
            time.sleep(_NETWORK_RETRY_DELAYS[attempt])
    if len(data) > max_bytes:
        if not truncate:
            raise GitHubProcessingError(f"GitHub API 响应超过 {max_bytes} 字节上限")
        data = data[:max_bytes]
    return data.decode(charset, errors="replace")


def _is_interesting_path(path: str) -> bool:
    lower = path.lower()
    name = lower.rsplit("/", maxsplit=1)[-1]
    suffix = Path(lower).suffix
    if name in _ROOT_FILE_NAMES:
        return True
    if lower.startswith(("docs/", "examples/", "example/", ".github/")) and suffix in _TEXT_EXTENSIONS:
        return True
    if lower.startswith(("src/", "app/", "skill_gather/")) and name in {"cli.py", "__main__.py", "main.py", "index.ts", "index.js"}:
        return True
    return False


def _path_role(path: str) -> str:
    lower = path.lower()
    name = lower.rsplit("/", maxsplit=1)[-1]
    if name.startswith("readme"):
        return "readme"
    if name in {"skill.md", "agents.md", "claude.md"} or "/skills/" in f"/{lower}/":
        return "skill"
    if lower.startswith("docs/"):
        return "docs"
    if lower.startswith(("examples/", "example/")):
        return "examples"
    if name in {"package.json", "pyproject.toml", "setup.py", "requirements.txt"} or "config" in name:
        return "config"
    if name in {"cli.py", "__main__.py", "main.py", "index.ts", "index.js"}:
        return "entry"
    return "other"


def _signals_for_file(path: str, text: str) -> dict[str, list[str]]:
    lower_path = path.lower()
    patterns = {
        "installation": ("install", "pip install", "npm install", "安装", "依赖"),
        "commands": ("python -m", "npm run", "cargo ", "godot", "命令", "```"),
        "api": ("api", "class ", "def ", "function ", "接口", "参数"),
        "examples": ("example", "examples", "示例", "用法", "usage"),
        "configuration": ("config", "配置", ".env", "settings", "选项"),
        "constraints": ("warning", "注意", "requires", "token", "cookie", "限制", "private"),
        "skill_materials": ("skill.md", "agents.md", "anthropic", "codex skill", "claude"),
    }
    signals: dict[str, list[str]] = {key: [] for key in patterns}
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    for key, needles in patterns.items():
        if key == "skill_materials" and any(name in lower_path for name in ("skill.md", "agents.md", "claude.md")):
            signals[key].append(f"文件路径显示已有 skill/Agent 材料：{path}")
        for line in lines:
            haystack = line.lower()
            if len(line) >= 6 and any(needle in haystack for needle in needles):
                signals[key].append(_excerpt(line, 180))
            if len(signals[key]) >= 3:
                break
    return signals


def _score_github_evidence(candidate_quality: int, findings: dict[str, list[dict[str, str]]], files: list[GitHubFile]) -> int:
    coverage = sum(12 for key in ("installation", "commands", "api", "examples", "configuration") if findings[key])
    readme_bonus = 12 if any(_path_role(file.path) == "readme" for file in files) else 0
    docs_bonus = 8 if any(_path_role(file.path) in {"docs", "examples"} for file in files) else 0
    return max(candidate_quality, min(100, 20 + coverage + readme_bonus + docs_bonus))


def _render_reference_markdown(record: dict[str, object]) -> str:
    lines = [
        f"# {record['repo']}",
        "",
        f"- 来源：{record['url']}",
        f"- 分支：{record['default_branch']}",
        f"- 质量分：{record['quality_score']}",
        "",
        "## 已分析文件",
        "",
    ]
    for file in record["files"]:
        lines.append(f"- `{file['path']}`（{file['role']}）：{file['excerpt']}")
    lines.extend(["", "## 关键信号", ""])
    labels = {
        "installation": "安装",
        "commands": "命令",
        "api": "API / 入口",
        "examples": "示例",
        "configuration": "配置",
        "constraints": "约束",
        "skill_materials": "已有 skill 材料",
    }
    findings = record["findings"]
    for key, label in labels.items():
        lines.append(f"### {label}")
        items = findings.get(key, [])
        if items:
            lines.extend(f"- `{item['path']}`：{item['excerpt']}" for item in items[:5])
        else:
            lines.append("- 未在轻量分析范围内识别到明确材料。")
        lines.append("")
    lines.append("## 限制")
    lines.extend(f"- {item}" for item in record["limitations"])
    return "\n".join(lines).rstrip() + "\n"


def _item(candidate: TopicSourceCandidate, reason: str) -> dict[str, str]:
    return {"candidate_id": candidate.candidate_id, "url": candidate.canonical_url or candidate.url, "reason": reason}


def _safe_component(value: str) -> str:
    return safe_slug(value) or "github-source"


def _excerpt(value: str, limit: int = 180) -> str:
    clean = re.sub(r"\s+", " ", value).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
