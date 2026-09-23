import copy
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

    def test_insufficient_status_with_only_current_citations_removes_past_claim(self):
        value = {**self.assessment, "basis": "Current observations support the current assessment.",
                 "evidence_ids": ["E-current"], "hypotheses": [{"status": "unresolved"}],
                 "historical_comparison": {"episode_id": "prior", "status": "insufficient_evidence",
                     "summary": "The prior episode involved a different failure.", "evidence_ids": ["E-current"]}}
        original = copy.deepcopy(value)
        result = ground_historical_comparison(value, self.checks, self.context)
        self.assertEqual(result["historical_comparison"]["status"], "insufficient_evidence")
        self.assertIn("A shared or different mechanism is not established", result["historical_comparison"]["summary"])
        self.assertEqual(result["historical_comparison"]["provenance"], "grounding_guard")
        self.assertEqual({key: item for key, item in result.items() if key != "historical_comparison"},
                         {key: item for key, item in original.items() if key != "historical_comparison"})
        self.assertEqual(value, original)
        self.assertEqual(ground_historical_comparison(result, self.checks, self.context), result)

    def test_insufficient_status_without_visible_matching_history_is_neutral(self):
        value = {**self.assessment, "historical_comparison": {
            **self.assessment["historical_comparison"], "status": "insufficient_evidence",
            "summary": "The prior episode involved a different failure."}}
        variants = [
            (self.checks, {"prior_checks": []}),
            ([], self.context),
            ([{**self.checks[0], "arguments": {"episode_id": "other"}}], self.context),
            (self.checks, {"prior_checks": [{"id": "Q2", "observation": {"observations": []}}]}),
            (self.checks, {"prior_checks": [{"id": "Q2", "observation": {
                **self.context["prior_checks"][0]["observation"], "availability": "unavailable"}}]}),
        ]
        for checks, context in variants:
            with self.subTest(checks=checks, context=context):
                result = ground_historical_comparison(value, checks, context)
                self.assertEqual(result["historical_comparison"]["provenance"], "grounding_guard")
                self.assertIn("did not cite visible retained observations", result["historical_comparison"]["summary"])
                self.assertEqual(result["summary"], value["summary"])

    def test_insufficient_status_with_cited_visible_prior_facts_is_preserved(self):
        value = {**self.assessment, "historical_comparison": {
            **self.assessment["historical_comparison"], "status": "insufficient_evidence",
            "summary": "Prior retained queue depth reached 42; a shared mechanism is not established."}}
        self.assertEqual(ground_historical_comparison(value, self.checks, self.context), value)


if __name__ == "__main__":
    unittest.main()
