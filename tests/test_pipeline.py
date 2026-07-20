import tempfile
import unittest
from pathlib import Path

from skill_gather.adapters.bilibili import build_initial_manifest
from skill_gather.config import parse_config
from skill_gather.integrations import YtDlpError
from skill_gather.models import PIPELINE_STAGES
from skill_gather.pipeline import run_video_pipeline
from skill_gather.runs import RunStore, read_json, write_json
from skill_gather.source import infer_source


CONFIG = {
    "providers": {
        "newapi": {
            "base_url": "https://api.renice.cc/v1",
            "api_key_env": "NEWAPI_API_KEY",
            "vision_model": "vision",
            "asr_model": "asr",
            "distiller_model": "distiller",
            "judge_model": "judge",
        }
    },
    "defaults": {
        "provider": "newapi",
        "output_dir": "./skills",
        "run_dir": "./runs",
    },
}


class FakeMetadataProbe:
    def __init__(self, metadata=None, error: Exception | None = None):
        self.metadata = metadata or {}
        self.error = error

    def probe_metadata(self, url):
        if self.error is not None:
            raise self.error
        return self.metadata


class FakeMediaDownloader:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result or {
            "status": "downloaded",
            "target_dir": "",
            "output_template": "%(id)s.%(ext)s",
            "media_files": ["runs/demo/media/BV1xx411c7mD.mp4"],
            "returncode": 0,
        }
        self.error = error

    def download_media(self, url, target_dir):
        if self.error is not None:
            raise self.error
        result = dict(self.result)
        result["target_dir"] = str(target_dir)
        return result


class PipelineTests(unittest.TestCase):
    def test_run_video_pipeline_writes_failure_audit_chain(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe(
                    {
                        "title": "Skill Demo",
                        "uploader": "Teacher",
                        "duration": 120,
                        "subtitles": {"zh-CN": [{"url": "https://example.test/subtitle.json"}]},
                    }
                ),
                media_downloader=FakeMediaDownloader(),
            )

            run_dir = store.run_path(result.run_id)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.completed_stages, PIPELINE_STAGES)
            saved_manifest = read_json(run_dir / "manifest.json")
            metadata = read_json(run_dir / "metadata.json")
            media_probe = read_json(run_dir / "media_probe.json")
            media_extract = read_json(run_dir / "media_extract.json")
            self.assertEqual(saved_manifest["title"], "Skill Demo")
            self.assertEqual(saved_manifest["duration_sec"], 120)
            self.assertEqual(metadata["title"], "Skill Demo")
            self.assertEqual(metadata["author"], "Teacher")
            self.assertEqual(media_probe["status"], "metadata_available")
            self.assertEqual(media_extract["status"], "downloaded")
            self.assertIn("media", media_extract["target_dir"])
            self.assertIn("BV1xx411c7mD.mp4", media_extract["media_files"][0])
            self.assertTrue((run_dir / "media_extract.json").exists())
            self.assertTrue((run_dir / "frame_index.json").exists())
            self.assertTrue((run_dir / "asr.json").exists())
            self.assertTrue((run_dir / "vision_ocr.json").exists())
            self.assertTrue((run_dir / "evidence_timeline.json").exists())
            self.assertTrue((run_dir / "distillation.json").exists())
            self.assertTrue((run_dir / "score.json").exists())
            self.assertTrue((run_dir / "metadata.json").exists())
            self.assertTrue((run_dir / "failure_report.md").exists())

    def test_run_video_pipeline_records_metadata_probe_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe(
                    error=YtDlpError(
                        "failed https://example.test/tmp cookie=session",
                        code="metadata_probe_failed",
                        returncode=1,
                    )
                ),
                media_downloader=FakeMediaDownloader(),
            )

            run_dir = store.run_path(result.run_id)
            saved_manifest = read_json(run_dir / "manifest.json")
            media_probe = read_json(run_dir / "media_probe.json")
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.failure_reason, "metadata probe failed: metadata_probe_failed")
            self.assertIn("metadata_probe_failed", saved_manifest["risk_flags"])
            self.assertEqual(media_probe["status"], "failed")
            self.assertEqual(media_probe["returncode"], 1)
            self.assertIn("[redacted-url]", media_probe["summary"])
            self.assertNotIn("https://example.test/tmp", media_probe["summary"])
            self.assertNotIn("cookie=session", media_probe["summary"])

    def test_run_video_pipeline_records_media_download_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe(
                    {
                        "title": "Skill Demo",
                        "uploader": "Teacher",
                        "duration": 120,
                    }
                ),
                media_downloader=FakeMediaDownloader(
                    error=YtDlpError(
                        "download failed https://example.test/tmp token=x",
                        code="media_download_failed",
                        returncode=2,
                    )
                ),
            )

            run_dir = store.run_path(result.run_id)
            saved_manifest = read_json(run_dir / "manifest.json")
            media_extract = read_json(run_dir / "media_extract.json")
            self.assertEqual(result.failure_reason, "media extraction failed: media_download_failed")
            self.assertIn("media_download_failed", saved_manifest["risk_flags"])
            self.assertEqual(media_extract["status"], "failed")
            self.assertEqual(media_extract["returncode"], 2)
            self.assertIn("[redacted-url]", media_extract["summary"])
            self.assertNotIn("token=x", media_extract["summary"])

    def test_run_video_pipeline_downloads_with_enriched_manifest_on_resume(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            url = "https://www.bilibili.com/video/BV1xx411c7mD/"
            config = parse_config(CONFIG)
            store = RunStore(Path(temp_dir) / "runs")
            source = infer_source(url)
            manifest = build_initial_manifest(url, source)
            manifest.title = "Already fetched"
            manifest.risk_flags = []
            state = store.start_or_resume(source.source, source.source_id)
            store.save_manifest(state.run_id, manifest)
            store.run_path(state.run_id).mkdir(parents=True, exist_ok=True)
            write_json(
                store.run_path(state.run_id) / "media_extract.json",
                {"status": "skipped", "reason": "old placeholder"},
            )

            result = run_video_pipeline(
                config=config,
                store=store,
                state=state,
                manifest=manifest,
                out_dir=Path(temp_dir) / "skills",
                metadata_probe=FakeMetadataProbe({"title": "Should not be needed"}),
                media_downloader=FakeMediaDownloader(
                    {
                        "status": "downloaded",
                        "target_dir": "ignored",
                        "output_template": "%(id)s.%(ext)s",
                        "media_files": ["safe.mp4"],
                        "stderr": "must not be persisted",
                        "returncode": 0,
                    }
                ),
            )

            run_dir = store.run_path(result.run_id)
            media_probe = read_json(run_dir / "media_probe.json")
            media_extract = read_json(run_dir / "media_extract.json")
            self.assertEqual(media_probe["status"], "metadata_available")
            self.assertEqual(media_extract["status"], "downloaded")
            self.assertEqual(media_extract["media_files"], ["safe.mp4"])
            self.assertNotIn("stderr", media_extract)


if __name__ == "__main__":
    unittest.main()
