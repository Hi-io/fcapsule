import json
import unittest
from unittest.mock import patch

from fcapsule.reasoning.incident_briefing import build_briefing_prompt, generate_incident_briefing


class IncidentBriefingTests(unittest.TestCase):
    def setUp(self):
        self.report = {
            "incident": {"title": "Checkout failure", "service": "checkout-api", "summary": "Retries increased."},
            "primary_hypothesis": {"statement": "Retries may be increasing pool pressure.", "uncertainty": ["Lock ownership is unknown."]},
            "actions": [{"action": "Retrieve traces"}],
            "fault_alerts": [{"evidence_id": "ev_alert_001", "name": "Pool saturation", "description": "Pool is full."}],
            "pm_signals": [{"evidence_id": "ev_metric_001", "label": "Error rate", "baseline": "0%", "peak": "45%"}],
            "log_patterns": [{"evidence_id": "ev_log_template_001", "pattern": "Retry exhausted", "summary": "200 matches"}],
            "supporting_evidence": [
                {
                    "evidence_id": "ev_config_001",
                    "type": "configuration",
                    "title": "ConfigMap checkout-config",
                    "summary": "Two runtime keys were retained.",
                    "configuration": {"data": {"SCHEMA_EPOCH": "41", "REQUIRED_EPOCH": "42"}},
                }
            ],
        }

    def test_prompt_includes_retained_configuration_values(self):
        prompt = build_briefing_prompt(self.report)

        self.assertIn("ev_config_001", prompt[1]["content"])
        self.assertIn("SCHEMA_EPOCH", prompt[1]["content"])

    @patch("fcapsule.reasoning.incident_briefing.DeepSeekChatClient")
    def test_valid_cited_response_is_retained(self, client_class):
        client_class.return_value.chat.return_value = {
            "content": json.dumps(
                {
                    "operator_brief": "Retries and pool saturation align with failed checkout requests.",
                    "first_action": "Retrieve failed-request traces before source retention expires.",
                    "why_this_first": "Traces can confirm the lock and dependency path while they remain available.",
                    "evidence_ids": ["ev_alert_001", "ev_metric_001", "ev_log_template_001"],
                    "uncertainty": "The database lock owner is not retained in the capsule.",
                }
            ),
            "latency_seconds": 0.4,
        }

        result = generate_incident_briefing(self.report)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["briefing"]["evidence_ids"], ["ev_alert_001", "ev_metric_001", "ev_log_template_001"])

    @patch("fcapsule.reasoning.incident_briefing.DeepSeekChatClient")
    def test_uncited_or_unknown_response_is_rejected(self, client_class):
        client_class.return_value.chat.return_value = {
            "content": json.dumps(
                {
                    "operator_brief": "The root cause is known.",
                    "first_action": "Restart the service.",
                    "why_this_first": "It will fix it.",
                    "evidence_ids": ["invented_001", "invented_002"],
                    "uncertainty": "None.",
                }
            ),
            "latency_seconds": 0.4,
        }

        result = generate_incident_briefing(self.report)

        self.assertEqual(result["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
