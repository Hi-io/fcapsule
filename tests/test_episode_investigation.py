import copy
import json
import unittest
from unittest.mock import Mock

from fcapsule.episode_investigation import assessment_payload, run_investigation, validate_assessment
from fcapsule.investigation_tools import InvestigationTools, episode_context, log_patterns, metric_summary, scrub
from fcapsule.processing.anonymizer import anonymize_text, template_for_message


def assessment(ref="Q001"):
    return {"summary": "Worker restarted", "likely_mechanism": "A retained retry may repeat the failure.",
            "next_action": "Inspect the retained delivery disposition.", "expected_finding": "Retry without acknowledgement",
            "uncertainty": "A repeated payload is not yet confirmed.", "evidence_ids": [ref],
            "hypotheses": [{"explanation": "Retry loop", "status": "supported", "reason": "Repeated decoder failure", "evidence_ids": [ref]}],
            "connections": []}


class FakeClient:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.requests = []

    def chat(self, request):
        self.requests.append(request)
        decision = next(self.decisions)
        if isinstance(decision, Exception):
            raise decision
        return {"content": json.dumps(decision), "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}}


class InvestigationEngineTests(unittest.TestCase):
    def setUp(self):
        self.context = {"episode_id": "episode", "evidence": [{"id": "E1"}], "alerts": [{"incident_id": "one"}]}
        self.kit = Mock(pods=["worker"], CATALOG=InvestigationTools.CATALOG)
        self.kit.execute.return_value = {"observations": [{"reason": "Error"}]}
        self.progress = []

    def run_case(self, decisions, **kwargs):
        if decisions and isinstance(decisions[-1], dict) and decisions[-1].get("action") == "finish":
            decisions = [*decisions, copy.deepcopy(decisions[-1])]
        client = FakeClient(decisions)
        state = run_investigation(self.context, self.kit, "test-model", 1000,
                                  lambda state: self.progress.append(copy.deepcopy(state)), client=client, **kwargs)
        return state, client

    def test_preserves_before_model_and_updates_from_real_tool_observation(self):
        state, client = self.run_case([
            {"action": "check", "tool": "review_omitted", "arguments": {}, "question": "Any contradictory logs?", "distinguishes": "A different failure mode"},
            {"action": "finish", "assessment": assessment("Q002")},
        ])
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["usage"]["total_tokens"], 390)
        self.assertEqual(len(state["checks"]), 2)
        self.assertTrue(state["checks"][0]["automatic_preservation"])
        self.assertIn('"reason": "Error"', client.requests[1].messages[1]["content"])
        self.assertEqual(self.progress[0]["checks"][0]["status"], "running")
        self.assertEqual(state["source_retention"], "unknown")

    def test_invalid_citation_never_becomes_ready(self):
        state, _ = self.run_case([{"action": "finish", "assessment": assessment("Q999")}], max_checks=0)
        self.assertEqual(state["status"], "incomplete")
        self.assertIsNone(state["assessment"])
        self.assertEqual(state["usage"]["total_tokens"], 130)

    def test_unavailable_query_is_not_citable(self):
        self.kit.execute.side_effect = RuntimeError("password=never-persist-this")
        state, _ = self.run_case([{"action": "finish", "assessment": assessment()}], max_checks=0)
        self.assertEqual(state["checks"][0]["status"], "unavailable")
        self.assertEqual(state["status"], "incomplete")
        self.assertNotIn("never-persist", json.dumps(state))

    def test_provider_failure_keeps_checks_and_marks_unknown_usage(self):
        state, _ = self.run_case([RuntimeError("provider failed password=do-not-save")])
        self.assertEqual(state["status"], "incomplete")
        self.assertFalse(state["usage"]["complete"])
        self.assertEqual(state["checks"][0]["status"], "completed")
        self.assertNotIn("do-not-save", json.dumps(state))

    def test_one_schema_repair_uses_existing_budget_and_keeps_rejected_decision(self):
        state, client = self.run_case([{"action": "check", "tool": "review_omitted", "arguments": {}, "question": "Other failures?", "distinguishes": "A competing cause"},
                                       {"action": "finish", "assessment": assessment("invented")},
                                       {"action": "finish", "assessment": assessment()}])
        self.assertEqual(state["status"], "ready")
        self.assertEqual(len(client.requests), 4)
        self.assertEqual(client.requests[0].reasoning_effort, "low")
        self.assertEqual(client.requests[2].reasoning_effort, "none")
        self.assertTrue(client.requests[1].json_output)
        self.assertIn("validation_error", state["calls"][1])
        self.assertEqual(state["usage"]["total_tokens"], 520)

    def test_review_corrects_draft_before_it_is_published(self):
        corrected = assessment()
        corrected["summary"] = "Worker restarted; recovery mechanism is unknown."
        state, client = self.run_case([
            {"action": "finish", "assessment": assessment()},
            {"action": "finish", "assessment": corrected},
        ], max_checks=0)
        self.assertEqual(state["assessment"]["summary"], corrected["summary"])
        self.assertTrue(state["review"]["changed"])
        self.assertEqual(state["calls"][-1]["phase"], "evidence_review")
        self.assertEqual(len(client.requests), 2)
        self.assertFalse(any(item["status"] == "ready" and item["assessment"] == assessment() for item in self.progress))

    def test_review_accepts_misplaced_arrays_and_records_original_layout(self):
        misplaced = {"action": "finish", "assessment": assessment()}
        misplaced["hypotheses"] = misplaced["assessment"].pop("hypotheses")
        misplaced["connections"] = misplaced["assessment"].pop("connections")
        state, _ = self.run_case([
            {"action": "finish", "assessment": assessment()}, misplaced,
        ], max_checks=0)
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["assessment"], assessment())
        self.assertNotIn("hypotheses", state["calls"][-1]["decision"]["assessment"])
        self.assertEqual(len(state["calls"][-1]["schema_adjustments"]), 2)

    def test_layout_normalization_does_not_overwrite_or_validate_content(self):
        with self.assertRaises(ValueError):
            assessment_payload({"assessment": assessment(), "hypotheses": []}, {})
        malformed = assessment_payload({"assessment": {"summary": "Missing fields"}, "hypotheses": "not a list"}, {})
        with self.assertRaises(ValueError):
            validate_assessment(malformed, {"Q001"}, {"one"})

    def test_excess_citations_explain_the_actual_limit(self):
        value = assessment()
        value["evidence_ids"] = [f"E{index}" for index in range(9)]
        with self.assertRaisesRegex(ValueError, "one to eight"):
            validate_assessment(value, set(value["evidence_ids"]), {"one"})

    def test_hard_call_budget_and_disallowed_tools(self):
        decision = {"action": "check", "tool": "resource_history", "arguments": {}, "question": "Resource pressure?", "distinguishes": "CPU or memory"}
        state, client = self.run_case([decision, decision], max_checks=1)
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(len(state["checks"]), 2)
        self.assertEqual(state["status"], "incomplete")
        self.kit.execute.reset_mock()
        state, _ = self.run_case([{**decision, "tool": "kubectl_exec"}])
        self.assertEqual(self.kit.execute.call_count, 1)  # Preservation only.

    def test_multi_alert_relationships_are_explicit(self):
        with self.assertRaises(ValueError):
            validate_assessment(assessment(), {"Q001"}, {"one", "two"})
        value = assessment()
        value["connections"] = [{"from": "one", "to": "two", "relationship": "no_link_established", "reason": "Timing only", "evidence_ids": ["Q001"]}]
        self.assertEqual(len(validate_assessment(value, {"Q001"}, {"one", "two"})["connections"]), 1)

    def test_live_log_query_is_not_delegated_back_without_attempt(self):
        self.context.update(live_capture=True, evidence=[{"id": "E1", "domain": "log_template"}])
        state, _ = self.run_case([{"action": "finish", "assessment": assessment()}], max_checks=0)
        self.assertEqual(state["status"], "incomplete")
        self.assertIn("Search source logs", state["validation_error"])


