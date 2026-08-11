import json
import tomllib
import unittest
from pathlib import Path

from skill_gather import __version__


class VersionTests(unittest.TestCase):
    def test_project_versions_match(self) -> None:
        root = Path(__file__).resolve().parents[1]
        package_version = json.loads((root / "package.json").read_text(encoding="utf-8"))["version"]
        pyproject_version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]

        self.assertEqual(__version__, "1.0.0")
        self.assertEqual(package_version, __version__)
        self.assertEqual(pyproject_version, __version__)


if __name__ == "__main__":
    unittest.main()
