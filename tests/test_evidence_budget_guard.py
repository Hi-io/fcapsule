import copy
import json
import unittest

from fcapsule.episode_investigation import run_investigation, validate_assessment
from fcapsule.investigation_tools import InvestigationTools, metric_summary, stamp
from fcapsule.reasoning.context_budget import _check_item, _minimal_check_observation, compact_for_model, estimate_tokens


def discovery(kind="ServiceMonitor", observed="disabled", health="unknown", error=""):
    pod = "processor-7f7946d9b6-xlm4t"
    prefix = "service" if kind == "ServiceMonitor" else "pod"
    pool = f"{kind[0].lower() + kind[1:]}/production/metrics-policy/0"
    target = {"pod": pod, "service": "metrics-endpoint", "scrape_pool": pool,
              "state": "active" if health != "unknown" else "dropped", "health": health, "last_error": error,
              "labels": {f"__meta_kubernetes_{prefix}_label_monitoring_example_enabled": observed,
                         "unrelated-label": "never-forward-this"}}
    return {"source": "Prometheus + Kubernetes", "scope": {"pods": [pod], "namespace": "production"},
        "active_targets": [],
        "dropped_targets": [{**target, "pod": f"other-{index}", "labels": {}} for index in range(24)] + [target],
        "monitor_selection": [{"monitor": {"kind": kind, "name": "metrics-policy", "namespace": "production",
                                             "match_labels": {"monitoring.example/enabled": "enabled"}},
                               "matched_services": ["metrics-endpoint"] if observed == "enabled" else [],
                               "matched_pods": [pod] if kind == "PodMonitor" and observed == "enabled" else []}],
    }


def current_assessment(reference="Q002"):
    return {"summary": "A current target observation was retained.",
            "likely_mechanism": "The observed target state does not establish a cause.",
            "next_action": "Compare the incident-time state with this current snapshot.",
            "expected_finding": "A dated configuration or endpoint observation would discriminate causes.",
            "uncertainty": "The collected discovery view is current and bounded.",
            "evidence_ids": [reference], "connections": [],
            "hypotheses": [{"explanation": "Configuration or endpoint change", "status": "unresolved",
                            "reason": "No causal discriminator is established.", "evidence_ids": [reference]}]}


