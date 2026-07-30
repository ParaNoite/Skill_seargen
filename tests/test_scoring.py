import unittest

from skill_gather.scoring import conservative_score, normalize_judge_difficulty


class ScoringTests(unittest.TestCase):
    def test_uses_more_conservative_score(self):
        result = conservative_score(rule_score=90, llm_judge_score=72)

        self.assertEqual(result.final_score, 72)
        self.assertEqual(result.final_status, "needs_review")

    def test_judge_difficulty_changes_thresholds(self):
        self.assertEqual(conservative_score(82, 82, difficulty="lenient").final_status, "passed")
        self.assertEqual(conservative_score(82, 82, difficulty="standard").final_status, "needs_review")
        self.assertEqual(conservative_score(82, 82, difficulty="strict").final_status, "needs_review")
        self.assertEqual(conservative_score(75, 75, difficulty="strict").final_status, "failed")

    def test_caps_single_channel_evidence_at_review(self):
        result = conservative_score(
            rule_score=95,
            llm_judge_score=92,
            single_channel_evidence=True,
        )

        self.assertEqual(result.final_status, "needs_review")

    def test_accepts_disabled_judge_difficulty(self):
        self.assertEqual(normalize_judge_difficulty("off"), "off")


if __name__ == "__main__":
    unittest.main()
