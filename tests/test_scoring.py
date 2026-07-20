import unittest

from skill_gather.scoring import conservative_score


class ScoringTests(unittest.TestCase):
    def test_uses_more_conservative_score(self):
        result = conservative_score(rule_score=90, llm_judge_score=72)

        self.assertEqual(result.final_score, 72)
        self.assertEqual(result.final_status, "needs_review")

    def test_caps_single_channel_evidence_at_review(self):
        result = conservative_score(
            rule_score=95,
            llm_judge_score=92,
            single_channel_evidence=True,
        )

        self.assertEqual(result.final_status, "needs_review")


if __name__ == "__main__":
    unittest.main()
