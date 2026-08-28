from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skill_gather.models import TopicBudget, TopicSourceCandidate
from skill_gather.topics import TopicRunStore


class TopicExternalSourceTests(unittest.TestCase):
    def test_select_can_confirm_public_urls_for_same_run(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TopicRunStore(Path(directory))
            task = store.start_or_resume(
                "Three.js runner",
                mode="technical",
                budget=TopicBudget(max_selected_sources=4),
            )
            task = store.begin_search(task.run_id)
            task = store.save_search_results(
                task.run_id,
                [TopicSourceCandidate(candidate_id="known", url="https://github.com/example/game", source_type="github")],
                search_audit={},
                warnings=[],
            )
            task = store.select_candidates(
                task.run_id,
                ["known"],
                ["https://github.com/RadhaKhatri/Car-Collision-Game", "https://freefrontend.com/three-js-games/"],
            )
            self.assertEqual(task.status, "processing_sources")
            self.assertEqual([item.source_type for item in task.selected_sources], ["github", "github", "web"])
            self.assertTrue(task.selected_sources[1].candidate_id.startswith("external-"))


if __name__ == "__main__":
    unittest.main()