class EvidenceBudgetGuardTests(unittest.TestCase):
    def test_compaction_retains_service_label_expression_and_namespace_evidence(self):
        raw = {
            "source": "Prometheus target discovery + Kubernetes APIs",
            "scope": {"namespace": "production", "pods": ["api-1"]},
            "observed_at": "2026-09-24T02:00:00Z",
            "provenance": [{"source": "Kubernetes monitoring and Service APIs", "observed_at": "2026-09-24T02:00:00Z"}],
            "active_targets": [], "dropped_targets": [],
            "current_service_labels": [{"service": "api-metrics", "namespace": "production",
                                         "labels": {"monitoring": "enabeld", "private-token": "never-leak"}}],
            "monitor_selection": [{
                "monitor": {"kind": "ServiceMonitor", "namespace": "monitoring", "name": "api",
                    "match_labels": {"monitoring": "enabled"}, "match_expressions": [
                        {"key": "tier", "operator": "In", "values": ["backend", "worker"]},
                        {"key": "private-token", "operator": "Exists", "values": []},
                    ], "selector_complete": True},
                "target_kind": "Service labels", "namespace_scope": {
                    "status": "resolved", "effective_namespaces": ["production"]},
                "matched_services": [], "matched_pods": [],
                "evaluated_services": [{"name": "api-metrics", "namespace": "production",
                    "labels": {"monitoring": "enabeld", "tier": "backend", "private-token": "never-leak"},
                    "selector_evaluation": {"status": "not_matched", "requirements": [
                        {"key": "monitoring", "operator": "Equals", "expected": "enabled",
                         "observed": "enabeld", "matches": False},
                        {"key": "tier", "operator": "In", "expected": ["backend", "worker"],
                         "observed": "backend", "matches": True},
                        {"key": "private-token", "operator": "Exists", "redacted": True},
                    ]}},
            ]}],
        }

        compact = _check_item({"id": "Q2", "tool": "scrape_discovery", "status": "completed", "result": raw}, False)
        observation = _minimal_check_observation(compact)
        selection = observation["monitor_selection"][0]
        self.assertEqual(selection["match_expressions"][0]["operator"], "In")
        self.assertEqual(selection["namespace_scope"]["effective_namespaces"], ["production"])
        self.assertEqual(selection["evaluated_resources"][0]["selector_status"], "not_matched")
        self.assertEqual(selection["evaluated_resources"][0]["requirements"][0]["observed"], "enabeld")
        self.assertEqual(selection["evaluated_resources"][0]["requirements"][1]["matches"], True)
        self.assertNotIn("never-leak", json.dumps(observation))
        self.assertEqual(observation["observed_at"], raw["observed_at"])
        self.assertTrue(observation["compacted_discovery"])

    def test_discovery_compaction_preserves_endpoint_ports_readiness_and_pod_health(self):
        raw = discovery(observed="enabled", health="down", error="connection refused")
        raw["monitor_selection"][0]["monitor"]["endpoints"] = [
            {"port": "metrics", "path": "/metrics", "interval": "30s"}]
        raw["monitor_selection"][0]["configured_endpoints"] = raw["monitor_selection"][0]["monitor"]["endpoints"]
        raw["monitor_selection"][0]["evaluated_services"] = [{
            "name": "metrics-endpoint", "namespace": "production", "labels": {"monitoring.example/enabled": "enabled"},
            "selector": {"app": "mysql-exporter"}, "ports": [{"name": "mysql", "port": 9104, "target_port": 9104}],
            "omitted_ports": 0,
            "endpoint_port_checks": [{"configured_port_name": "metrics", "service_port_names": ["mysql"],
                "status": "does_not_match_service_port_name", "comparison_basis": "Compared ServiceMonitor port with Service port name."}],
            "endpoint_slices": [{"name": "metrics-a", "namespace": "production", "service": "metrics-endpoint",
                "ports": [{"name": "mysql", "port": 9104, "protocol": "TCP"}],
                "endpoints": [{"target_ref": {"kind": "Pod", "name": "mysql-exporter-0", "namespace": "production"},
                    "ready": False, "serving": False, "terminating": False, "address_count": 1}]}],
            "endpoint_pod_health": [{"pod": "mysql-exporter-0", "namespace": "production", "workload": "mysql-exporter",
                "phase": "Running", "ready": False, "ready_status": "false", "container_health": [
                    {"name": "exporter", "ready": False, "restart_count": 2,
                     "state": {"kind": "waiting", "reason": "CrashLoopBackOff"}}]}],
            "selector_evaluation": {"status": "matched", "requirements": [
                {"key": "monitoring.example/enabled", "operator": "Equals", "expected": "enabled",
                 "observed": "enabled", "matches": True}]},
        }]
        raw["endpoint_slice_inventory"] = {"status": "observed", "omitted_slices": 0}
        raw["current_service_labels"] = [{"service": "metrics-endpoint", "namespace": "production",
            "labels": {"monitoring.example/enabled": "enabled"}, "selector": {"app": "mysql-exporter"},
            "ports": [{"name": "mysql", "port": 9104, "target_port": 9104}],
            "endpoint_slices": raw["monitor_selection"][0]["evaluated_services"][0]["endpoint_slices"]}]
        raw["current_pod_health"] = raw["monitor_selection"][0]["evaluated_services"][0]["endpoint_pod_health"]

        compact = _check_item({"id": "Q2", "tool": "scrape_discovery", "status": "completed", "result": raw}, False)
        observation = _minimal_check_observation(compact)
        selection = observation["monitor_selection"][0]
        service = selection["evaluated_resources"][0]

        self.assertEqual(selection["configured_endpoints"][0]["port"], "metrics")
        self.assertEqual(service["service_ports"][0]["name"], "mysql")
        self.assertEqual(service["endpoint_port_checks"][0]["status"], "does_not_match_service_port_name")
        self.assertFalse(service["endpoint_slices"][0]["endpoints"][0]["ready"])
        self.assertEqual(service["endpoint_pod_health"][0]["ready_status"], "false")
        self.assertEqual(service["endpoint_pod_health"][0]["container_health"][0]["restart_count"], 2)
        self.assertEqual(observation["endpoint_slice_inventory"]["status"], "observed")

        pod_raw = discovery(kind="PodMonitor", observed="enabled")
        pod_raw["monitor_selection"][0]["monitor"]["endpoints"] = [{"port": "metrics"}]
        pod_raw["monitor_selection"][0]["evaluated_pods"] = [{
            "name": "mysql-exporter-0", "namespace": "production", "labels": {"monitoring.example/enabled": "enabled"},
            "health": {"pod": "mysql-exporter-0", "namespace": "production", "phase": "Running",
                       "ready": False, "ready_status": "false", "container_health": []},
            "container_ports": [{"name": "mysql", "port": 9104, "protocol": "TCP"}],
            "container_ports_complete": True,
            "endpoint_port_checks": [{"configured_port_name": "metrics", "container_ports": [
                {"name": "mysql", "port": 9104, "protocol": "TCP"}], "container_ports_complete": True,
                "status": "does_not_match_pod_container_port", "comparison_basis": "Compared PodMonitor port with declared container ports."}],
            "selector_evaluation": {"status": "matched", "requirements": [
                {"key": "monitoring.example/enabled", "operator": "Equals", "expected": "enabled",
                 "observed": "enabled", "matches": True}]},
        }]
        pod_compact = _check_item({"id": "Q3", "tool": "scrape_discovery", "status": "completed", "result": pod_raw}, False)
        pod_observation = _minimal_check_observation(pod_compact)
        pod_selection = pod_observation["monitor_selection"][0]
        pod = pod_selection["evaluated_resources"][0]
        self.assertEqual(pod_selection["configured_endpoints"][0]["port"], "metrics")
        self.assertEqual(pod["endpoint_port_checks"][0]["status"], "does_not_match_pod_container_port")
        self.assertEqual(pod["health"]["ready_status"], "false")

    def test_dependency_compaction_keeps_declared_port_comparison_and_provenance(self):
        raw = {
            "service": "inventory", "pod": "inventory-1", "matching_pods": 1,
            "observed_at": "2026-09-24T02:00:00Z", "latest_alert_at": "2026-09-24T01:59:00Z",
            "window": ["2026-09-24T01:50:00Z", "2026-09-24T02:00:00Z"],
            "declared_endpoints": [{"service": "inventory", "configured_via": "ConfigMap/runtime:INVENTORY_URL",
                "configured_endpoint": {"host": "inventory", "scheme": "http", "port": 8080, "port_source": "explicit"},
                "observed_at": "2026-09-24T02:00:00Z"}],
            "service_observation": {"name": "inventory", "namespace": "production", "type": "ClusterIP",
                "selector": {"app": "inventory"}, "ports": [{"name": "http", "port": 8081,
                    "target_port": 8081, "protocol": "TCP"}], "source": "Kubernetes API Service",
                "observed_at": "2026-09-24T02:00:00Z"},
            "port_comparisons": [{"configured_host": "inventory", "configured_port": 8080,
                "configured_port_source": "explicit", "status": "does_not_match_service_port",
                "service_ports": [{"name": "http", "port": 8081, "target_port": 8081, "protocol": "TCP"}],
                "comparison_basis": "Configured endpoint port compared with Kubernetes Service port."}],
            "provenance": [{"source": "Kubernetes API Service", "resource": "Service/production/inventory",
                            "observed_at": "2026-09-24T02:00:00Z"}],
            "observations": [], "unavailable_sources": [], "not_collected_sources": [],
            "limitation": "Current Service state only.",
        }

        compact = _check_item({"id": "Q3", "tool": "dependency_evidence", "status": "completed", "result": raw}, False)
        observation = _minimal_check_observation(compact)

        self.assertEqual(observation["declared_endpoints"][0]["configured_endpoint"]["port"], 8080)
        self.assertEqual(observation["service_observation"]["ports"][0]["port"], 8081)
        self.assertEqual(observation["port_comparisons"][0]["status"], "does_not_match_service_port")
        self.assertEqual(observation["provenance"][0]["resource"], "Service/production/inventory")
        self.assertEqual(observation["observed_at"], raw["observed_at"])

    def test_compaction_keeps_paired_port_provenance(self):
        raw = {"declared_endpoints": [{
            "service": "inventory", "configured_via": "ConfigMap/runtime:INVENTORY_HOST",
            "configured_endpoint": {"host": "inventory", "port": 8081, "port_source": "paired_environment"},
            "port_configured_via": "ConfigMap/runtime:INVENTORY_PORT",
            "observed_at": "2026-09-24T02:00:00Z",
        }], "service_observation": {"ports": []}, "port_comparisons": [], "observations": []}

        compact = _check_item({"id": "Q3", "tool": "dependency_evidence", "status": "completed", "result": raw}, False)
        observation = _minimal_check_observation(compact)

        self.assertEqual(observation["declared_endpoints"][0]["port_configured_via"],
                         "ConfigMap/runtime:INVENTORY_PORT")
        workload = _check_item({"id": "Q1", "tool": "workload_state", "result": {
            "declared_dependencies": raw["declared_endpoints"], "observations": []}}, False)
        self.assertEqual(workload["observation"]["declared_dependencies"][0]["port_configured_via"],
                         "ConfigMap/runtime:INVENTORY_PORT")

    def test_workload_state_summary_retains_endpoint_identity_without_url_path(self):
        raw = {"declared_dependencies": [{"service": "inventory", "configured_via": "ConfigMap/runtime:INVENTORY_URL",
                "configured_endpoint": {"host": "inventory", "scheme": "http", "port": 8080,
                                         "port_source": "explicit", "path": "/private"},
                "observed_at": "2026-09-24T02:00:00Z"}],
            "observations": [{"kind": "PodSpec", "name": "orders-1", "resources": [], "container_states": []}]}
        compact = _check_item({"id": "Q1", "tool": "workload_state", "result": raw}, False)
        endpoint = compact["observation"]["declared_dependencies"][0]["configured_endpoint"]
        self.assertEqual(endpoint["host"], "inventory")
        self.assertEqual(endpoint["port"], 8080)
        self.assertNotIn("path", endpoint)

    def test_selector_and_actual_labels_stay_paired_without_forcing_mismatch(self):
        for kind in ("ServiceMonitor", "PodMonitor"):
            for actual in ("enabled", "disabled"):
                with self.subTest(kind=kind, actual=actual):
                    raw = discovery(kind, actual)
                    before = copy.deepcopy(raw)
                    compact = _check_item({"id": "Q2", "tool": "scrape_discovery", "status": "completed", "result": raw}, False)
                    observation = _minimal_check_observation(compact)
                    selected = observation["monitor_selection"][0]
                    self.assertEqual(selected["kind"], kind)
                    self.assertEqual(selected["match_labels"]["monitoring.example/enabled"], "enabled")
                    target = selected["targets"][0]
                    self.assertEqual(target["pod"], raw["scope"]["pods"][0])
                    self.assertEqual(target["selector_labels"]["monitoring.example/enabled"], actual)
                    self.assertEqual(selected["matched_services"], ["metrics-endpoint"] if actual == "enabled" else [])
                    self.assertEqual(selected["target_count"], 25)
                    self.assertNotIn("never-forward", json.dumps(observation))
                    self.assertNotIn("mismatch", json.dumps(observation))
                    self.assertEqual(_minimal_check_observation({**compact, "observation": observation}), observation)
                    self.assertEqual(raw, before)

    def test_missing_labels_are_unknown_and_scrape_errors_do_not_need_a_monitor(self):
        raw = discovery()
        raw["monitor_selection"] = []
        raw["active_targets"] = [{"pod": raw["scope"]["pods"][0], "state": "active", "health": "down",
                                  "last_error": "HTTP 404 from 10.2.3.4 password=never-error"}]
        compact = _check_item({"id": "Q2", "tool": "scrape_discovery", "status": "completed", "result": raw}, False)
        observation = _minimal_check_observation(compact)
        self.assertEqual(observation["monitor_selection"], [])
        target = observation["other_targets"][0]
        self.assertEqual(target["state"], "active")
        self.assertEqual(target["health"], "down")
        self.assertIn("404", target["last_error"])
        self.assertNotIn("never-error", json.dumps(observation))
        self.assertNotIn("10.2.3.4", json.dumps(observation))
        self.assertIn("absence is not proof", observation["limitation"])
        raw = discovery()
        for target in raw["dropped_targets"]:
            target["labels"] = {}
        raw["monitor_selection"][0]["monitor"]["match_labels"]["private_token"] = "never-secret"
        compact = _check_item({"id": "Q2", "tool": "scrape_discovery", "status": "completed", "result": raw}, False)
        self.assertEqual(compact["observation"]["monitor_selection"][0]["targets"][0]["selector_labels"], {})
        self.assertNotIn("never-secret", json.dumps(compact))

    def test_generic_minimum_keeps_facts_not_a_serialized_source_prefix(self):
        check = {"tool": "resource_history", "observation": {
            "source": "metadata " * 50, "scope": "metadata " * 50,
            "observations": [{"metric": "queue_wait_seconds", "max": 4.25}],
            "limitation": "Sampled interval only.",
        }}
        observation = _minimal_check_observation(check)
        self.assertIsInstance(observation, dict)
        self.assertEqual(observation["observations"][0]["max"], 4.25)
        self.assertEqual(_minimal_check_observation({**check, "observation": observation}), observation)

    def test_resource_history_keeps_alert_phase_samples_and_explicit_freshness_in_minimum_mode(self):
        focus = stamp("2026-09-24T12:00:00Z")
        captured = "2026-09-24T12:05:00Z"
        raw = {"captured_at": captured, "latest_alert_at": focus.isoformat(),
               "observations": metric_summary([{"metric": "pod_memory_working_set_bytes", "labels": {"pod": "worker-1"},
                   "values": [["2026-09-24T11:58:00Z", 15], ["2026-09-24T12:00:00Z", 32],
                              ["2026-09-24T12:01:00Z", 48], ["2026-09-24T12:02:00Z", 64]]}],
                   focus, captured_at=captured), "limitation": "Sampled history only."}
        check = {"id": "Q-memory", "tool": "resource_history", "status": "completed",
                 "required_observation": True, "result": raw}

        compact = _check_item(check, True)
        minimum = _minimal_check_observation(compact)
        sample = minimum["observations"][0]
        self.assertEqual(sample["before_alert"], {"timestamp": "2026-09-24T11:58:00Z", "value": 15.0})
        self.assertEqual(sample["nearest_alert"]["value"], 32.0)
        self.assertEqual(sample["after_alert"], {"timestamp": "2026-09-24T12:01:00Z", "value": 48.0})
        self.assertEqual(sample["sampled_peak"]["value"], 64.0)
        self.assertEqual(sample["freshness"]["status"], "sampled")
        self.assertEqual(sample["freshness"]["age_seconds"], 180)

        context = {"episode_id": "metric-minimum", "live_capture": True, "evidence": []}
        bounded, visible = compact_for_model(context, [check], max_prompt_tokens=350)
        self.assertLessEqual(estimate_tokens(bounded), 350)
        self.assertIn("Q-memory", visible)
        self.assertTrue(bounded["prior_checks"][0]["observation"].get("minimal_resource_history"))
        self.assertEqual(bounded["prior_checks"][0]["observation"]["observations"][0]["sampled_peak"]["value"], 64.0)

    def test_resource_history_missing_series_is_unknown_not_zero(self):
        missing = {"captured_at": "2026-09-24T12:05:00Z", "observations": [
            {"metric": "pod_memory_working_set_bytes", "labels": {}, "samples": 0,
             "freshness": {"status": "no_data", "latest_sample_at": None, "age_seconds": None}}
        ]}
        compact = _check_item({"id": "Q-empty", "tool": "resource_history", "status": "completed", "result": missing}, True)
        minimum = _minimal_check_observation(compact)
        self.assertEqual(minimum["data_status"], "no_data")
        self.assertEqual(minimum["observations"][0]["samples"], 0)
        self.assertEqual(minimum["observations"][0]["freshness"]["status"], "no_data")
        self.assertNotIn('"value":0', json.dumps(minimum))

    def test_pod_labels_are_not_substituted_for_service_selector_labels(self):
        for kind in ("PodMonitor", "ServiceMonitor"):
            raw = discovery(kind)
            raw["dropped_targets"] = []
            raw["current_pod_labels"] = [{"pod": raw["scope"]["pods"][0], "labels": {
                "monitoring.example/enabled": "disabled", "unrelated": "never-copy"}}]
            compact = _check_item({"tool": "scrape_discovery", "status": "completed", "result": raw}, False)
            selection = compact["observation"]["monitor_selection"][0]
            if kind == "PodMonitor":
                self.assertEqual(selection["current_pod_labels"][0]["labels"], {"monitoring.example/enabled": "disabled"})
            else:
                self.assertNotIn("current_pod_labels", selection)
            self.assertNotIn("never-copy", json.dumps(compact))

    def test_current_required_fact_precedes_later_history_under_pressure(self):
        context = {"episode_id": "current", "evidence": [{"id": "E1", "summary": "Current observation"}]}
        checks = [{"id": "Q-current", "tool": "resource_history", "status": "completed", "required_observation": True,
                   "result": {"observations": [{"metric": "queue_wait_seconds", "max": 4.25}]}},
                  {"id": "Q-history", "tool": "historical_episode", "status": "completed", "required_observation": True,
                   "result": {"observations": [{"summary": "Prior observation " * 30}] * 10}}]
        for budget in (220, 300):
            compact, visible = compact_for_model(context, checks, max_prompt_tokens=budget)
            self.assertLessEqual(estimate_tokens(compact), budget)
            self.assertIn("Q-current", visible)
            self.assertNotIn("Q-history", visible)
            self.assertIn("4.25", json.dumps(compact["prior_checks"]))

    def test_full_2100_requests_keep_current_discovery_in_draft_and_review(self):
        raw = discovery()
        context = {"episode_id": "current", "live_capture": True,
                   "scope": {"namespace": "production", "pod": raw["scope"]["pods"][0]},
                   "alerts": [{"incident_id": "one", "labels": {"target_workload": "processor"}}],
                   "evidence": [{"id": "E1", "summary": "Expected monitoring target absent."}]
                               + [{"id": f"E-noise-{index}", "summary": "Other observation " * 60} for index in range(30)],
                   "historical_candidates": [{"episode_id": "prior-episode"}], "recurrence": {"previous_count": 8}}
        checks = {"workload_state": {"observations": [{"kind": "PodSpec", "ready": True}]},
                  "scrape_discovery": raw,
                  "historical_episode": {"observations": [{"summary": "Prior captured state. " * 50}] * 80}}
        requests = []

        class Kit:
            CATALOG = InvestigationTools.CATALOG
            pods = raw["scope"]["pods"]

            def execute(self, name, arguments):
                return copy.deepcopy(checks[name])

        class Client:
            def chat(self, request):
                requests.append(request)
                value = current_assessment()
                value["historical_comparison"] = {"episode_id": "Q003", "status": "similar_mechanism",
                                                   "summary": "Unsupported prior claim", "evidence_ids": ["Q003"]}
                return {"content": json.dumps({"action": "finish", "assessment": value}),
                        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}

        state = run_investigation(context, Kit(), "fake-model", 1200, lambda state: None,
                                  max_checks=1, max_prompt_tokens=2100, max_total_tokens=12000, client=Client())
        self.assertEqual(state["status"], "ready")
        self.assertEqual(len(requests), 2)
        self.assertEqual(state["assessment"]["summary"], current_assessment()["summary"])
        self.assertNotIn("historical_comparison", state["assessment"])
        self.assertEqual(state["assessment"]["historical_comparison_review"]["reason"], "unavailable_episode")
        for request, call in zip(requests, state["calls"]):
            prompt = json.loads(request.messages[1]["content"])
            self.assertLessEqual(estimate_tokens(request.messages[0]["content"]) + estimate_tokens(request.messages[1]["content"]), 2100)
            self.assertEqual(call["model_context"], prompt["episode"])
            self.assertIn("Q002", call["visible_evidence_ids"])
            check = next(row for row in prompt["episode"]["prior_checks"] if row["id"] == "Q002")
            selection = check["observation"]["monitor_selection"][0]
            self.assertEqual(selection["match_labels"]["monitoring.example/enabled"], "enabled")
            self.assertEqual(selection["targets"][0]["selector_labels"]["monitoring.example/enabled"], "disabled")

    def test_omitted_or_unknown_comparison_cannot_hide_invalid_current_citations(self):
        for comparison in (None, {"episode_id": "unknown", "evidence_ids": ["invented"]}):
            value = current_assessment("E-current")
            if comparison is not None:
                value["historical_comparison"] = comparison
            result = validate_assessment(value, {"E-current"}, {"one"}, {"prior"})
            self.assertNotIn("historical_comparison", result)
            self.assertEqual(result["historical_comparison_review"]["status"], "omitted")
            self.assertEqual(validate_assessment(result, {"E-current"}, {"one"}, {"prior"})["summary"], result["summary"])
            value["evidence_ids"] = ["invented"]
            with self.assertRaisesRegex(ValueError, "unavailable evidence"):
                validate_assessment(value, {"E-current"}, {"one"}, {"prior"})


if __name__ == "__main__":
    unittest.main()
