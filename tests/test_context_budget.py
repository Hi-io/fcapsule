import copy
import json
import unittest

from fcapsule.reasoning.context_budget import _log_observation, _workload_observation, compact_for_model, compact_metric_observation, estimate_tokens


class ContextBudgetTests(unittest.TestCase):
    def test_primary_scope_and_candidate_count_are_protected_under_pressure(self):
        context = {
            "episode_id": "scope-budget",
            "scope": {"pod": "specific-worker-1", "namespace": "specific-namespace", "cluster": "cluster",
                      "service": "worker", "alert_started_at": "2026-09-23T02:00:00Z",
                      "window": {"start": "2026-09-23T01:55:00Z", "end": "2026-09-23T02:05:00Z"}},
            "recurrence": {"previous_count": 8},
            "alerts": [{"incident_id": "one", "annotations": {"description": "Noisy alert. " * 100}}] * 12,
            "evidence": [{"id": f"E{index}", "summary": "Other details " * 100} for index in range(40)],
        }
        before = copy.deepcopy(context)
        for budget in (600, 900, 2100):
            with self.subTest(budget=budget):
                compact, visible = compact_for_model(context, [], max_prompt_tokens=budget)
                self.assertLessEqual(estimate_tokens(compact), budget)
                for field in ("pod", "namespace", "alert_started_at", "window"):
                    self.assertEqual(compact["scope"][field], context["scope"][field])
                self.assertEqual(compact["recurrence"]["previous_count"], 8)
                self.assertIn("not evidence of the same cause", compact["recurrence"]["limitation"])
                self.assertEqual(visible, [item["id"] for item in compact["evidence"]])
        self.assertEqual(context, before)

    def test_scope_and_recurrence_are_field_masked_even_for_direct_contexts(self):
        context = {
            "episode_id": "scope-mask", "evidence": [{"id": "E1"}],
            "scope": {"pod": "worker-1", "namespace": "ns", "service": "worker password=never-service",
                      "annotations": "never-annotations", "resource": {"kind": "Pod", "name": "worker-1", "private": "never-resource"},
                      "window": {"start": "2026-09-23T02:00:00Z", "secret": "never-window"}},
            "recurrence": {"previous_count": 20000, "prior_hypothesis": "never-cause", "limitation": "same cause proven"},
        }
        compact, _ = compact_for_model(context, [])
        self.assertNotIn("never-", json.dumps(compact))
        self.assertNotIn("same cause proven", json.dumps(compact))
        self.assertEqual(compact["recurrence"]["previous_count"], 9999)
        self.assertTrue(compact["recurrence"]["count_capped"])
        for scope in ({"namespace": "ns", "resource": {"kind": "Service", "name": "metrics"}},
                      {"namespace": "ns", "service": "metrics"},
                      {"namespace": "ns", "pod": "collector-pod", "resource": {"kind": "Node", "name": "worker-node"}}):
            compact, _ = compact_for_model({**context, "scope": scope}, [], max_prompt_tokens=300)
            self.assertEqual(compact["scope"], scope)
            self.assertLessEqual(estimate_tokens(compact), 300)

    def test_rule_metric_fields_and_missing_samples_remain_citable_at_low_budget(self):
        metric = {
            "metric": "alert_rule_value", "expression": "sum(rate(worker_errors_total[5m]))", "unit": "errors/s",
            "threshold": 3, "operator": ">", "rule": {"name": "WorkerErrors", "duration": "5m"},
            "labels": {"namespace": "ns", "pod": "worker-1", "private_label": "never-label"},
            "source": {"adapter": "prometheus", "capture_mode": "incident_capture", "private": "never-source"},
            "values": [["now", "never-raw-point"]] * 100,
            "condition": {"observed_samples": 8, "matching_samples": 5, "missing_samples": 2,
                          "incident_observed_samples": 4, "incident_matching_samples": 3,
                          "min": 1, "max": 5, "latest": {"timestamp": "2026-09-23T02:00:00Z", "value": 4},
                          "raw": "never-condition"},
        }
        context = {
            "episode_id": "metric-budget", "scope": {"pod": "worker-1", "namespace": "ns"},
            "evidence": [{"id": f"E-other-{index}", "summary": "ERROR generic alert. " * 100} for index in range(40)]
                      + [{"id": "E-rule", "domain": "metric_anomaly", "signal_origin": "alert_rule",
                          "summary": "Alert samples cross the threshold. " * 20, "metric_observation": metric}],
        }
        compact, visible = compact_for_model(context, [], max_prompt_tokens=550)
        self.assertLessEqual(estimate_tokens(compact), 550)
        self.assertEqual(compact["evidence"][0]["id"], "E-rule")
        self.assertIn("E-rule", visible)
        observation = compact["evidence"][0]["metric_observation"]
        self.assertEqual(observation["threshold"], 3)
        self.assertEqual(observation["operator"], ">")
        self.assertEqual(observation["condition"]["latest"]["value"], 4)
        self.assertEqual(observation["condition"]["missing_samples"], 2)
        self.assertIn("not proof of continuous rule duration or cause", observation["condition"]["limitation"])
        self.assertNotIn("never-", json.dumps(compact))
        self.assertNotIn("values", observation)
        self.assertEqual(compact["scope"]["pod"], "worker-1")

    def test_rule_metric_numeric_mask_does_not_turn_missing_or_invalid_values_into_zero(self):
        observation = compact_metric_observation({
            "metric": "alert_rule_value", "threshold": float("inf"), "operator": ">",
            "condition": {"min": None, "max": float("nan"), "observed_samples": True,
                          "missing_samples": 5, "latest": {"timestamp": "now", "value": None}},
        })
        self.assertNotIn("threshold", observation)
        for key in ("min", "max", "observed_samples"):
            self.assertNotIn(key, observation["condition"])
        self.assertNotIn("value", observation["condition"]["latest"])
        self.assertEqual(observation["condition"]["missing_samples"], 5)
        self.assertNotIn("threshold", compact_metric_observation({"metric": "metric", "threshold": 10 ** 999}))

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

    def test_structured_log_fields_and_relation_tokens_survive_prompt_compaction(self):
        fields = {"error_code": "ECONNREFUSED", "status_code": 503, "request_id": "request-4821",
                  "authorization": "Bearer do-not-send"}
        context = {"episode_id": "structured-logs", "evidence": [], "alerts": []}
        checks = [{
            "id": "Q-log", "tool": "search_logs", "status": "completed", "required_observation": True,
            "result": {"matching_patterns": 1, "patterns": [{"count": 2, "fields": fields, "examples": [
                {"timestamp": "2026-09-20T00:00:00Z", "level": "ERROR", "message": "dependency failed",
                 "diagnostic_fields": fields},
            ]}]},
        }]

        compact, _ = compact_for_model(context, checks, max_prompt_tokens=1200)

        observation = compact["prior_checks"][0]["observation"]
        token = observation["top_signal"]["request_id"]
        self.assertRegex(token, r"^<REF:[A-Z2-7]{10}>$")
        self.assertEqual(observation["fields"]["request_id"], token)
        self.assertEqual(observation["top_signal"]["error_code"], "ECONNREFUSED")
        self.assertEqual(observation["top_signal"]["status_code"], "503")
        self.assertNotIn("request-4821", json.dumps(compact))
        self.assertNotIn("do-not-send", json.dumps(compact))

    def test_capsule_log_diagnostic_fields_survive_compaction(self):
        context = {
            "episode_id": "capsule-log-fields", "alerts": [],
            "evidence": [{
                "id": "E-log", "domain": "log_template", "title": "Dependency rejected request",
                "summary": "A bounded log pattern contains a structured failure.",
                "diagnostic_fields": {"error_code": "ECONNREFUSED", "request_id": "request-88"},
                "examples": ["request failed"],
            }],
        }

        compact, _ = compact_for_model(context, [], max_prompt_tokens=900)

        fields = compact["evidence"][0]["diagnostic_fields"]
        self.assertEqual(fields["error_code"], "ECONNREFUSED")
        self.assertTrue(fields["request_id"].startswith("<REF:"))
        self.assertNotIn("request-88", json.dumps(compact))

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

    def test_revision_evidence_precedes_competing_member_priorities(self):
        for domain in ("image_evidence", "audio_evidence"):
            with self.subTest(domain=domain):
                sources = [{"id": f"E-{index}", "domain": "log_template", "revision_priority": True,
                            "summary": "Error: connection timeout. " * 10} for index in range(40)]
                addition = {"id": "A-new", "domain": domain, "revision_addition": True,
                            "summary": "Observed endpoint returned HTTP 404.",
                            "limitation": "Observation alone does not establish a cause.",
                            "time_range": {"observed_at": "2026-09-23T02:00:00Z"}}
                compact, visible = compact_for_model(
                    {"episode_id": "episode", "evidence": sources + [addition]}, [],
                    max_prompt_tokens=700,
                    priority_evidence_ids=[item["id"] for item in sources] + ["A-new"],
                )
                self.assertEqual(compact["evidence"][0]["id"], "A-new")
                self.assertIn("A-new", visible)
                self.assertIn("does not establish", compact["evidence"][0]["limitation"])
                self.assertLessEqual(estimate_tokens(compact), 700)
                self.assertEqual(set(compact["priority_evidence_ids"]), set(visible))

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
