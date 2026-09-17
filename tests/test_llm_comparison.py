import json
import tempfile
import unittest
from pathlib import Path

from fcapsule.pipeline import investigate_case
from fcapsule.reasoning.model_comparator import _extract_json, _score_response, build_model_prompt
from fcapsule.ui.dashboard import render_dashboard
from tests.common import REFERENCE_CASE


class LLMComparisonTests(unittest.TestCase):
    def test_prompt_and_scoring_require_domains_and_valid_citations(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            investigate_case(REFERENCE_CASE, output)
            capsule = json.loads((output / "capsule.json").read_text(encoding="utf-8"))
            prompt = build_model_prompt(capsule)
            self.assertIn("fault_events", prompt[1]["content"])
            parsed = {
                "incident_summary": "CheckoutHighErrorRate affects checkout-service and the checkout pod.",
                "primary_hypothesis": {
                    "claim": "A dependency failure may explain checkout failed 503 responses and error rate changes; this is not a final root cause.",
                    "confidence": 0.74,
                    "supporting_evidence_ids": ["ev_alert_001", "ev_log_template_003", "ev_metric_003"],
                    "domain_reasoning": {
                        "fault_events": "Alert fired.",
                        "log_text": "Checkout failed and dependency errors are grouped.",
                        "time_series_metrics": "request_error_rate and checkout_errors_total increased.",
                        "topology_metadata": "The same service, namespace, and pod align the signals.",
                        "trace_access": "Traces are queryable during source retention and raw spans are not retained.",
                    },
                    "contradictions_or_limits": ["Missing trace and dependency health evidence."],
                },
                "alternative_hypotheses": [],
                "next_checks": ["Inspect dependency health.", "Check traces.", "Break down per-endpoint latency."],
                "retention_value": "The capsule keeps evidence after raw logs expire.",
            }
            score = _score_response(parsed, json.dumps(parsed), capsule)
            self.assertGreaterEqual(score["total_score"], 0.85)
            self.assertEqual(score["domain_score"], 1.0)
            self.assertEqual(score["citation_score"], 1.0)
            self.assertGreater(score["evidence_depth_score"], 0.0)
            self.assertGreater(score["signal_depth_score"], 0.0)

    def test_json_extraction_and_dashboard_rendering(self):
        parsed, error = _extract_json("```json\n{\"ok\": true}\n```")
        self.assertIsNone(error)
        self.assertEqual(parsed, {"ok": True})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            investigate_case(REFERENCE_CASE, output)
            dashboard = render_dashboard(output)
            self.assertTrue(dashboard.exists())
            self.assertIn("Operational Signal Domains", dashboard.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
