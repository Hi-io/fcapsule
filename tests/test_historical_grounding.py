import unittest

from fcapsule.episode_investigation import ground_historical_comparison
from fcapsule.reasoning.context_budget import _minimal_check_observation


class HistoricalGroundingTests(unittest.TestCase):
    def setUp(self):
        self.assessment = {"summary": "Current failure", "historical_comparison": {
            "episode_id": "prior", "status": "similar_mechanism", "summary": "Similar retained signature",
            "evidence_ids": ["Q2"],
        }}
        self.checks = [{"id": "Q2", "tool": "historical_episode", "status": "completed",
                        "arguments": {"episode_id": "prior"}}]
        self.context = {"prior_checks": [{"id": "Q2", "observation": {
            "observations": [{"metric_observation": {"metric": "queue_depth", "condition": {"max": 42}}}],
            "availability": "retained",
        }}]}

    def test_visible_selected_history_remains_a_tentative_model_comparison(self):
        result = ground_historical_comparison(self.assessment, self.checks, self.context)
        self.assertEqual(result, self.assessment)

    def test_wrong_episode_missing_facts_or_only_current_citation_abstains(self):
        variants = [
            ([{**self.checks[0], "arguments": {"episode_id": "different"}}], self.context, ["Q2"]),
            (self.checks, {"prior_checks": []}, ["Q2"]),
            (self.checks, {"prior_checks": [{"id": "Q2", "observation": "Truncated metadata"}]}, ["Q2"]),
            (self.checks, self.context, ["E-current"]),
        ]
        for checks, context, refs in variants:
            with self.subTest(refs=refs, context=context):
                value = {**self.assessment, "historical_comparison": {
                    **self.assessment["historical_comparison"], "evidence_ids": refs}}
                result = ground_historical_comparison(value, checks, context)
                self.assertEqual(result["historical_comparison"]["status"], "insufficient_evidence")
                self.assertEqual(result["historical_comparison"]["provenance"], "grounding_guard")
                self.assertEqual(result["summary"], value["summary"])
                self.assertEqual(value["historical_comparison"]["status"], "similar_mechanism")

    def test_small_history_keeps_facts_and_compaction_is_idempotent(self):
        check = {"tool": "historical_episode", "observation": self.context["prior_checks"][0]["observation"]}
        result = _minimal_check_observation(check)
        self.assertEqual(result["observations"][0]["metric_observation"]["condition"]["max"], 42)
        self.assertEqual(_minimal_check_observation({**check, "observation": result}), result)
        self.assertEqual(_minimal_check_observation({"observation": "Already shortened"}), "Already shortened")


if __name__ == "__main__":
    unittest.main()
