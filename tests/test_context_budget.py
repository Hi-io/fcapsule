import json
import unittest

from fcapsule.reasoning.context_budget import compact_for_model, estimate_tokens


class ContextBudgetTests(unittest.TestCase):
    def test_compaction_keeps_citable_ids_within_budget(self):
        context = {
            "episode_id": "episode-1",
            "alerts": [{"incident_id": "incident-1", "alertname": "HighMemory", "labels": {"pod": "worker"}}],
            "impact": [{"summary": "Slow requests " + "x" * 900} for _ in range(8)],
            "evidence": [
                {
                    "id": f"E{index:03d}",
                    "domain": "log_template",
                    "title": "Representative log",
                    "summary": "A diagnostic event " + "y" * 900,
                    "examples": [{"line": "exception " + "z" * 900}],
                }
                for index in range(28)
            ],
        }
        checks = [
            {"id": f"Q{index:03d}", "tool": "search_logs", "status": "completed",
             "question": "What changed?", "distinguishes": "Two causes",
             "result": {"observations": [{"message": "detail " + "q" * 1000} for _ in range(8)]}}
            for index in range(6)
        ]

        compact, visible = compact_for_model(context, checks, max_prompt_tokens=1200)

        self.assertLessEqual(estimate_tokens(compact), 1200)
        self.assertIn("E000", visible)
        self.assertIn("Q005", visible)
        self.assertEqual(compact["prior_checks"][-1]["id"], "Q005")
        self.assertNotIn("q" * 500, json.dumps(compact))

    def test_visible_ids_only_include_shown_completed_checks(self):
        context = {"episode_id": "episode-2", "evidence": [{"id": "E1"}], "alerts": []}
        checks = [
            {"id": "Q1", "tool": "workload_state", "status": "completed", "result": {"value": 1}},
            {"id": "Q2", "tool": "search_logs", "status": "unavailable", "result": {"error": "unavailable"}},
        ]

        _, visible = compact_for_model(context, checks)

        self.assertIn("E1", visible)
        self.assertIn("Q1", visible)
        self.assertNotIn("Q2", visible)


if __name__ == "__main__":
    unittest.main()
