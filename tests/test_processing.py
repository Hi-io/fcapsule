import unittest

from fcapsule.io.case_loader import load_case
from fcapsule.processing.anonymizer import anonymize_text, template_for_message
from fcapsule.processing.entity_resolver import resolve_entities
from fcapsule.processing.log_reducer import reduce_logs
from fcapsule.processing.metrics_analyzer import analyze_metrics
from tests.common import REFERENCE_CASE


class ProcessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_case(REFERENCE_CASE)

    def test_anonymizer_masks_sensitive_values(self):
        text = "user=a@example.com host=10.0.0.3 token=abc123 duration=42"
        masked = anonymize_text(text)
        self.assertNotIn("a@example.com", masked)
        self.assertNotIn("10.0.0.3", masked)
        self.assertNotIn("abc123", masked)
        self.assertIn("<EMAIL>", masked)
        self.assertIn("<IP>", masked)

    def test_template_masks_variable_tokens(self):
        template = template_for_message("Failed to connect to 10.0.0.3 after 3 retries")
        self.assertEqual(template, "Failed to connect to <IP> after <NUM> retries")

    def test_log_reducer_groups_repeated_failures(self):
        templates = reduce_logs(self.bundle)
        failure = next(item for item in templates if "Payment dependency" in item["template"])
        self.assertEqual(failure["count"], 40)
        self.assertIn("ERROR", failure["levels"])
        self.assertNotIn("10.0.0.3", failure["template"])

    def test_entity_resolution_has_cross_domain_coverage(self):
        result = resolve_entities(self.bundle)
        self.assertEqual(result["primary_entity"], "checkout-service")
        self.assertTrue(all(result["coverage"].values()))
        self.assertEqual(result["warnings"], [])

    def test_counter_analysis_uses_deltas(self):
        metrics = {item["metric"]: item for item in analyze_metrics(self.bundle)}
        self.assertEqual(metrics["http_requests_total"]["analysis_mode"], "counter_delta")
        self.assertLess(metrics["http_requests_total"]["anomaly_score"], 0.1)
        self.assertGreater(metrics["http_request_errors_total"]["anomaly_score"], 0.8)
        self.assertGreater(metrics["request_error_rate"]["anomaly_score"], 0.8)


if __name__ == "__main__":
    unittest.main()