class InvestigationToolTests(unittest.TestCase):
    def setUp(self):
        self.entries = [{"incident": {"source_kind": "live", "incident_id": "one"}, "capsule": {
            "case": {"pod": "worker-1", "window": {"start": "2026-09-20T12:00:00Z", "end": "2026-09-20T12:10:00Z"}},
            "selected_evidence": [{"source_id": "selected"}], "log_templates": [
                {"template_id": "omitted", "template": "reason=OOMKilled", "count": 1, "representative_lines": ["rare OOM"], "first_seen": "start", "last_seen": "end"},
                {"template_id": "selected", "template": "routine", "count": 10},
            ]}, "report": {"incident": {"incident_id": "one"}, "supporting_evidence": [
                {"evidence_id": "ev1", "type": "alert", "title": "Alert", "summary": "Triggered"}]}}]
        self.sources = Mock()
        self.sources.configuration.return_value = {"cluster_name": "test", "namespaces": ["ns"]}
        self.prom, self.logs, self.kube = Mock(), Mock(), Mock()
        self.sources.adapters.return_value = (self.prom, self.logs, self.kube)
        self.kube.list_pods.return_value = []
        self.prom.collect_pod_metrics.return_value = []
        self.kit = InvestigationTools(self.entries, {"cluster": "test", "namespace": "ns", "name": "worker"}, self.sources)

    def test_scope_and_argument_allowlist(self):
        for name, arguments in [("search_logs", {"pod": "other"}), ("resource_history", {"query": "up"}),
                                ("search_logs", {"terms": ["x"] * 4}), ("exec", {})]:
            with self.assertRaises(ValueError):
                self.kit.execute(name, arguments)
        self.sources.adapters.assert_not_called()

    def test_imports_cannot_query_live_and_omitted_candidates_still_work(self):
        self.entries[0]["incident"]["source_kind"] = "external"
        with self.assertRaises(ValueError):
            self.kit.execute("workload_state", {})
        omitted = self.kit.execute("review_omitted", {})
        self.assertEqual(omitted["matching_candidates"], 1)
        self.assertEqual(omitted["observations"][0]["count"], 1)

    def test_old_cluster_or_namespace_cannot_cross_scope(self):
        for config in ({"cluster_name": "other"}, {"cluster_name": "test", "namespaces": ["other"]}):
            self.sources.configuration.return_value = config
            with self.assertRaises(ValueError):
                self.kit.execute("resource_history", {})

    def test_baseline_falls_back_to_preceding_window_and_declares_uncertainty(self):
        result = self.kit.execute("compare_baseline", {})
        self.assertEqual(result["method"], "preceding_window")
        args = self.prom.collect_pod_metrics.call_args.args
        self.assertEqual((args[3] - args[2]).total_seconds(), 600)
        self.assertIn("Unverified", result["comparability"])

    def test_peer_selection_rejects_unrelated_workloads(self):
        self.kube.list_pods.return_value = [{"name": "foreign", "workload": "other", "ready": True}, {"name": "worker-2", "workload": "worker", "ready": True}]
        result = self.kit.execute("compare_baseline", {})
        self.assertEqual(result["reference_pod"], "worker-2")

    def test_literal_search_is_bounded_and_scoped(self):
        self.logs.collect_logs.return_value = []
        result = self.kit.execute("search_logs", {"terms": ["decoder"]})
        self.assertEqual(result["scanned_lines"], 0)
        self.assertEqual(self.logs.collect_logs.call_args.kwargs, {"limit": 300, "terms": ["decoder"]})
        self.assertEqual(self.logs.collect_logs.call_args.args[:2], ("ns", "worker-1"))

    def test_dedup_keeps_member_provenance(self):
        other = copy.deepcopy(self.entries[0])
        other["incident"]["incident_id"] = "two"
        context = episode_context({"episode_id": "episode"}, self.entries + [other])
        self.assertEqual(len(context["evidence"]), 1)
        self.assertEqual(len(context["evidence"][0]["provenance"]), 2)

    def test_diagnostic_codes_survive_reduction_and_secrets_do_not(self):
        self.assertNotEqual(template_for_message('exit_code=137 job=19'), template_for_message('exit_code=1 job=20'))
        self.assertEqual(template_for_message('exit_code=1 job=19'), template_for_message('exit_code=1 job=20'))
        masked = anonymize_text('{"password":"a value with spaces", "token":"abc"} Bearer abc.def')
        self.assertNotIn("a value", masked)
        self.assertNotIn("abc", masked)
        self.assertNotIn("token", scrub({"token": "abc", "reason": "known"}))
        self.assertEqual(metric_summary([{"metric": "empty", "values": [["now", "NaN"]]}]), [])
        groups = log_patterns([{"message": "exit_code=1"}, {"message": "exit_code=137"}])
        self.assertEqual(groups["matching_patterns"], 2)


if __name__ == "__main__":
    unittest.main()
