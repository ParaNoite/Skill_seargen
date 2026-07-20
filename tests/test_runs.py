import tempfile
import unittest
from pathlib import Path

from skill_gather.runs import RunStore


class RunStoreTests(unittest.TestCase):
    def test_start_or_resume_reuses_source_id_run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RunStore(temp_dir)

            first = store.start_or_resume("bilibili", "BV1xx411c7mD")
            second = store.start_or_resume("bilibili", "BV1xx411c7mD")

            self.assertEqual(first.run_id, second.run_id)
            self.assertTrue((Path(temp_dir) / first.run_id / "run_state.json").exists())


if __name__ == "__main__":
    unittest.main()
