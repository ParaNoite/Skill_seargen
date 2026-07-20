import tempfile
import unittest
from pathlib import Path

from skill_gather.adapters.bilibili import build_initial_manifest
from skill_gather.config import parse_config
from skill_gather.models import PIPELINE_STAGES
from skill_gather.pipeline import run_video_pipeline
from skill_gather.runs import RunStore
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
            )

            run_dir = store.run_path(result.run_id)
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.completed_stages, PIPELINE_STAGES)
            self.assertTrue((run_dir / "media_extract.json").exists())
            self.assertTrue((run_dir / "frame_index.json").exists())
            self.assertTrue((run_dir / "asr.json").exists())
            self.assertTrue((run_dir / "vision_ocr.json").exists())
            self.assertTrue((run_dir / "evidence_timeline.json").exists())
            self.assertTrue((run_dir / "distillation.json").exists())
            self.assertTrue((run_dir / "score.json").exists())
            self.assertTrue((run_dir / "metadata.json").exists())
            self.assertTrue((run_dir / "failure_report.md").exists())


if __name__ == "__main__":
    unittest.main()
