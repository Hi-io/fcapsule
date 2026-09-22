import json
import unittest

from fcapsule.reasoning.context_budget import _log_observation, _workload_observation, compact_for_model, estimate_tokens


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

    def test_compaction_prioritizes_error_signals_and_keeps_required_observations(self):
        context = {"episode_id": "episode-logs", "evidence": [{"id": "E1", "summary": "Crash loop alert"}], "alerts": []}
        checks = [
            {
                "id": "Q1", "tool": "workload_state", "status": "completed", "required_observation": True,
                "result": {"source": "Kubernetes API", "observations": [{
                    "kind": "PodSpec", "name": "worker", "phase": "Running", "ready": False,
                    "resources": [{"name": "worker", "limits": {"memory": "160Mi"}}],
                    "container_states": [{"name": "worker", "restart_count": 4,
                                          "last_state": {"terminated": {"reason": "Error", "exitCode": 1}}}],
                }]},
            },
            {
                "id": "Q2", "tool": "search_logs", "status": "completed", "required_observation": True,
                "result": {"source": "OpenSearch", "scanned_lines": 101, "matching_patterns": 2, "patterns": [
                    {"pattern": "Worker scheduler heartbeat", "count": 100,
                     "examples": [{"level": "INFO", "message": "Worker scheduler heartbeat"}]},
                    {"pattern": "Import decoder rejected document", "count": 1,
                     "examples": [{"message": json.dumps({"level": "ERROR", "message": "Import decoder rejected document", "error": "Only base64 data is allowed"})}]},
                ]},
            },
        ]

        compact, visible = compact_for_model(context, checks, max_prompt_tokens=1800)

        self.assertIn("Q1", visible)
        self.assertIn("Q2", visible)
        log_check = next(item for item in compact["prior_checks"] if item["id"] == "Q2")
        signal = log_check["observation"]["top_signal"]
        self.assertEqual(signal["level"], "ERROR")
        self.assertEqual(signal["error"], "Only base64 data is allowed")

    def test_log_observation_prefers_less_frequent_operational_signal_over_heartbeat(self):
        observation = _log_observation({"patterns": [
            {"pattern": "Worker scheduler heartbeat", "count": 100,
             "examples": [{"level": "INFO", "message": '{"message":"Worker scheduler heartbeat"}'}]},
            {"pattern": "Export page encoded delivery buffered", "count": 12,
             "examples": [{"level": "INFO", "message": '{"message":"Export page encoded","buffered_bytes":141432000,"delivery":"buffered"}'}]},
        ]})

        self.assertEqual(observation["top_signal"]["message"], "Export page encoded")
        self.assertEqual(observation["top_signal"]["delivery"], "buffered")
        self.assertEqual(observation["top_signal"]["buffered_bytes"], "141432000")

    def test_revision_priority_keeps_new_operator_evidence_visible(self):
        context = {
            "episode_id": "episode-3",
            "evidence": [
                {"id": f"E{index:03d}", "summary": "existing evidence " + "x" * 300}
                for index in range(40)
            ] + [{
                "id": "A-new-image", "domain": "image_evidence", "summary": "Target table shows checkout metrics down.",
                "operator_context": {"note": "Captured after the alert."},
            }],
            "alerts": [],
        }

        compact, visible = compact_for_model(
            context, [], max_prompt_tokens=1200, priority_evidence_ids=["A-new-image"],
        )

        self.assertEqual(compact["evidence"][0]["id"], "A-new-image")
        self.assertTrue(compact["evidence"][0]["revision_priority"])
        self.assertIn("A-new-image", compact["priority_evidence_ids"])
        self.assertIn("A-new-image", visible)

    def test_diagnostic_log_evidence_survives_a_large_heartbeat_ledger(self):
        heartbeats = [
            {"id": f"E-heartbeat-{index}", "domain": "log_template",
             "title": "Worker scheduler heartbeat", "summary": "Routine healthy heartbeat."}
            for index in range(35)
        ]
        context = {
            "episode_id": "episode-priority",
            "evidence": [
                {"id": "E-alert", "domain": "alert", "title": "Worker OOMKilled",
                 "summary": "The worker was OOMKilled."},
                *heartbeats,
                {"id": "E-buffered", "domain": "log_template",
                 "title": "Export page encoded with buffered_bytes",
                 "summary": "Buffered export pages grew before the OOM termination."},
            ],
            "alerts": [],
        }

        compact, visible = compact_for_model(
            context, [], max_prompt_tokens=1200, priority_evidence_ids=["E-alert"],
        )

        self.assertEqual(compact["evidence"][0]["id"], "E-alert")
        self.assertIn("E-buffered", visible)
        self.assertLess(
            [item["id"] for item in compact["evidence"]].index("E-buffered"),
            [item["id"] for item in compact["evidence"]].index("E-heartbeat-0"),
        )

    def test_extreme_budget_reaches_a_stable_minimum_context(self):
        context = {
            "episode_id": "episode-minimum",
            "evidence": [{"id": "E1", "domain": "log_template", "title": "Long title " * 20,
                          "summary": "Long diagnostic summary " * 80}],
            "alerts": [],
        }

        compact, visible = compact_for_model(context, [], max_prompt_tokens=80)

        self.assertLessEqual(estimate_tokens(compact), 80)
        self.assertEqual(visible, ["E1"])
        self.assertLessEqual(len(compact["evidence"][0].get("summary", "")), 60)

    def test_workload_observation_keeps_safe_configuration_and_termination_time(self):
        observation = _workload_observation({
            "observations": [
                {
                    "kind": "PodSpec",
                    "ready": True,
                    "resources": [{"limits": {"memory": "160Mi"}}],
                    "container_states": [{"restart_count": 3, "last_state": {"terminated": {
                        "reason": "OOMKilled", "exitCode": 137, "finishedAt": "2026-09-23T10:00:00Z",
                    }}}],
                },
                {
                    "kind": "ConfigMap",
                    "name": "orders-config",
                    "data": {
                        "INVENTORY_URL": "http://inventory-api:8099",
                        "INVENTORY_TIMEOUT_SECONDS": "0.05",
                        "REQUEST_KEY_ID": "checkout-key-v1",
                        "MYSQL_PASSWORD": "never-send-this",
                        "lab.cnf": "[mysqld]\nmax_connections=40\npassword=never-send-this",
                    },
                },
            ],
        })

        self.assertEqual(observation["workloads"][0]["last_termination"]["finished_at"], "2026-09-23T10:00:00Z")
        values = observation["configuration"][0]["values"]
        self.assertEqual(values["INVENTORY_URL"], "http://inventory-api:8099")
        self.assertEqual(values["REQUEST_KEY_ID"], "checkout-key-v1")
        self.assertIn("max_connections=40", values["lab.cnf"])
        self.assertNotIn("MYSQL_PASSWORD", values)
        self.assertNotIn("never-send-this", json.dumps(values))


if __name__ == "__main__":
    unittest.main()
