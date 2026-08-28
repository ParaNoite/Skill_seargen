import json
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from skill_gather.catalog import CatalogStore, default_catalog_root


class CatalogStoreTests(unittest.TestCase):
    def test_repository_catalog_has_curated_complete_items(self):
        store = CatalogStore(default_catalog_root())

        items = store.list_items()

        self.assertEqual(len(items), 22)
        self.assertEqual(sum(item["downloadable"] for item in items), 9)
        self.assertTrue(all(item["source_url"].startswith("https://") for item in items))
        self.assertTrue(all(item["license"] for item in items))
        self.assertEqual(
            {item["id"] for item in items[:3]},
            {"browser-game-prototype", "browser-game-playtest", "game-loop-design"},
        )
        self.assertEqual(store.get_item("nvidia-skillspector")["popularity"]["status"], "observed")
        self.assertTrue(store.get_item("math-modeling-method")["downloadable"])

    def test_filters_by_query_category_and_availability(self):
        store = CatalogStore(default_catalog_root())

        self.assertEqual([item["id"] for item in store.list_items(query="ASR")], ["video-evidence-distiller"])
        self.assertEqual(len(store.list_items(category="研究")), 4)
        self.assertEqual(len(store.list_items(availability="downloadable")), 9)
        self.assertEqual(len(store.list_items(availability="source_only")), 13)

    def test_ppt_agent_is_a_complete_downloadable_skill(self):
        store = CatalogStore(default_catalog_root())

        item = store.get_item("ppt-agent")

        self.assertTrue(item["downloadable"])
        self.assertEqual(item["review_status"], "verified")
        filename, payload = store.build_agent_package(["ppt-agent"], "ppt-agent-demo")
        self.assertEqual(filename, "ppt-agent-demo.zip")
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            self.assertIn("ppt-agent-demo/.opencode/skills/ppt-agent/SKILL.md", archive.namelist())
            self.assertIn("ppt-agent-demo/.opencode/skills/ppt-agent/LICENSE", archive.namelist())
            skill_text = archive.read("ppt-agent-demo/.opencode/skills/ppt-agent/SKILL.md").decode("utf-8")
            self.assertIn("重新打开最终 PPTX", skill_text)

    def test_builds_zip_for_authorized_local_package(self):
        store = CatalogStore(default_catalog_root())

        filename, payload = store.build_download("video-evidence-distiller")

        self.assertEqual(filename, "video-evidence-distiller.zip")
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            self.assertIn("video-evidence-distiller/SKILL.md", archive.namelist())
            self.assertIn("video-evidence-distiller/LICENSE", archive.namelist())

    def test_builds_opencode_agent_from_selected_skills(self):
        store = CatalogStore(default_catalog_root())

        filename, payload = store.build_agent_package(["browser-game-prototype", "game-loop-design"], "runner-agent")

        self.assertEqual(filename, "runner-agent.zip")
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            names = archive.namelist()
            self.assertIn("runner-agent/AGENTS.md", names)
            self.assertIn("runner-agent/.opencode/agents/runner-agent.md", names)
            self.assertIn("runner-agent/.opencode/skills/browser-game-prototype/SKILL.md", names)
            self.assertIn("runner-agent/.opencode/skills/game-loop-design/SKILL.md", names)

    def test_agent_package_rejects_empty_selection(self):
        with self.assertRaisesRegex(ValueError, "至少选择"):
            CatalogStore(default_catalog_root()).build_agent_package([])

    def test_lists_and_downloads_curated_agents(self):
        store = CatalogStore(default_catalog_root())

        agent = store.get_agent("ppt-presenter")
        self.assertEqual(agent["role"], "演示文稿顾问")
        self.assertEqual([skill["id"] for skill in agent["skills"]], ["ppt-agent", "skill-release-review"])
        filename, payload = store.build_agent_download("ppt-presenter")
        self.assertEqual(filename, "ppt-presenter.zip")
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            self.assertIn("ppt-presenter/.opencode/agents/ppt-presenter.md", archive.namelist())
            self.assertIn("ppt-presenter/.opencode/skills/ppt-agent/SKILL.md", archive.namelist())

    def test_builds_zip_with_the_manifest_license_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package_root = root / "packages" / "custom-license"
            package_root.mkdir(parents=True)
            (package_root / "SKILL.md").write_text("# Custom license", encoding="utf-8")
            (root / "licenses").mkdir()
            (root / "licenses" / "CUSTOM.txt").write_text("CUSTOM TERMS", encoding="utf-8")
            (root / "catalog.json").write_text(
                json.dumps({"items": [{
                    "id": "custom-license", "title": "Custom", "summary": "Custom license", "category": "测试",
                    "tags": [], "audience": "tester", "prerequisites": ["fixture"], "source_url": "https://example.com",
                    "source_label": "example", "source_version": "1", "license": "Custom",
                    "license_path": "licenses/CUSTOM.txt", "review_status": "verified", "redistributable": True,
                    "package_path": "packages/custom-license", "accent": "blue", "icon": "file",
                    "evidence": ["fixture"], "risks": ["fixture"],
                }]}),
                encoding="utf-8",
            )

            _, payload = CatalogStore(root).build_download("custom-license")

            with zipfile.ZipFile(BytesIO(payload)) as archive:
                self.assertEqual(archive.read("custom-license/LICENSE"), b"CUSTOM TERMS")

    def test_rejects_download_for_source_only_item(self):
        store = CatalogStore(default_catalog_root())

        with self.assertRaises(PermissionError):
            store.build_download("openai-docs")

    def test_rejects_package_path_outside_controlled_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "catalog.json").write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "unsafe-skill",
                                "title": "Unsafe",
                                "summary": "Unsafe path",
                                "category": "测试",
                                "tags": [],
                                "audience": "tester",
                                "prerequisites": ["fixture"],
                                "source_url": "https://example.com",
                                "source_label": "example",
                                "source_version": "1",
                                "license": "MIT",
                                "license_path": "packages/LICENSE",
                                "review_status": "verified",
                                "evidence": ["fixture"],
                                "risks": ["fixture"],
                                "redistributable": True,
                                "package_path": "../outside",
                                "accent": "blue",
                                "icon": "file",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                CatalogStore(root).build_download("unsafe-skill")

    def test_rejects_string_redistribution_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "catalog.json").write_text(
                json.dumps({"items": [{
                    "id": "bad-flag", "title": "Bad", "summary": "Bad flag", "category": "测试",
                    "tags": [], "audience": "tester", "prerequisites": ["fixture"], "source_url": "https://example.com",
                    "source_label": "example", "source_version": "1", "license": "MIT", "license_path": "packages/LICENSE",
                    "review_status": "verified", "redistributable": "false", "package_path": "packages/bad-flag",
                    "accent": "blue", "icon": "file",
                    "evidence": ["fixture"], "risks": ["fixture"],
                }]}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "必须是布尔值"):
                CatalogStore(root).list_items()


if __name__ == "__main__":
    unittest.main()
