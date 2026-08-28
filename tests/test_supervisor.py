import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from skill_gather.supervisor import SupervisorConfigError, SupervisorStore, load_supervisor_config


class SupervisorTests(unittest.TestCase):
    def test_supervisor_prompt_locks_ted_opening_contract(self):
        project_root = Path(__file__).resolve().parents[1]
        prompt = (project_root / ".agents" / "supervisor" / "AGENT.md").read_text(encoding="utf-8")
        config = json.loads((project_root / "configs" / "ted-supervisor.json").read_text(encoding="utf-8"))

        opening_game = config["supervisor"]["opening_game"]

        for document in ("README.md", "docs/evidence-contract.md"):
            self.assertIn(document, prompt)
        self.assertIn("2D 跑酷", prompt)
        self.assertIn("3D 跑酷", prompt)
        self.assertIn("禁止生成地牢", prompt)
        self.assertIn("needs_review", prompt)
        self.assertEqual(opening_game["baseline_path"], "frontend/ted-games/baseline-2d")
        self.assertEqual(opening_game["target_path"], "frontend/ted-games/skill-3d")
        self.assertIn("不得替换为地牢", opening_game["task_prompt"])

    def test_ensure_docker_script_parses_in_windows_powershell(self):
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("Windows PowerShell is not available")

        project_root = Path(__file__).resolve().parents[1]
        script_path = project_root / "scripts" / "ensure-docker.ps1"
        command = (
            "$tokens=$null; $errors=$null; "
            f"[System.Management.Automation.Language.Parser]::ParseFile('{script_path}', "
            "[ref]$tokens, [ref]$errors) > $null; "
            "$errors | ForEach-Object { $_.Message }; "
            "if ($errors.Count -gt 0) { exit 1 }"
        )

        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", command],
            cwd=project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _write_config(self, directory: str) -> Path:
        path = Path(directory) / "ted-supervisor.json"
        path.write_text(json.dumps({
            "supervisor": {
                "max_fix_rounds": 3,
                "max_normal_topics": 5,
                "allowed_paths": ["src", "tests"],
                "ted_critical_topics": ["opening-3d-game-skill"],
                "capture": {"ted_relevance_threshold": 70, "max_showcase_frames": 5},
                "opening_game": {"task_prompt": "制作一款 3D 游戏"},
            }
        }), encoding="utf-8")
        return path

    def test_start_persists_recoverable_state(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load_supervisor_config(self._write_config(directory))
            store = SupervisorStore(Path(directory) / "lab")
            state = store.start(config, "demo")

            self.assertEqual(state["status"], "active")
            self.assertEqual(store.load("demo")["config"]["max_fix_rounds"], 3)

    def test_add_theme_marks_explicit_critical_topic(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load_supervisor_config(self._write_config(directory))
            store = SupervisorStore(Path(directory) / "lab")
            store.start(config, "demo")

            critical = store.add_theme("demo", topic="opening-3d-game-skill", reason_selected="TED 开场", utility_score=90)
            ordinary = store.add_theme("demo", topic="Playwright 浏览器回归", reason_selected="热门工程能力", utility_score=88)

            self.assertEqual(critical["acceptance_level"], "ted_critical")
            self.assertEqual(ordinary["acceptance_level"], "normal")

    def test_start_or_resume_prefers_latest_active_state(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load_supervisor_config(self._write_config(directory))
            store = SupervisorStore(Path(directory) / "lab")
            created, resumed = store.start_or_resume(config)
            recovered, recovered_resumed = store.start_or_resume(config)

            self.assertFalse(resumed)
            self.assertTrue(recovered_resumed)
            self.assertEqual(recovered["supervision_id"], created["supervision_id"])

    def test_rejects_invalid_fix_round_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps({"supervisor": {"max_fix_rounds": 0}}), encoding="utf-8")
            with self.assertRaises(SupervisorConfigError):
                load_supervisor_config(path)

    def test_uses_defaults_for_minimal_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "minimal.json"
            path.write_text("{}", encoding="utf-8")

            config = load_supervisor_config(path)

            self.assertEqual(config.max_fix_rounds, 3)
            self.assertIn("src", config.allowed_paths)
            self.assertTrue(config.capture.require_trace)
            self.assertEqual(config.capture.max_showcase_frames, 5)

    def test_add_theme_promotes_agent_assessed_ted_topic(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load_supervisor_config(self._write_config(directory))
            store = SupervisorStore(Path(directory) / "lab")
            store.start(config, "demo")

            item = store.add_theme(
                "demo",
                topic="AI 课程生成",
                reason_selected="演讲核心主题",
                ted_relevance_score=82,
                narrative_beats=["generate", "course_skill"],
                showcase_reason="可展示学习和产物",
                expected_artifacts=["COURSE.md", "SKILL.md"],
            )

            self.assertEqual(item["acceptance_level"], "ted_critical")
            self.assertEqual(item["narrative_beats"], ["generate", "course_skill"])
