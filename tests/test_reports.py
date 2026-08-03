import tempfile
import unittest
from pathlib import Path

from skill_gather.reports import build_reliability_report, build_vision_report
from skill_gather.runs import write_json


class ReportTests(unittest.TestCase):
    def test_reliability_report_keeps_missing_samples_visible(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "bilibili-BVdone"
            run_dir.mkdir()
            write_json(
                run_dir / "run_state.json",
                {
                    "status": "failed",
                    "completed_stages": ["manifest", "distill"],
                },
            )
            write_json(
                run_dir / "model_audit.json",
                {
                    "distillation": {
                        "attempts": [
                            {"status": "failed", "reason_code": "invalid_distillation_json"},
                            {"status": "succeeded"},
                        ]
                    }
                },
            )
            write_json(run_dir / "cli_result.json", {"exit_code": 1})
            report = build_reliability_report(
                {
                    "benchmark_id": "v0.2",
                    "videos": [
                        {"source_id": "BVdone"},
                        {"source_id": "BVpending"},
                    ],
                },
                root,
            )

            self.assertEqual(report["executed_count"], 1)
            self.assertEqual(report["not_run_count"], 1)
            self.assertEqual(report["retry_count"], 1)
            self.assertEqual(report["retry_success_count"], 1)
            self.assertEqual(report["nonzero_exit_code_coverage"], 1.0)
            self.assertEqual(report["model_audit_coverage"], 1.0)

    def test_vision_report_compares_calls_timings_and_expected_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "sampled"
            run_dir.mkdir()
            write_json(
                run_dir / "vision_ocr.json",
                {
                    "strategy": "sampled",
                    "source_frame_count": 20,
                    "remote_call_count": 4,
                    "items": [
                        {
                            "observations": [
                                {"claim": "Run pip install", "raw_excerpt": "python -m pip install -e ."}
                            ]
                        }
                    ],
                },
            )
            write_json(
                run_dir / "stage_timings.json",
                {"stages": [{"stage": "vision_ocr", "duration_ms": 1250}]},
            )

            report = build_vision_report(
                [run_dir],
                ["python -m pip install -e .", "pytest"],
            )

            run = report["runs"][0]
            self.assertEqual(run["remote_call_count"], 4)
            self.assertEqual(run["vision_duration_ms"], 1250)
            self.assertEqual(run["field_accuracy"], 0.5)


if __name__ == "__main__":
    unittest.main()
