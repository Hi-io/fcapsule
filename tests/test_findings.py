import unittest

from fcapsule.reasoning.findings import derive_findings


class FindingTests(unittest.TestCase):
    def test_exact_pod_monitor_mismatch_is_prioritized_over_model_explanation(self):
        state = {
            "checks": [{
                "id": "Q002", "tool": "scrape_discovery", "status": "completed", "result": {
                    "scope": {"namespace": "lab", "pods": ["worker-1"], "workload": "worker"},
                    "active_targets": [], "dropped_targets": [],
                    "current_pod_labels": [{"pod": "worker-1", "labels": {"metrics": "enabeld"}}],
                    "monitor_selection": [{
                        "monitor": {"kind": "PodMonitor", "namespace": "monitoring", "name": "lab", "match_labels": {"metrics": "enabled"}},
                        "matched_pods": [], "matched_services": [],
                    }],
                },
            }],
            "assessment": {"likely_mechanism": "The monitoring selector may exclude this workload.", "evidence_ids": ["Q002"], "next_action": "Verify labels."},
        }

        findings = derive_findings(state)

        self.assertEqual(findings[0]["state"], "observed")
        self.assertEqual(findings[0]["category"], "monitoring_selection")
        self.assertIn("enabeld", findings[0]["summary"])
        self.assertEqual(findings[-1]["state"], "likely_explanation")

    def test_down_target_and_alert_rule_remain_separate_observations(self):
        state = {"checks": [
            {"id": "Q001", "tool": "scrape_discovery", "status": "completed", "result": {
                "scope": {"namespace": "lab"}, "dropped_targets": [], "monitor_selection": [], "current_pod_labels": [],
                "active_targets": [{"pod": "api-1", "health": "down", "last_error": "connection refused"}],
            }},
            {"id": "Q002", "tool": "alert_rule_logic", "status": "completed", "result": {
                "current_definitions": {"LabTargetDown": {"query": "up == 0"}},
            }},
        ]}

        findings = derive_findings(state)

        self.assertEqual([item["category"] for item in findings], ["scrape_health", "alert_logic"])
        self.assertIn("connection refused", findings[0]["summary"])
        self.assertIn("not the underlying cause", findings[1]["summary"])

    def test_exact_active_target_outweighs_an_unrelated_dropped_target(self):
        state = {"checks": [{
            "id": "Q002", "tool": "scrape_discovery", "status": "completed", "result": {
                "scope": {"namespace": "lab", "pods": ["exporter-1"], "workload": "metrics-exporter"},
                "discovery_targets": {"target_service": "metrics-exporter", "target_workload": "metrics-exporter"},
                "active_targets": [{
                    "state": "active", "health": "down", "last_error": "HTTP 404 Not Found",
                    "scrape_pool": "serviceMonitor/observability/exporter/0", "scrape_path": "/metrics-v2",
                    "service": "metrics-exporter", "pod": "exporter-1",
                }],
                "dropped_targets": [
                    {"state": "dropped", "scrape_pool": "serviceMonitor/observability/applications/0",
                     "service": "metrics-exporter", "pod": "exporter-1"},
                    {"state": "dropped", "scrape_pool": "serviceMonitor/observability/applications/0",
                     "service": "inventory-api", "pod": "inventory-1"},
                ],
                "current_pod_labels": [],
                "monitor_selection": [{
                    "monitor": {"kind": "ServiceMonitor", "namespace": "observability", "name": "applications",
                                "match_labels": {"metrics": "enabled"}},
                    "matched_services": ["app-metrics"], "matched_pods": [],
                }],
            },
        }]}

        findings = derive_findings(state)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["category"], "scrape_health")
        self.assertIn("HTTP 404", findings[0]["summary"])
        self.assertEqual(findings[0]["observations"][0]["scrape_path"], "/metrics-v2")


if __name__ == "__main__":
    unittest.main()
