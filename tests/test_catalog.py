import json
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from skill_gather.catalog import CatalogStore, default_catalog_root


class CatalogStoreTests(unittest.TestCase):
    def test_repository_catalog_has_twelve_complete_items(self):
        store = CatalogStore(default_catalog_root())

        items = store.list_items()

        self.assertEqual(len(items), 12)
        self.assertEqual(sum(item["downloadable"] for item in items), 4)
        self.assertTrue(all(item["source_url"].startswith("https://") for item in items))
        self.assertTrue(all(item["license"] for item in items))

    def test_filters_by_query_category_and_availability(self):
        store = CatalogStore(default_catalog_root())

        self.assertEqual([item["id"] for item in store.list_items(query="ASR")], ["video-evidence-distiller"])
        self.assertEqual(len(store.list_items(category="研究")), 2)
        self.assertEqual(len(store.list_items(availability="downloadable")), 4)
        self.assertEqual(len(store.list_items(availability="source_only")), 8)

    def test_builds_zip_for_authorized_local_package(self):
        store = CatalogStore(default_catalog_root())

        filename, payload = store.build_download("video-evidence-distiller")

        self.assertEqual(filename, "video-evidence-distiller.zip")
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            self.assertIn("video-evidence-distiller/SKILL.md", archive.namelist())
            self.assertIn("video-evidence-distiller/LICENSE", archive.namelist())

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
