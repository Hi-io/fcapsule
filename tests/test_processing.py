import unittest
from dataclasses import replace

from fcapsule.io.case_loader import load_case
from fcapsule.processing.anonymizer import anonymize_text, diagnostic_fields, template_for_message
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
        self.assertNotIn("db-password", anonymize_text("mysql://reader:db-password@db.internal:3306/inventory"))

    def test_template_masks_variable_tokens(self):
        template = template_for_message("Failed to connect to 10.0.0.3 after 3 retries")
        self.assertEqual(template, "Failed to connect to <IP> after <NUM> retries")

    def test_identifier_pseudonyms_preserve_relations_without_retaining_raw_values(self):
        identifier = "a9c74440-635f-4ca3-99a1-c989391fb843"
        first = anonymize_text(f"request {identifier}")
        second = anonymize_text(f"retry {identifier}")

        self.assertIn("<REF:", first)
        self.assertEqual(first.rsplit(" ", 1)[1], second.rsplit(" ", 1)[1])
        self.assertNotIn(identifier, first)
        self.assertEqual(
            template_for_message(f"request {identifier}"),
            template_for_message("request 07a03c00-2e2f-4471-90cb-42b31db92544"),
        )

    def test_structured_diagnostics_are_allowlisted_redacted_and_template_safe(self):
        first_fields = diagnostic_fields(None, {
            "error": {"code": "ECONNRESET", "message": "password=do-not-keep"},
            "http": {"response": {"status_code": 503}},
            "request": {"id": "request-1001"},
            "api_key": "do-not-keep-either",
            "payload": {"customer_name": "not-a-diagnostic-field"},
        })
        second_fields = diagnostic_fields(None, {
            "request_id": "request-1002", "error_code": "ECONNRESET", "status_code": 503,
            "error_message": "password=do-not-keep",
        })

        self.assertEqual(first_fields["error_code"], "ECONNRESET")
        self.assertEqual(first_fields["status_code"], "503")
        self.assertNotEqual(first_fields["request_id"], second_fields["request_id"])
        self.assertTrue(first_fields["request_id"].startswith("<REF:"))
        self.assertNotIn("do-not-keep", repr(first_fields))
        self.assertNotIn("customer_name", repr(first_fields))
        bounded = diagnostic_fields(None, {f"request_{index}_id": f"id-{index}" for index in range(30)})
        self.assertLessEqual(len(bounded), 12)
        self.assertTrue(all(value.startswith("<REF:") for value in bounded.values()))
        self.assertEqual(
            template_for_message("request failed", first_fields),
            template_for_message("request failed", second_fields),
        )

    def test_reduced_linked_entities_use_same_opaque_identifier(self):
        identifier = "a9c74440-635f-4ca3-99a1-c989391fb843"
        events = [
            {"@timestamp": "2026-09-20T00:00:00Z", "message": "retry " + identifier,
             "level": "ERROR", "cncc_uuid": identifier},
            {"@timestamp": "2026-09-20T00:00:01Z", "message": "retry " + identifier,
             "level": "ERROR", "cncc_uuid": identifier},
        ]

        templates = reduce_logs(replace(self.bundle, logs=events))

        self.assertEqual(len(templates), 1)
        linked = templates[0]["linked_entities"]
        self.assertIn(anonymize_text(identifier), linked)
        self.assertNotIn(identifier, repr(templates))

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

    def test_metric_analysis_falls_back_when_alert_is_outside_sample_window(self):
        alerts = [{**self.bundle.alerts[0], "startsAt": "2099-01-01T00:00:00Z"}]
        shifted_bundle = replace(self.bundle, alerts=alerts)

        metrics = analyze_metrics(shifted_bundle)

        self.assertEqual(len(metrics), len(self.bundle.metrics))
        self.assertTrue(all(item["peak_timestamp"].endswith("Z") for item in metrics))

    def test_two_sample_counter_has_a_valid_incident_point(self):
        counter = next(item for item in self.bundle.metrics if str(item["metric"]).endswith("_total"))
        metrics = [{**counter, "values": counter["values"][:2]}]
        alerts = [{**self.bundle.alerts[0], "startsAt": "2099-01-01T00:00:00Z"}]
        minimal_bundle = replace(self.bundle, metrics=metrics, alerts=alerts)

        result = analyze_metrics(minimal_bundle)

        self.assertEqual(result[0]["analysis_mode"], "counter_delta")
        self.assertTrue(result[0]["peak_timestamp"].endswith("Z"))


if __name__ == "__main__":
    unittest.main()
