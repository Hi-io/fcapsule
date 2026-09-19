import unittest

from fcapsule.attention.evidence_scorer import score_evidence
from fcapsule.attention.evidence_selector import select_evidence
from fcapsule.io.case_loader import load_case
from fcapsule.processing.log_reducer import reduce_logs
from fcapsule.processing.metrics_analyzer import analyze_metrics
from fcapsule.reasoning.hypothesis_generator import generate_hypotheses
from fcapsule.reasoning.hypothesis_verifier import verify_hypotheses
from tests.common import REFERENCE_CASE


class AttentionReasoningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_case(REFERENCE_CASE)
        cls.candidates = score_evidence(cls.bundle, reduce_logs(cls.bundle), analyze_metrics(cls.bundle))
        cls.selected, _ = select_evidence(cls.candidates)

    def test_scores_expose_intermediate_components(self):
        self.assertGreater(len(self.candidates), 3)
        for item in self.candidates:
            self.assertIn("score_components", item)
            self.assertIn("entity_match_score", item["score_components"])

    def test_selection_preserves_each_domain(self):
        types = {item["type"] for item in self.selected}
        domains = {item["domain"] for item in self.selected}
        self.assertTrue({"alert", "log_template", "metric_anomaly"}.issubset(types))
        self.assertTrue({"fault_events", "log_text", "time_series_metrics"}.issubset(domains))

    def test_generated_hypotheses_are_fully_grounded(self):
        hypotheses = generate_hypotheses(self.selected)
        verified = verify_hypotheses(hypotheses, self.selected)
        self.assertGreaterEqual(len(verified), 2)
        self.assertTrue(all(item["verdict"] == "plausible" for item in verified))
        self.assertTrue(all(item["supporting_evidence"] for item in verified))

    def test_configuration_mismatch_is_prioritized_over_retry_symptoms(self):
        selected = [
            {"evidence_id": "ev_alert", "type": "alert", "title": "Pod restarted", "summary": "Restart detected", "score": 0.8},
            {"evidence_id": "ev_schema", "type": "log_template", "title": "schema epoch mismatch", "summary": "unsafe-write prevented", "score": 0.9},
            {"evidence_id": "ev_retry", "type": "log_template", "title": "dependency retry", "summary": "attempt failed", "score": 0.7},
            {"evidence_id": "ev_pool", "type": "log_template", "title": "pool lock", "summary": "pool exhausted", "score": 0.7},
            {"evidence_id": "ev_config", "type": "configuration", "title": "ConfigMap checkout", "summary": "runtime config", "score": 0.8},
            {"evidence_id": "ev_metric", "type": "metric_anomaly", "title": "retry_total", "summary": "retry increased", "score": 0.7},
        ]

        hypotheses = generate_hypotheses(selected)

        self.assertIn("configuration mismatch", hypotheses[0]["hypothesis"].lower())
        self.assertIn("ev_config", hypotheses[0]["supporting_evidence"])

    def test_verifier_rejects_unknown_evidence(self):
        hypothesis = {
            "hypothesis_id": "hyp_bad",
            "hypothesis": "Unsupported claim",
            "confidence": 0.9,
            "supporting_evidence": ["ev_missing"],
            "missing_evidence": [],
        }
        result = verify_hypotheses([hypothesis], self.selected)[0]
        self.assertEqual(result["verdict"], "unsupported")
        self.assertLess(result["adjusted_confidence"], 0.3)


if __name__ == "__main__":
    unittest.main()
