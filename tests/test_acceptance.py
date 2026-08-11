import json
import tempfile
import unittest
from pathlib import Path

from skill_gather.acceptance import REQUIRED_COVERAGE, run_offline_acceptance


class V10AcceptanceTests(unittest.TestCase):
    def test_fixed_dataset_runs_all_ten_topics_offline(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            report = run_offline_acceptance(
                root / "benchmarks" / "v1.0-topics.json",
                config_path=root / "configs" / "skill-gather.example.json",
                runs_path=Path(temp_dir) / "runs",
            )

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["case_count"], 10)
        self.assertEqual(report["passed_count"], 10)
        self.assertTrue(report["synthetic"])
        self.assertEqual(set(report["coverage"]), REQUIRED_COVERAGE)
        self.assertTrue(all(item["candidate_count"] > 0 for item in report["results"]))

    def test_dataset_declares_exactly_ten_unique_cases(self) -> None:
        root = Path(__file__).resolve().parents[1]
        payload = json.loads((root / "benchmarks" / "v1.0-topics.json").read_text(encoding="utf-8"))
        ids = [item["case_id"] for item in payload["cases"]]

        self.assertEqual(len(ids), 10)
        self.assertEqual(len(set(ids)), 10)


if __name__ == "__main__":
    unittest.main()
