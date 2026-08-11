from __future__ import annotations

import tempfile
import unittest

from skill_gather.planning import assess_ambiguity, build_semantic_plan
from skill_gather.topics import TopicRunStore


class PlanningTests(unittest.TestCase):
    def test_llm_intent_enriches_plan_without_saving_raw_response(self):
        class Client:
            def build_search_intent(self, topic, mode, model):
                return {"goal": "明确目标", "facets": ["维度A"], "exclusions": ["排除A"], "queries": ["查询A"]}

        with tempfile.TemporaryDirectory() as directory:
            task = TopicRunStore(directory).start_or_resume("教程")
            plan = build_semantic_plan(task, Client(), "model")
            self.assertEqual(plan.generation_method, "newapi")
            self.assertEqual(plan.options[0].goal, "明确目标")
            self.assertNotIn("raw", plan.to_dict())

    def test_ambiguous_topic_waits_for_semantic_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TopicRunStore(directory)
            task = store.start_or_resume("教程")
            planned = store.create_plan(task.run_id)
            self.assertTrue(assess_ambiguity("教程").ambiguous)
            self.assertEqual(planned.status, "awaiting_plan_confirmation")
            self.assertGreaterEqual(len(planned.plan.options), 2)
            confirmed = store.confirm_plan(task.run_id, planned.plan.recommended_option_id)
            self.assertEqual(confirmed.status, "created")
            self.assertEqual(confirmed.plan.audit_status, "confirmed")

    def test_plan_interruption_persists_conservative_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TopicRunStore(directory)
            task = store.start_or_resume("AI", execution_mode="auto")
            interrupted = store.interrupt_plan(task.run_id)
            self.assertEqual(interrupted.plan.warning, "plan_interrupted")
            self.assertEqual(interrupted.plan.generation_method, "deterministic")
            self.assertEqual(interrupted.plan_audit[-1]["event"], "plan_interrupted")


if __name__ == "__main__":
    unittest.main()
