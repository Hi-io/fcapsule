import copy
import json
import unittest

from fcapsule.reasoning.context_budget import (
    _discovery_observation, _log_observation, _minimal_check_observation, _workload_observation, compact_for_model,
    compact_metric_observation, estimate_tokens,
)


class ContextBudgetTests(unittest.TestCase):
    def test_discovery_compaction_prioritizes_the_exact_down_target_pool(self):
        down_target = {
            "state": "active", "health": "down", "last_error": "HTTP 404 Not Found",
            "scrape_pool": "serviceMonitor/observability/exporter/0", "scrape_path": "/metrics-v2",
            "service": "metrics-exporter", "pod": "exporter-1",
        }
        result = {
            "scope": {"namespace": "lab", "pods": ["exporter-1"], "workload": "metrics-exporter"},
            "discovery_targets": {"target_service": "metrics-exporter", "target_workload": "metrics-exporter"},
            "active_targets": [down_target],
            "dropped_targets": [{
                "state": "dropped", "scrape_pool": "serviceMonitor/observability/applications/0",
                "service": "inventory-api", "pod": "inventory-1",
            }],
            "monitor_selection": [
                {
                    "monitor": {"kind": "ServiceMonitor", "namespace": "observability", "name": "applications",
                                "match_labels": {"metrics": "enabled"}},
                    "matched_services": ["app-metrics"], "matched_pods": [],
                    "evaluated_services": [{"name": "metrics-exporter", "target_relevance": "alert_target_service",
                                            "selector_evaluation": {"status": "not_matched"}}],
                },
                {
                    "monitor": {"kind": "ServiceMonitor", "namespace": "observability", "name": "exporter",
                                "match_labels": {"metrics": "exporter"}},
                    "matched_services": ["metrics-exporter"], "matched_pods": [],
                    "evaluated_services": [{"name": "metrics-exporter", "target_relevance": "alert_target_service",
                                            "selector_evaluation": {"status": "matched"}}],
                },
            ],
        }

        observation = _discovery_observation(result)

        self.assertEqual(observation["monitor_selection"][0]["name"], "exporter")
        self.assertEqual(observation["monitor_selection"][0]["targets"][0]["scrape_path"], "/metrics-v2")

    def test_log_diagnostics_stay_paired_with_representative_events_in_model_context(self):
        context = {"episode_id": "diag-pair", "live_capture": True, "evidence": [{
            "id": "E-log", "domain": "log_template", "title": "reservation failed",
            "examples": [
                {"timestamp": "2026-09-20T12:00:00Z", "level": "ERROR", "message": "Reservation refused",
                 "diagnostic_fields": {"buffered_bytes": "4096", "request_id": "<REF:AAAAAAAAAA>"}},
                {"timestamp": "2026-09-20T12:04:00Z", "level": "ERROR", "message": "Reservation refused",
                 "diagnostic_fields": {"buffered_bytes": "8192", "request_id": "<REF:BBBBBBBBBB>"}},
            ],
        }]}

        compact, visible = compact_for_model(context, [], max_prompt_tokens=500)

        pairs = compact["evidence"][0]["diagnostic_examples"]
        self.assertEqual([item["diagnostic_fields"]["buffered_bytes"] for item in pairs], ["4096", "8192"])
        self.assertEqual([item["timestamp"] for item in pairs], ["2026-09-20T12:00:00Z", "2026-09-20T12:04:00Z"])
        self.assertEqual(visible, ["E-log"])

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

    def test_primary_alert_and_evidence_attribution_survive_large_episode_compaction(self):
        alerts = [{"incident_id": f"incident-{index:02d}", "alertname": f"Alert{index}",
                   "started_at": f"2026-09-20T12:{index:02d}:00Z"} for index in range(14)]
        context = {
            "episode_id": "multi-alert", "primary_incident_id": "incident-07",
            "alerts": alerts,
            "evidence": [{"id": "E-current", "summary": "Current alert sample",
                          "provenance": [{"incident_id": "incident-07", "evidence_id": "ev_metric_002"}]},
                         {"id": "E-sibling", "summary": "Earlier sibling sample",
                          "provenance": [{"incident_id": "incident-04", "evidence_id": "ev_log_004"}]}],
        }

        compact, visible = compact_for_model(context, [], max_prompt_tokens=800)

        self.assertEqual(compact["primary_incident_id"], "incident-07")
        self.assertEqual(compact["alerts"][0]["incident_id"], "incident-07")
        evidence = next(row for row in compact["evidence"] if row["id"] == "E-current")
        self.assertNotIn("incident_ids", evidence)
        sibling = next(row for row in compact["evidence"] if row["id"] == "E-sibling")
        self.assertEqual(sibling["incident_ids"], ["incident-04"])
        self.assertIn("E-current", visible)
        self.assertLessEqual(len(compact["alerts"]), 12)

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

    def test_selected_resource_history_survives_prompt_compaction(self):
        context = {
            "episode_id": "resource-history-budget",
            "live_capture": True,
            "scope": {"pod": "worker-1", "namespace": "production",
                      "alert_started_at": "2026-09-23T02:00:00Z",
                      "window": {"start": "2026-09-23T01:55:00Z", "end": "2026-09-23T02:05:00Z"}},
            "alerts": [{"incident_id": "incident-1", "alertname": "WorkerPressure"}],
            "evidence": [{"id": f"E{index}", "domain": "log_template",
                          "summary": "Routine worker status remained available. " * 80}
                         for index in range(12)],
        }
        checks = [
            {"id": "Q001", "tool": "workload_state", "status": "completed", "required_observation": True,
             "result": {"observations": [
                 {"kind": "PodSpec", "ready": True, "resources": [{"limits": {"memory": "160Mi"}}]},
                 {"kind": "ConfigMap", "name": "worker-config", "data": {"WORKER_MAX_BUFFER_BYTES": "104857600"}},
             ]}},
            {"id": "Q002", "tool": "search_logs", "status": "completed", "required_observation": True,
             "result": {"patterns": [{"count": 4, "examples": [{"message": "worker completed request"}]}]}},
            {"id": "Q003", "tool": "historical_episode", "status": "completed", "required_observation": True,
             "result": {"observations": [{"summary": "Earlier alert observations are incomplete."}],
                        "limitation": "Partial retained history."}},
            {"id": "Q004", "tool": "resource_history", "status": "completed", "required_observation": False,
             "question": "Did sampled memory approach its configured limit?",
             "distinguishes": "Resource exhaustion versus an unrelated alert.",
             "result": {"captured_at": "2026-09-23T02:05:02Z", "latest_alert_at": "2026-09-23T02:00:00Z",
                        "observations": [{
                            "metric": metric, "samples": 31, "labels": {"pod": "worker-1", "namespace": "production"},
                            "freshness": {"status": "sampled", "latest_sample_at": "2026-09-23T02:04:48Z",
                                          "age_seconds": 14, "captured_at": "2026-09-23T02:05:02Z"},
                            "before_alert": {"timestamp": "2026-09-23T01:59:48Z", "value": 120000000},
                            "nearest_alert": {"timestamp": "2026-09-23T02:00:08Z", "value": 128000000,
                                               "offset_seconds": 8},
                            "after_alert": {"timestamp": "2026-09-23T02:00:08Z", "value": 128000000},
                            "sampled_peak": {"timestamp": "2026-09-23T02:00:08Z", "value": 128000000},
                            "max": 128000000, "min": 100000000, "median": 110000000,
                        } for metric in ("pod_memory_working_set_bytes", "pod_memory_limit_bytes",
                                        "pod_cpu_throttled_ratio", "pod_container_restarts_total")]}}
        ]

        compact, visible = compact_for_model(context, checks, max_prompt_tokens=1200)

        self.assertLessEqual(estimate_tokens(compact), 1200)
        self.assertIn("Q004", visible)
        history = next(item for item in compact["prior_checks"] if item["id"] == "Q004")
        metrics = {item["metric"] for item in history["observation"]["observations"]}
        self.assertIn("pod_memory_working_set_bytes", metrics)
        self.assertIn("Q001", visible)
        workload = next(item for item in compact["prior_checks"] if item["id"] == "Q001")
        self.assertIn("160Mi", json.dumps(workload["observation"]))
        self.assertIn("104857600", json.dumps(workload["observation"]))

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

    def test_log_observation_contrasts_captured_replicas_and_route_ports(self):
        observation = _log_observation({"matching_patterns": 2, "pod_samples": [
            {"pod": "edge-a", "sampled_lines": 75}, {"pod": "edge-b", "sampled_lines": 75},
        ], "patterns": [
            {"count": 70, "examples": [{"pod": "edge-a", "level": "ERROR",
                "message": '{"pod":"edge-a","message":"Inventory dependency probe failed","dependency_port":8099,"mode":"route-drift"}'}]},
            {"count": 10, "examples": [{"pod": "edge-b", "level": "INFO",
                "message": '{"pod":"edge-b","message":"Inventory dependency probe succeeded","dependency_port":8081}'}]},
        ]})

        self.assertEqual(observation["top_signal"]["pod"], "edge-a")
        self.assertEqual(observation["top_signal"]["dependency_port"], "8099")
        self.assertEqual(observation["other_diagnostic_patterns"][0]["top_signal"]["pod"], "edge-b")
        self.assertEqual(observation["other_diagnostic_patterns"][0]["top_signal"]["dependency_port"], "8081")
        self.assertEqual(len(observation["pod_samples"]), 2)

    def test_log_check_compaction_preserves_numeric_range_and_secondary_bound_signal(self):
        result = {"matching_patterns": 3, "patterns": [
            {"pattern": "Retained export page", "count": 36,
             "fields": {"buffered_bytes": "100504940", "page_bytes": "2184890",
                        "delivery": "buffered", "rows": "24000"},
             "diagnostic_ranges": {"buffered_bytes": {
                 "min": "24033790", "max": "100504940", "samples": "36"}},
             "examples": [{"timestamp": "2026-09-24T17:22:47Z", "level": "INFO",
                           "message": "Export page retained past its delivery boundary",
                           "diagnostic_fields": {"buffered_bytes": "100504940", "page_bytes": "2184890",
                                                 "delivery": "buffered", "rows": "24000"}}]},
            {"pattern": "Export buffer reached configured safety bound", "count": 1,
             "fields": {"buffered_pages": "46", "buffered_bytes": "100504940",
                        "maximum_buffered_bytes": "100663296"},
             "examples": [{"timestamp": "2026-09-24T17:22:48Z", "level": "WARN",
                           "message": "Export buffer reached its configured safety bound",
                           "diagnostic_fields": {"buffered_pages": "46", "buffered_bytes": "100504940",
                                                 "maximum_buffered_bytes": "100663296"}}]},
            {"pattern": "Worker scheduler heartbeat", "count": 81,
             "examples": [{"level": "INFO", "message": "Worker scheduler heartbeat"}]},
        ]}
        checks = [{"id": "Q001", "tool": "search_logs", "status": "completed",
                   "required_observation": True, "result": result}]

        compact, visible = compact_for_model(
            {"episode_id": "memory-pressure", "live_capture": True,
             "scope": {"namespace": "production", "pod": "worker-1"},
             "alerts": [{"incident_id": "incident-1", "alertname": "WorkerBufferPressure"}],
             "evidence": [{"id": "E-alert", "summary": "Buffer allocation exceeded alert threshold."}]},
            checks, max_prompt_tokens=700,
        )
        observation = compact["prior_checks"][0]["observation"]

        self.assertLessEqual(estimate_tokens(compact), 700)
        self.assertIn("Q001", visible)
        self.assertEqual(observation["diagnostic_ranges"]["buffered_bytes"],
                         {"min": "24033790", "max": "100504940", "samples": "36"})
        bound = observation["other_diagnostic_patterns"][0]
        self.assertEqual(bound["top_signal"]["level"], "WARN")
        self.assertEqual(bound["fields"]["buffered_pages"], "46")
        self.assertEqual(bound["fields"]["maximum_buffered_bytes"], "100663296")
        minimum = _minimal_check_observation(compact["prior_checks"][0])
        self.assertEqual(minimum["diagnostic_ranges"], observation["diagnostic_ranges"])
        self.assertEqual(minimum["other_diagnostic_patterns"], observation["other_diagnostic_patterns"])

    def test_structured_log_fields_and_relation_tokens_survive_prompt_compaction(self):
        fields = {
            "error_code": "ECONNREFUSED", "status_code": 503, "request_id": "request-4821",
            "dependency_duration_ms": 125.5,
            "mysql_error_code": 1062, "constraint_name": "reservation_events.PRIMARY",
            "final_upstream_status": 200,
            "query_revision": "v2", "timeout_seconds": 0.05,
            "authorization": "Bearer do-not-send",
        }
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
        self.assertEqual(observation["top_signal"]["dependency_duration_ms"], "125.5")
        self.assertEqual(observation["top_signal"]["mysql_error_code"], "1062")
        self.assertEqual(observation["top_signal"]["constraint"], "reservation_events.PRIMARY")
        self.assertEqual(observation["top_signal"]["final_upstream_status"], "200")
        self.assertEqual(observation["top_signal"]["query_revision"], "v2")
        self.assertEqual(observation["top_signal"]["timeout_seconds"], "0.05")
        self.assertNotIn("request-4821", json.dumps(compact))
        self.assertNotIn("do-not-send", json.dumps(compact))
        self.assertLessEqual(estimate_tokens(compact), 1200)

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
