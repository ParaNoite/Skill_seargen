import tempfile
import unittest
from pathlib import Path

from skill_gather.runs import read_json, write_json
from skill_gather.technical_skill import (
    _is_procedural_claim,
    _rank_procedures,
    apply_topic_human_review,
    generate_technical_skill,
)
from skill_gather.topics import TopicRunStore


class TechnicalSkillTests(unittest.TestCase):
    def test_generates_skill_and_dimension_score_from_saved_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("Godot 导航", mode="technical")
            root = store.run_path(task.run_id)
            (root / "topic_package/knowledge.md").write_text("# knowledge\n", encoding="utf-8")
            write_json(
                root / "topic_package/evidence/github-demo.json",
                {
                    "source_type": "github",
                    "findings": {"commands": [{"command": "godot --path ."}]},
                },
            )
            fusion = {
                "conclusions": [
                    {
                        "claim": "配置导航代理后运行验证命令。",
                        "citations": [{"source_id": "G1", "locator": "README.md:1"}],
                    }
                ],
                "conflicts": [],
                "evidence_gaps": [],
                "risk_flags": [],
            }

            skill_path, score_path, score = generate_technical_skill(task, root, fusion)

            skill = skill_path.read_text(encoding="utf-8")
            self.assertTrue(skill.startswith("---\nname:"))
            self.assertIn("description:", skill)
            self.assertIn("## Workflow", skill)
            self.assertIn("完成标准：", skill)
            self.assertIn("`references/index.md`", skill)
            self.assertNotIn("## Procedure", skill)
            self.assertIn("## Boundaries", skill)
            reference_index = root / "topic_package/references/index.md"
            self.assertTrue(reference_index.exists())
            self.assertIn("**G1**", reference_index.read_text(encoding="utf-8"))
            self.assertEqual(score["schema_version"], "0.9")
            self.assertEqual(set(score["dimensions"]), {"documentation", "skill", "evidence", "executability", "boundaries"})
            self.assertEqual(read_json(score_path)["final_status"], "passed")

    def test_human_review_updates_status_without_rewriting_evidence(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            score_path = Path(temp_dir) / "score.json"
            write_json(score_path, {"final_status": "failed", "evidence_count": 1})

            score = apply_topic_human_review(score_path, label="needs_changes", notes="补充边界")

            self.assertEqual(score["final_status"], "needs_review")
            self.assertEqual(score["evidence_count"], 1)
            self.assertEqual(score["human_review"]["notes"], "补充边界")

    def test_rejects_unrelated_conclusions_and_readme_paragraphs_as_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("Godot 信号总线", mode="technical")
            root = store.run_path(task.run_id)
            write_json(
                root / "topic_package/evidence/github-demo.json",
                {
                    "source_type": "github",
                    "findings": {"commands": [{"excerpt": "This repository contains Godot documentation."}]},
                },
            )
            fusion = {
                "conclusions": [{"claim": "Build the documentation with Sphinx.", "citations": [{"source_id": "G1"}]}],
                "conflicts": [],
                "evidence_gaps": [],
                "risk_flags": [],
            }

            skill_path, _score_path, score = generate_technical_skill(task, root, fusion)

            skill = skill_path.read_text(encoding="utf-8")
            self.assertIn("读取 `references/index.md`", skill)
            self.assertNotIn("## Commands", skill)
            self.assertEqual(score["final_status"], "failed")

    def test_treats_bare_api_calls_as_executable_steps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("Godot NavigationAgent2D avoidance", mode="technical")
            root = store.run_path(task.run_id)
            fusion = {
                "conclusions": [{"claim": "To use avoidance, call set_velocity() and move_and_slide() each frame.", "citations": [{"source_id": "S1"}]}],
                "conflicts": [],
                "evidence_gaps": [],
                "risk_flags": [],
            }

            skill_path, _score_path, score = generate_technical_skill(task, root, fusion)

            self.assertIn("set_velocity()", skill_path.read_text(encoding="utf-8"))
            self.assertEqual(score["dimensions"]["executability"], 70)

    def test_low_confidence_evidence_cannot_receive_a_full_evidence_score(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("Godot NavigationAgent2D", mode="technical")
            root = store.run_path(task.run_id)
            fusion = {
                "conclusions": [{"claim": "Call NavigationAgent2D.set_velocity() for avoidance.", "citations": [{"source_id": "S1"}], "low_confidence": True, "status": "needs_review"}],
                "conflicts": [],
                "evidence_gaps": [],
                "risk_flags": [],
            }

            _skill_path, _score_path, score = generate_technical_skill(task, root, fusion)

            self.assertEqual(score["dimensions"]["evidence"], 60)

    def test_topic_terms_in_citation_titles_keep_subject_implicit_claims(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume(
                "Godot NavigationAgent2D examples repository",
                mode="technical",
            )
            root = store.run_path(task.run_id)
            fusion = {
                "conclusions": [
                    {
                        "claim": "设置 target_position 后，必须在每个物理帧调用 get_next_path_position 更新内部路径。",
                        "citations": [
                            {
                                "source_id": "S1",
                                "title": "NavigationAgent2D - Godot Engine documentation",
                                "url": "https://docs.godotengine.org/classes/class_navigationagent2d.html",
                            }
                        ],
                    }
                ],
                "conflicts": [],
                "evidence_gaps": [],
                "risk_flags": [],
            }

            skill_path, _score_path, score = generate_technical_skill(task, root, fusion)

            self.assertEqual(score["evidence_count"], 1)
            self.assertIn("get_next_path_position", skill_path.read_text(encoding="utf-8"))

    def test_rejects_headings_prose_and_api_signatures_as_workflow_steps(self):
        self.assertFalse(_is_procedural_claim("Agent-to-Agent Avoidance (RVO)"))
        self.assertFalse(_is_procedural_claim("A loading screen scene (e.g., with a spinner animation)."))
        self.assertFalse(_is_procedural_claim("void set_avoidance_enabled ( bool value )"))
        self.assertFalse(_is_procedural_claim('GD.PrintErr($"Loading {path}");'))
        self.assertFalse(_is_procedural_claim("if (status == ResourceLoader.ThreadLoadStatus.Failed)"))
        self.assertFalse(_is_procedural_claim('You call load_threaded_request("res://world.tscn") and wait.'))
        self.assertFalse(_is_procedural_claim("使用 NavigationAgent avoidance_enabled 属性是切换避障的首选选项。"))
        self.assertTrue(
            _is_procedural_claim(
                "To use avoidance, call set_velocity() and move_and_slide() each frame."
            )
        )

    def test_workflow_deduplicates_api_variants_and_drops_disable_steps(self):
        ranked = _rank_procedures(
            [
                {"claim": "NavigationServer2D.agent_set_avoidance_enabled(agent, true)"},
                {"claim": "NavigationServer2D.AgentSetAvoidanceEnabled(agent, true);"},
                {"claim": "NavigationServer2D.agent_set_avoidance_enabled(agent, false)"},
                {"claim": "NavigationServer2D.agent_set_avoidance_callback(agent, Callable())"},
            ]
        )

        self.assertEqual(
            [item["claim"] for item in ranked],
            ["NavigationServer2D.agent_set_avoidance_enabled(agent, true)"],
        )

    def test_false_argument_does_not_drop_a_resource_loader_request(self):
        ranked = _rank_procedures(
            [
                {
                    "claim": 'ResourceLoader.load_threaded_request(path, "PackedScene", false, ResourceLoader.CACHE_MODE_REPLACE)'
                }
            ]
        )

        self.assertEqual(len(ranked), 1)

    def test_godot_4_keeps_autoload_registration_and_drops_old_signal_syntax(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("Godot 4 signal bus autoload", mode="technical")
            root = store.run_path(task.run_id)
            fusion = {
                "conclusions": [
                    {
                        "claim": "In the Project Settings, navigate to the Autoloads tab and register your new script as an auto-loaded node.",
                        "citations": [{"source_id": "S1"}],
                    },
                    {
                        "claim": 'Connect with Events.connect("signal_name", self, "_on_signal_name").',
                        "citations": [{"source_id": "S2"}],
                    },
                    {
                        "claim": "From any script, call Events.emit_signal() to emit the signal.",
                        "citations": [{"source_id": "S3"}],
                    },
                ],
                "conflicts": [],
                "evidence_gaps": [],
                "risk_flags": [],
            }

            skill_path, _score_path, score = generate_technical_skill(task, root, fusion)
            skill = skill_path.read_text(encoding="utf-8")

            self.assertIn("Autoloads tab", skill)
            self.assertNotIn('Events.connect("signal_name"', skill)
            self.assertNotIn("Events.emit_signal()", skill)
            self.assertEqual(score["dimensions"]["executability"], 70)

    def test_workflow_creates_script_before_one_autoload_registration(self):
        ranked = _rank_procedures(
            [
                {"claim": 'Register it once in Project Settings Autoload as "Events".'},
                {"claim": "Create a new script that extends Node and define signals on it."},
                {"claim": "In Project Settings, navigate to Autoloads and register the script."},
            ]
        )

        self.assertEqual(
            [item["claim"] for item in ranked],
            [
                "Create a new script that extends Node and define signals on it.",
                "In Project Settings, navigate to Autoloads and register the script.",
            ],
        )

    def test_configfile_topic_keeps_short_api_and_rejects_generic_save_steps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume(
                "Godot 4 ConfigFile save game versioning migration atomic persistence",
                mode="technical",
            )
            root = store.run_path(task.run_id)
            fusion = {
                "conclusions": [
                    {"claim": "var config = ConfigFile.new()", "citations": [{"source_id": "S1"}]},
                    {"claim": "第二步：实现 MigrationRegistry", "citations": [{"source_id": "S1"}]},
                    {
                        "claim": 'var save_file = FileAccess.open("user://savegame.save", FileAccess.WRITE)',
                        "citations": [{"source_id": "S2"}],
                    },
                    {"claim": "save_file.store_line(json_string)", "citations": [{"source_id": "S2"}]},
                    {
                        "claim": "使用提供的 key 将 ConfigFile 对象的内容保存到 AES-256 加密文件中。",
                        "citations": [{"source_id": "S1"}],
                    },
                    {
                        "claim": "将 ConfigFile 对象保存到 AES-256 文件中，使用 password 加密。",
                        "citations": [{"source_id": "S1"}],
                    },
                ],
                "conflicts": [],
                "evidence_gaps": [],
                "risk_flags": [],
            }

            skill_path, _score_path, _score = generate_technical_skill(task, root, fusion)
            skill = skill_path.read_text(encoding="utf-8")

            self.assertIn("ConfigFile.new()", skill)
            self.assertIn("MigrationRegistry", skill)
            self.assertNotIn("FileAccess.open", skill)
            self.assertNotIn("store_line", skill)
            self.assertEqual(skill.count("AES-256"), 1)
            self.assertEqual(_score["dimensions"]["executability"], 70)

    def test_file_open_precedes_write_when_both_are_relevant(self):
        ranked = _rank_procedures(
            [
                {"claim": "save_file.store_line(json_string)"},
                {"claim": 'var save_file = FileAccess.open("user://savegame.save", FileAccess.WRITE)'},
            ]
        )

        self.assertEqual(
            [item["claim"] for item in ranked],
            [
                'var save_file = FileAccess.open("user://savegame.save", FileAccess.WRITE)',
                "save_file.store_line(json_string)",
            ],
        )

    def test_configfile_creation_prefers_code_and_short_plain_assignment_is_not_useful(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("Godot 4 ConfigFile loading", mode="technical")
            root = store.run_path(task.run_id)
            fusion = {
                "conclusions": [
                    {"claim": "创建新的 ConfigFile 对象。", "citations": [{"source_id": "S1"}]},
                    {"claim": "var config = ConfigFile.new()", "citations": [{"source_id": "S1"}]},
                    {"claim": "var loading := true", "citations": [{"source_id": "S1"}]},
                ],
                "conflicts": [],
                "evidence_gaps": [],
                "risk_flags": [],
            }

            skill_path, _score_path, _score = generate_technical_skill(task, root, fusion)
            skill = skill_path.read_text(encoding="utf-8")

            self.assertIn("var config = ConfigFile.new()", skill)
            self.assertNotIn("创建新的 ConfigFile 对象", skill)
            self.assertNotIn("var loading := true", skill)

    def test_chinese_numbered_actions_are_executable_and_keep_step_order(self):
        ranked = _rank_procedures(
            [
                {"claim": "第二步：实现 MigrationRegistry"},
                {"claim": "第一步：建立 SaveData 子类的版本字段"},
            ]
        )

        self.assertEqual(
            [item["claim"] for item in ranked],
            [
                "第一步：建立 SaveData 子类的版本字段",
                "第二步：实现 MigrationRegistry",
            ],
        )

    def test_2d_topic_excludes_3d_workflow_claims(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TopicRunStore(Path(temp_dir) / "runs")
            task = store.start_or_resume("Godot NavigationServer2D avoidance", mode="technical")
            root = store.run_path(task.run_id)
            fusion = {
                "conclusions": [
                    {"claim": "NavigationServer2D.agent_set_velocity(agent, velocity)", "citations": [{"source_id": "S1"}]},
                    {"claim": "NavigationServer3D.agent_set_velocity(agent, velocity)", "citations": [{"source_id": "S2"}]},
                ],
                "conflicts": [],
                "evidence_gaps": [],
                "risk_flags": [],
            }

            skill_path, _score_path, _score = generate_technical_skill(task, root, fusion)
            skill = skill_path.read_text(encoding="utf-8")

            self.assertIn("NavigationServer2D.agent_set_velocity", skill)
            self.assertNotIn("NavigationServer3D.agent_set_velocity", skill)


if __name__ == "__main__":
    unittest.main()
