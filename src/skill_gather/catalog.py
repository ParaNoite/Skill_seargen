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


class CatalogStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.manifest_path = self.root / "catalog.json"
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
            seen.add(item_id)
            item = dict(raw)
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
