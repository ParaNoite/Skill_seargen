import json
import tempfile
import unittest
from pathlib import Path

from skill_gather.models import TopicSourceCandidate
from skill_gather.runs import write_json
from skill_gather.topic_fusion import fuse_topic_evidence, write_fusion_artifacts
from skill_gather.topics import TopicRunStore


class TopicFusionTests(unittest.TestCase):
    def test_fuses_matching_claims_across_three_source_types_with_bounded_citations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store, task, root = self._topic(Path(temp_dir), source_types=("web", "video", "github"))
            claim = "安装工具后运行验证命令可以确认配置是否成功。"
            write_json(root / "topic_package/evidence/web.json", self._web("web-one", claim, quality=80))
            write_json(root / "topic_package/evidence/video.json", self._video("video-one", claim, confidence=0.8))
            write_json(root / "topic_package/evidence/github.json", self._github("github-one", claim, quality=90))

            fusion = fuse_topic_evidence(task, root)

            conclusion = fusion["conclusions"][0]
            self.assertEqual(conclusion["supporting_source_count"], 3)
            self.assertEqual(conclusion["supporting_source_types"], ["github", "video", "web"])
            self.assertEqual(len(conclusion["citations"]), 3)
            self.assertFalse(conclusion["low_confidence"])
            self.assertEqual(conclusion["status"], "supported")
            self.assertFalse(fusion["conflicts"])

    def test_fuses_a_substantial_claim_wrapped_by_source_attribution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _store, task, root = self._topic(Path(temp_dir), source_types=("web", "github"))
            write_json(
                root / "topic_package/evidence/web.json",
                self._web(
                    "web-one",
                    "下载链接：godot-statecharts A state charts extension for Godot 4 项目地址：https://example.test。",
                ),
            )
            write_json(
                root / "topic_package/evidence/github.json",
                self._github("github-one", "A state charts extension for Godot 4.", quality=90),
            )

            fusion = fuse_topic_evidence(task, root)

            conclusion = fusion["conclusions"][0]
            self.assertEqual(conclusion["supporting_source_count"], 2)
            self.assertEqual(conclusion["status"], "supported")

    def test_does_not_fuse_short_topic_labels_by_containment_alone(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _store, task, root = self._topic(Path(temp_dir), source_types=("web", "github"))
            write_json(
                root / "topic_package/evidence/web.json",
                self._web("web-one", "Godot State Charts 插件指南介绍了复杂游戏状态管理。"),
            )
            write_json(
                root / "topic_package/evidence/github.json",
                self._github("github-one", "Godot State Charts", quality=90),
            )

            fusion = fuse_topic_evidence(task, root)

            self.assertEqual(len(fusion["conclusions"]), 2)

    def test_preserves_opposite_claims_as_unresolved_conflict(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _store, task, root = self._topic(Path(temp_dir), source_types=("web", "github"))
            write_json(root / "topic_package/evidence/web.json", self._web("web-one", "部署前必须启用缓存以避免重复计算。"))
            write_json(root / "topic_package/evidence/github.json", self._github("github-one", "部署前不要启用缓存以避免重复计算。"))

            fusion = fuse_topic_evidence(task, root)

            self.assertEqual(len(fusion["conflicts"]), 1)
            conflict = fusion["conflicts"][0]
            self.assertEqual(conflict["status"], "unresolved")
            self.assertEqual(len(conflict["claims"]), 2)
            self.assertEqual(fusion["conclusions"][0]["status"], "conflicted")
            self.assertEqual(fusion["conclusions"][0]["conflict_ids"], ["X1"])
            self.assertIn("unresolved_source_conflicts", fusion["risk_flags"])
            self.assertIn("unresolved_conflicts", {item["code"] for item in fusion["evidence_gaps"]})

    def test_marks_single_source_conclusions_low_confidence_and_records_gap(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _store, task, root = self._topic(Path(temp_dir), source_types=("video",))
            write_json(root / "topic_package/evidence/video.json", self._video("video-one", "使用明确的验证步骤检查最终结果。", confidence=0.92))

            fusion = fuse_topic_evidence(task, root)

            self.assertTrue(fusion["conclusions"][0]["low_confidence"])
            self.assertEqual(fusion["conclusions"][0]["status"], "needs_review")
            self.assertIn("single_source_type", {item["code"] for item in fusion["evidence_gaps"]})

    def test_compacts_video_asr_in_time_order_and_excludes_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _store, task, root = self._topic(Path(temp_dir), source_types=("video",))
            items = [
                {
                    "timestamp": f"00:00:{index:02d}",
                    "type": "asr",
                    "claim": f"segment {index} " + ("detail " * 12),
                    "confidence": 0.7,
                }
                for index in range(20)
            ]
            items.extend(
                [
                    {"timestamp": "00:00:00", "type": "metadata_title", "claim": "视频标题说明主题", "confidence": 0.45},
                    {"timestamp": "00:00:00", "type": "metadata_author", "claim": "视频作者", "confidence": 0.35},
                ]
            )
            video = self._video("video-one", "placeholder", confidence=0.7)
            video["timeline"]["items"] = items
            write_json(root / "topic_package/evidence/video.json", video)

            fusion = fuse_topic_evidence(task, root)

            claims = [item["claim"] for item in fusion["conclusions"]]
            self.assertLessEqual(len(claims), 12)
            self.assertTrue(claims[0].startswith("segment 0"))
            self.assertIn("segment 19", claims[-1])
            self.assertNotIn("视频标题说明主题", claims)
            self.assertNotIn("视频作者", claims)

    def test_joins_chinese_asr_fragments_with_chinese_punctuation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _store, task, root = self._topic(Path(temp_dir), source_types=("video",))
            video = self._video("video-one", "placeholder", confidence=0.7)
            video["timeline"]["items"] = [
                {"timestamp": "00:00:00", "type": "asr", "claim": "首先创建状态基类", "confidence": 0.7},
                {"timestamp": "00:00:04", "type": "asr", "claim": "然后定义进入和退出方法", "confidence": 0.7},
            ]
            write_json(root / "topic_package/evidence/video.json", video)

            fusion = fuse_topic_evidence(task, root)

            self.assertEqual(fusion["conclusions"][0]["claim"], "首先创建状态基类，然后定义进入和退出方法。")

    def test_excludes_pure_video_outro_but_keeps_technical_intro(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _store, task, root = self._topic(Path(temp_dir), source_types=("video",))
            video = self._video("video-one", "placeholder", confidence=0.7)
            video["timeline"]["items"] = [
                {
                    "timestamp": "00:00:00",
                    "type": "asr",
                    "claim": "欢迎来到教程，首先安装 StateCharts 插件",
                    "confidence": 0.7,
                },
                {
                    "timestamp": "00:09:15",
                    "type": "asr",
                    "claim": "下一期会把状态图插件用于项目，估计还是要间隔一段时间，因为空闲时间不多",
                    "confidence": 0.7,
                },
                {
                    "timestamp": "00:09:20",
                    "type": "asr",
                    "claim": "感谢同学们的收看和陪伴，下期再见",
                    "confidence": 0.7,
                },
            ]
            write_json(root / "topic_package/evidence/video.json", video)

            fusion = fuse_topic_evidence(task, root)

            claims = [item["claim"] for item in fusion["conclusions"]]
            self.assertEqual(len(claims), 1)
            self.assertIn("StateCharts 插件", claims[0])
            self.assertIn("状态图插件用于项目", claims[0])
            self.assertNotIn("估计还是要间隔", claims[0])
            self.assertNotIn("下期再见", claims[0])

    def test_writes_auditable_fusion_and_knowledge_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store, task, root = self._topic(Path(temp_dir), source_types=("web",))
            write_json(root / "topic_package/evidence/web.json", self._web("web-one", "首先安装依赖，然后运行测试验证安装结果。"))

            fusion = fuse_topic_evidence(task, root)
            fusion_path, knowledge_path = write_fusion_artifacts(task, root, fusion)
            task.artifacts["fusion"] = fusion_path.relative_to(root).as_posix()
            task.artifacts["knowledge"] = knowledge_path.relative_to(root).as_posix()
            store.save(task)

            saved = json.loads(fusion_path.read_text(encoding="utf-8"))
            knowledge = knowledge_path.read_text(encoding="utf-8")
            loaded = store.load(task.run_id)
            self.assertEqual(saved["schema_version"], "0.8")
            self.assertIn("## 关键结论", knowledge)
            self.assertIn("[S1:sentence:1]", knowledge)
            self.assertEqual(loaded.artifacts["fusion"], "topic_package/fusion.json")
            self.assertEqual(loaded.package.fusion, "topic_package/fusion.json")

    def test_web_evidence_prioritizes_later_topic_actions_over_intro_boilerplate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _store, task, root = self._topic(Path(temp_dir), source_types=("web",))
            task.topic = "Godot NavigationAgent2D 避障"
            intro = "。".join(f"这是文档介绍段落 {index}，用于说明版本和阅读注意事项" for index in range(20))
            action = "为 NavigationAgent2D 设置 target_position，然后每帧调用 get_next_path_position。"
            write_json(root / "topic_package/evidence/web.json", self._web("web-one", intro + "。" + action))

            fusion = fuse_topic_evidence(task, root)

            self.assertIn(action, [item["claim"] for item in fusion["conclusions"]])

    def test_web_evidence_scans_past_120_sentences_for_migration_actions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            _store, task, root = self._topic(Path(temp_dir), source_types=("web",))
            task.topic = "Godot 4 ConfigFile schema migration persistence"
            intro = "。".join(
                f"ConfigFile 持久化背景说明段落 {index}，用于介绍存档设计的一般考虑"
                for index in range(140)
            )
            action = "建立 schema_version 字段，并实现 MigrationRegistry 逐版本迁移。"
            write_json(
                root / "topic_package/evidence/web.json",
                self._web("web-one", intro + "。" + action),
            )

            fusion = fuse_topic_evidence(task, root)

            self.assertIn(action, [item["claim"] for item in fusion["conclusions"]])

    def _topic(self, root: Path, *, source_types: tuple[str, ...]):
        store = TopicRunStore(root / "runs")
        task = store.start_or_resume("融合测试", mode="technical")
        task.selected_sources = [
            TopicSourceCandidate(
                url=f"https://example.test/{source_type}",
                candidate_id=f"{source_type}-one",
                source_type=source_type,
                selected=True,
            )
            for source_type in source_types
        ]
        store.save(task)
        return store, task, store.run_path(task.run_id)

    @staticmethod
    def _web(candidate_id: str, claim: str, *, quality: int = 80):
        return {
            "source_id": "S1", "candidate_id": candidate_id, "source_type": "web",
            "url": "https://example.test/web", "title": "网页来源", "quality_score": quality,
            "risk_flags": [], "text": claim,
        }

    @staticmethod
    def _video(candidate_id: str, claim: str, *, confidence: float):
        return {
            "candidate_id": candidate_id, "source_type": "video",
            "manifest": {"title": "视频来源", "url": "https://example.test/video", "risk_flags": []},
            "timeline": {"items": [{"timestamp": "00:00:12", "type": "asr", "claim": claim, "confidence": confidence}]},
        }

    @staticmethod
    def _github(candidate_id: str, claim: str, *, quality: int = 80):
        return {
            "source_id": "G1", "candidate_id": candidate_id, "source_type": "github", "repo": "example/repo",
            "url": "https://github.com/example/repo", "quality_score": quality, "confidence": quality / 100,
            "risk_flags": [], "findings": {"commands": [{"path": "README.md", "excerpt": claim}]},
        }


if __name__ == "__main__":
    unittest.main()
