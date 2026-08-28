from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any


CATALOG_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
REQUIRED_FIELDS = {
    "id",
    "title",
    "summary",
    "category",
    "tags",
    "audience",
    "prerequisites",
    "source_url",
    "source_label",
    "source_version",
    "license",
    "license_path",
    "review_status",
    "evidence",
    "risks",
    "redistributable",
    "package_path",
    "accent",
    "icon",
}
AGENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
POPULARITY_STATUSES = {"observed", "not_assessed"}


class CatalogStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.manifest_path = self.root / "catalog.json"
        self.agents_manifest_path = self.root / "agents.json"
        self.packages_root = self.root / "packages"

    def list_items(
        self,
        *,
        query: str = "",
        category: str = "all",
        availability: str = "all",
    ) -> list[dict[str, Any]]:
        query_terms = [term.casefold() for term in query.split() if term.strip()]
        items = self._load_items()
        result: list[dict[str, Any]] = []
        for item in items:
            if category != "all" and item["category"] != category:
                continue
            if availability == "downloadable" and not item["downloadable"]:
                continue
            if availability == "source_only" and item["downloadable"]:
                continue
            searchable = " ".join(
                [item["title"], item["summary"], item["category"], item["audience"], *item["tags"]]
            ).casefold()
            if query_terms and not all(term in searchable for term in query_terms):
                continue
            result.append(item)
        return result

    def get_item(self, item_id: str) -> dict[str, Any]:
        if not CATALOG_ID_PATTERN.fullmatch(item_id):
            raise FileNotFoundError("无效的 Skill ID")
        for item in self._load_items():
            if item["id"] == item_id:
                return item
        raise FileNotFoundError("未找到这个 Skill")

    def categories(self) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for item in self._load_items():
            counts[item["category"]] = counts.get(item["category"], 0) + 1
        return [{"name": name, "count": counts[name]} for name in sorted(counts)]

    def build_download(self, item_id: str) -> tuple[str, bytes]:
        item = self.get_item(item_id)
        if not item["downloadable"]:
            raise PermissionError("该条目仅提供来源索引，未获得本地镜像下载授权")
        package_path = str(item.get("package_path", ""))
        if not package_path:
            raise FileNotFoundError("该 Skill 缺少本地包")
        packages_root = self.packages_root.resolve()
        package_root = (self.root / package_path).resolve()
        try:
            package_root.relative_to(packages_root)
        except ValueError as exc:
            raise ValueError("Skill 包路径超出受控目录") from exc
        if not package_root.is_dir() or not (package_root / "SKILL.md").is_file():
            raise FileNotFoundError("该 Skill 的本地包不完整")
        license_file = self._resolve_license_file(item_id, str(item.get("license_path", "")))

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(package_root.rglob("*")):
                if path.is_file():
                    try:
                        path.resolve().relative_to(packages_root)
                    except ValueError as exc:
                        raise ValueError("Skill 包包含指向受控目录外的文件") from exc
                    archive.write(path, Path(item_id) / path.relative_to(package_root))
            packaged_license = package_root / "LICENSE"
            if license_file.resolve() != packaged_license.resolve():
                archive.write(license_file, Path(item_id) / "LICENSE")
        return f"{item_id}.zip", buffer.getvalue()

    def build_agent_package(self, item_ids: list[str], agent_name: str = "skill-seargen-agent") -> tuple[str, bytes]:
        """Build a self-contained OpenCode agent folder from approved local Skills."""
        if not item_ids:
            raise ValueError("至少选择一个可下载 Skill")
        agent_name = agent_name.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,47}", agent_name):
            raise ValueError("Agent 名称需为 2-48 位字母、数字、下划线或短横线")
        unique_ids = list(dict.fromkeys(item_ids))
        items = [self.get_item(item_id) for item_id in unique_ids]
        if any(not item["downloadable"] for item in items):
            raise PermissionError("只能组装已验证、允许再分发的 Skill")

        buffer = io.BytesIO()
        root = Path(agent_name)
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            readme = "# Skill Seargen Agent\n\n解压到项目根目录后，用 OpenCode 打开该目录即可。\n\n已组装 Skills：\n" + "\n".join(f"- {item['title']} ({item['id']})" for item in items) + "\n"
            archive.writestr((root / "README.md").as_posix(), readme)
            agents_md = "# Skill Seargen Agent\n\n本目录由 skill_seargen 组装。按需使用 `.opencode/skills/` 下的能力，并遵守每个 Skill 的边界、证据和风险说明。\n"
            archive.writestr((root / "AGENTS.md").as_posix(), agents_md)
            agent_md = "---\ndescription: 使用已组装的 Skill 完成用户任务\nmode: primary\n---\n\n你是一个受 Skill 边界约束的 OpenCode Agent。先读取与任务相关的 `.opencode/skills/*/SKILL.md`，再执行任务；不得绕过其中的前置条件、风险和完成标准。\n"
            archive.writestr((root / ".opencode" / "agents" / f"{agent_name}.md").as_posix(), agent_md)
            for item in items:
                package_root = self._validated_package_root(item)
                license_file = self._resolve_license_file(item["id"], str(item.get("license_path", "")))
                for path in sorted(package_root.rglob("*")):
                    if path.is_file():
                        try:
                            path.resolve().relative_to(self.packages_root.resolve())
                        except ValueError as exc:
                            raise ValueError("Skill 包包含指向受控目录外的文件") from exc
                        archive.write(path, (root / ".opencode" / "skills" / item["id"] / path.relative_to(package_root)).as_posix())
                packaged_license = package_root / "LICENSE"
                if license_file.resolve() != packaged_license.resolve():
                    archive.write(license_file, (root / ".opencode" / "skills" / item["id"] / "LICENSE").as_posix())
        return f"{agent_name}.zip", buffer.getvalue()

    def list_agents(self) -> list[dict[str, Any]]:
        return self._load_agents()

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        if not AGENT_ID_PATTERN.fullmatch(agent_id):
            raise FileNotFoundError("无效的 Agent ID")
        for agent in self._load_agents():
            if agent["id"] == agent_id:
                return agent
        raise FileNotFoundError("未找到这个 Agent")

    def build_agent_download(self, agent_id: str) -> tuple[str, bytes]:
        agent = self.get_agent(agent_id)
        return self.build_agent_package([skill["id"] for skill in agent["skills"]], agent["id"])

    def _validated_package_root(self, item: dict[str, Any]) -> Path:
        package_root = (self.root / str(item.get("package_path", ""))).resolve()
        packages_root = self.packages_root.resolve()
        try:
            package_root.relative_to(packages_root)
        except ValueError as exc:
            raise ValueError("Skill 包路径超出受控目录") from exc
        if not package_root.is_dir() or not (package_root / "SKILL.md").is_file():
            raise FileNotFoundError("该 Skill 的本地包不完整")
        return package_root

    def _load_agents(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.agents_manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Agent 清单不可用：{exc}") from exc
        values = payload.get("agents") if isinstance(payload, dict) else None
        if not isinstance(values, list):
            raise ValueError("Agent 清单必须包含 agents 数组")
        skills = {item["id"]: item for item in self._load_items()}
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw in enumerate(values):
            if not isinstance(raw, dict):
                raise ValueError(f"Agent 清单第 {index + 1} 项必须是对象")
            required = {"id", "name", "role", "summary", "description", "skills", "accent", "status"}
            missing = sorted(required - raw.keys())
            agent_id = str(raw.get("id", ""))
            if missing or not AGENT_ID_PATTERN.fullmatch(agent_id) or agent_id in seen:
                raise ValueError(f"Agent 条目无效：{agent_id or index + 1}")
            skill_ids = raw["skills"]
            if not isinstance(skill_ids, list) or not skill_ids or not all(isinstance(value, str) for value in skill_ids):
                raise ValueError(f"Agent Skills 无效：{agent_id}")
            selected = []
            for skill_id in skill_ids:
                item = skills.get(skill_id)
                if item is None or not item["downloadable"]:
                    raise ValueError(f"Agent 只能引用可下载 Skill：{agent_id}")
                selected.append({"id": item["id"], "title": item["title"], "summary": item["summary"], "accent": item["accent"]})
            if not all(isinstance(raw[field], str) and raw[field].strip() for field in required - {"skills"}):
                raise ValueError(f"Agent 文本字段无效：{agent_id}")
            seen.add(agent_id)
            agent = dict(raw)
            agent["skills"] = selected
            agent["downloadable"] = True
            result.append(agent)
        return result

    def _load_items(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"目录清单不可用：{exc}") from exc
        values = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(values, list):
            raise ValueError("目录清单必须包含 items 数组")

        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for index, raw in enumerate(values):
            if not isinstance(raw, dict):
                raise ValueError(f"目录第 {index + 1} 项必须是对象")
            missing = sorted(REQUIRED_FIELDS - raw.keys())
            if missing:
                raise ValueError(f"目录条目缺少字段：{', '.join(missing)}")
            item_id = str(raw["id"])
            if not CATALOG_ID_PATTERN.fullmatch(item_id) or item_id in seen:
                raise ValueError(f"目录条目 ID 无效或重复：{item_id}")
            tags = raw["tags"]
            if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                raise ValueError(f"目录条目 tags 无效：{item_id}")
            if type(raw["redistributable"]) is not bool:
                raise ValueError(f"目录条目 redistributable 必须是布尔值：{item_id}")
            source_url = str(raw["source_url"])
            if not source_url.startswith(("https://", "http://")):
                raise ValueError(f"目录条目来源 URL 无效：{item_id}")
            review_status = str(raw["review_status"])
            if review_status not in {"verified", "needs_review"}:
                raise ValueError(f"目录条目复核状态无效：{item_id}")
            if raw["redistributable"] and review_status != "verified":
                raise ValueError(f"未经复核的目录条目不能开放下载：{item_id}")
            prerequisites = raw["prerequisites"]
            if not isinstance(prerequisites, list) or not all(isinstance(value, str) and value.strip() for value in prerequisites):
                raise ValueError(f"目录条目 prerequisites 无效：{item_id}")
            evidence = raw["evidence"]
            risks = raw["risks"]
            if not isinstance(evidence, list) or not all(isinstance(value, str) and value.strip() for value in evidence):
                raise ValueError(f"目录条目 evidence 无效：{item_id}")
            if not isinstance(risks, list) or not all(isinstance(value, str) and value.strip() for value in risks):
                raise ValueError(f"目录条目 risks 无效：{item_id}")
            featured = raw.get("featured", False)
            if type(featured) is not bool:
                raise ValueError(f"目录条目 featured 必须是布尔值：{item_id}")
            popularity = raw.get("popularity", {})
            if not isinstance(popularity, dict):
                raise ValueError(f"目录条目 popularity 必须是对象：{item_id}")
            popularity_status = str(popularity.get("status", "not_assessed"))
            if popularity_status not in POPULARITY_STATUSES:
                raise ValueError(f"目录条目 popularity.status 无效：{item_id}")
            signals = popularity.get("signals", [])
            limitations = popularity.get("limitations", [])
            if not isinstance(signals, list) or not all(isinstance(value, str) and value.strip() for value in signals):
                raise ValueError(f"目录条目 popularity.signals 无效：{item_id}")
            if not isinstance(limitations, list) or not all(isinstance(value, str) and value.strip() for value in limitations):
                raise ValueError(f"目录条目 popularity.limitations 无效：{item_id}")
            if featured and popularity_status != "observed":
                raise ValueError(f"精选条目必须有 observed 热度证据：{item_id}")
            seen.add(item_id)
            item = dict(raw)
            item["featured"] = featured
            item["popularity"] = {
                "status": popularity_status,
                "label": str(popularity.get("label", "")),
                "observed_at": str(popularity.get("observed_at", "")),
                "signals": list(signals),
                "limitations": list(limitations),
            }
            package_path = str(item.get("package_path", ""))
            item["downloadable"] = self._validate_downloadable_package(
                item_id,
                package_path,
                str(item.get("license_path", "")),
            ) if item["redistributable"] else False
            result.append(item)
        return result

    def _validate_downloadable_package(self, item_id: str, package_path: str, license_path: str) -> bool:
        if not package_path:
            raise ValueError(f"可再分发条目缺少本地包路径：{item_id}")
        packages_root = self.packages_root.resolve()
        package_root = (self.root / package_path).resolve()
        try:
            package_root.relative_to(packages_root)
        except ValueError as exc:
            raise ValueError(f"Skill 包路径超出受控目录：{item_id}") from exc
        if not package_root.is_dir() or not (package_root / "SKILL.md").is_file():
            raise ValueError(f"可下载 Skill 包不完整：{item_id}")
        if not license_path:
            raise ValueError(f"可下载 Skill 包缺少许可证证据路径：{item_id}")
        self._resolve_license_file(item_id, license_path)
        return True

    def _resolve_license_file(self, item_id: str, license_path: str) -> Path:
        catalog_root = self.root.resolve()
        license_file = (self.root / license_path).resolve()
        try:
            license_file.relative_to(catalog_root)
        except ValueError as exc:
            raise ValueError(f"许可证证据路径超出目录：{item_id}") from exc
        if not license_file.is_file():
            raise ValueError(f"可下载 Skill 包缺少许可证文件：{item_id}")
        return license_file


def default_catalog_root() -> Path:
    return Path(__file__).resolve().parents[2] / "catalog"
