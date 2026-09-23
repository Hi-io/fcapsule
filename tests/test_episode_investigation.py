import copy
import json
import unittest
from unittest.mock import Mock

from fcapsule.episode_investigation import SYSTEM, assessment_payload, run_investigation, validate_assessment
from fcapsule.investigation_tools import InvestigationTools, episode_context, log_patterns, metric_summary, scrub, stamp
from fcapsule.processing.anonymizer import anonymize_text, template_for_message
from fcapsule.reasoning.context_budget import estimate_tokens


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


class NoUsageClient(FakeClient):
    """A provider response without billing metadata must still consume the reserve."""

    def chat(self, request):
        self.requests.append(request)
        decision = next(self.decisions)
        if isinstance(decision, Exception):
            raise decision
        return {"content": json.dumps(decision), "usage": {}}


class InvestigationEngineTests(unittest.TestCase):
    def setUp(self):
        self.context = {"episode_id": "episode", "evidence": [{"id": "E1"}], "alerts": [{"incident_id": "one"}]}
        self.kit = Mock(pods=["worker"], CATALOG=InvestigationTools.CATALOG)
        self.kit.execute.return_value = {"observations": [{"reason": "Error"}]}
        self.progress = []

    def run_case(self, decisions, **kwargs):
        model_max_tokens = kwargs.pop("model_max_tokens", 1000)
        if decisions and isinstance(decisions[-1], dict) and decisions[-1].get("action") == "finish":
            decisions = [*decisions, copy.deepcopy(decisions[-1])]
        client = FakeClient(decisions)
        state = run_investigation(self.context, self.kit, "test-model", model_max_tokens,
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
        self.assertIn('"reason":"Error"', client.requests[1].messages[1]["content"])
        self.assertEqual(self.progress[0]["checks"][0]["status"], "running")
        self.assertEqual(state["source_retention"], "unknown")

    def test_invalid_citation_never_becomes_ready(self):
        state, _ = self.run_case([{"action": "finish", "assessment": assessment("Q999")}], max_checks=0)
        self.assertEqual(state["status"], "inconclusive")
        self.assertEqual(state["assessment"]["provenance"], "deterministic_abstention")
        self.assertEqual(state["usage"]["total_tokens"], 130)

    def test_basis_is_optional_bounded_and_uses_assessment_citations(self):
        original = assessment("E1")
        self.assertNotIn("basis", validate_assessment(original, {"E1"}, {"one"}))
        value = {**original, "basis": "  A decoder error with exit code 1 supports application failure, not an observed OOM.  ",
                 "basis_evidence_ids": ["invented"]}
        result = validate_assessment(value, {"E1"}, {"one"})
        self.assertEqual(result["basis"], value["basis"].strip())
        self.assertEqual(result["evidence_ids"], ["E1"])
        self.assertNotIn("basis_evidence_ids", result)
        self.assertEqual(len(validate_assessment({**original, "basis": "x" * 500}, {"E1"}, {"one"})["basis"]), 500)
        for invalid in (None, "", "  ", "x" * 501, [], {}, 1, True):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "basis"):
                validate_assessment({**original, "basis": invalid}, {"E1"}, {"one"})
        with self.assertRaisesRegex(ValueError, "unavailable evidence"):
            validate_assessment(value, {"E2"}, {"one"})

    def test_basis_is_scrubbed_in_draft_review_and_final_without_extra_calls(self):
        value = assessment()
        value["basis"] = "Repeated decoder failure supports an application error. password=never-retain-basis"
        state, client = self.run_case([{"action": "finish", "assessment": value}], max_checks=0)
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["policy_version"], "episode-investigation-1.17")
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(state["assessment"]["evidence_ids"], ["Q001"])
        self.assertNotIn("never-retain-basis", json.dumps(state))
        review = json.loads(client.requests[-1].messages[1]["content"])
        self.assertIn("basis", review["assessment_to_review"])
        self.assertNotIn("never-retain-basis", json.dumps(review))
        self.assertIn("assessment evidence_ids", review["instruction"])
        masked = validate_assessment({**assessment(), "basis": "x" * 492 + " token=x"}, {"Q001"}, {"one"})
        self.assertLessEqual(len(masked["basis"]), 500)

    def test_prompt_requires_discriminatory_facts_not_a_prescribed_diagnosis(self):
        for instruction in ("affected pod/resource and namespace", "capture-window qualifier", "same assessment",
                            "no uncited new claims", "missing discriminator", "alert_rule_logic",
                            "no required diagnosis", "same-signature prior counts do not prove"):
            self.assertIn(instruction, SYSTEM)

    def test_scope_and_recurrence_survive_full_2100_budget_with_basis_and_review(self):
        scope = {"namespace": "checkout-production", "pod": "checkout-worker-7f7946d9b6-xlm4t",
                 "service": "checkout-worker", "cluster": "production",
                 "alert_started_at": "2026-09-23T02:00:00Z",
                 "window": {"start": "2026-09-23T01:55:00Z", "end": "2026-09-23T02:05:00Z"}}
        self.context.update({
            "scope": scope,
            "recurrence": {"previous_count": 7},
            "live_capture": True,
            "evidence": [{"id": "E1", "domain": "log_template", "summary": "Exit code 1 after decoder failure."}]
                        + [{"id": f"E-old-{index}", "summary": "Other retained detail. " * 50} for index in range(40)],
            "priority_evidence_ids": ["E1"],
        })
        value = assessment("E1")
        value["basis"] = "Decoder failure before exit code 1 supports an application error; no OOM termination is observed."
        state, client = self.run_case([{"action": "finish", "assessment": value}], max_checks=1,
                                      max_prompt_tokens=2100, max_total_tokens=12000)
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["review"]["status"], "completed")
        self.assertEqual(len(client.requests), 2)
        for call, request in zip(state["calls"], client.requests):
            compact = json.loads(request.messages[1]["content"])["episode"]
            for field in ("pod", "namespace", "alert_started_at", "window"):
                self.assertEqual(compact["scope"][field], scope[field])
            self.assertEqual(compact["recurrence"]["previous_count"], 7)
            self.assertIn("not evidence of the same cause", compact["recurrence"]["limitation"])
            self.assertIn("E1", call["visible_evidence_ids"])
            self.assertLessEqual(estimate_tokens(request.messages[0]["content"]) +
                                 estimate_tokens(request.messages[1]["content"]), 2100)
        self.assertEqual(state["assessment"]["basis"], value["basis"])

    def test_rule_metric_survives_2100_request_and_review_with_maximum_basis(self):
        self.context.update({
            "scope": {"pod": "worker-1", "namespace": "production",
                      "alert_started_at": "2026-09-23T02:00:00Z",
                      "window": {"start": "2026-09-23T01:55:00Z", "end": "2026-09-23T02:05:00Z"}},
            "recurrence": {"previous_count": 5},
            "evidence": [{"id": "E-rule", "signal_origin": "alert_rule", "domain": "metric_anomaly",
                          "summary": "Observed matching samples of the captured rule expression.",
                          "metric_observation": {
                              "metric": "worker_errors_total", "expression": "sum(rate(worker_errors_total[5m]))",
                              "threshold": 3, "operator": ">", "unit": "errors/s",
                              "rule": {"name": "WorkerErrors", "duration": 300.0, "keep_firing_for": 0.0},
                              "labels": {"namespace": "production", "pod": "worker-1"},
                              "time_range": {"start": "2026-09-23T01:55:00Z", "end": "2026-09-23T02:05:00Z"},
                              "condition": {"observed_samples": 30, "matching_samples": 20, "missing_samples": 10,
                                            "min": 1, "max": 5, "incident_observed_samples": 15, "incident_matching_samples": 12,
                                            "latest": {"timestamp": "2026-09-23T02:05:00Z", "value": 4}},
                          }}],
        })
        value = assessment("E-rule")
        value["basis"] = ("Observed matching samples support the detected symptom, not its cause. " * 8)[:500]
        state, client = self.run_case([{"action": "finish", "assessment": value}], max_checks=1,
                                      max_prompt_tokens=2100, max_total_tokens=12000)
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["review"]["status"], "completed")
        self.assertEqual(len(client.requests), 2)
        for request in client.requests:
            compact = json.loads(request.messages[1]["content"])["episode"]
            self.assertEqual(compact["scope"]["pod"], "worker-1")
            self.assertEqual(compact["scope"]["namespace"], "production")
            metric = compact["evidence"][0]["metric_observation"]
            self.assertEqual(metric["condition"]["matching_samples"], 20)
            self.assertEqual(metric["condition"]["missing_samples"], 10)
            self.assertEqual(metric["rule"]["duration"], 300.0)
            self.assertEqual(metric["operator"], ">")
            self.assertEqual(metric["threshold"], 3)
            self.assertIn("rate(", metric["expression"])
            self.assertLessEqual(estimate_tokens(request.messages[0]["content"]) +
                                 estimate_tokens(request.messages[1]["content"]), 2100)

    def test_attachment_references_survive_scrubbing_in_review_and_final_assessment(self):
        ref = "A-attachment-0123456789abcdef0123456789abcdef"
        self.context["evidence"] = [{"id": ref, "summary": "Target is down"}]
        candidate = assessment(ref)
        candidate["summary"] = "Target is down password=never-retain-this"
        state, client = self.run_case([{"action": "finish", "assessment": candidate}], max_checks=0)
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["assessment"]["evidence_ids"], [ref])
        self.assertEqual(state["assessment"]["hypotheses"][0]["evidence_ids"], [ref])
        review = json.loads(client.requests[-1].messages[1]["content"])
        self.assertEqual(review["assessment_to_review"]["evidence_ids"], [ref])
        for call in state["calls"]:
            self.assertEqual(call["decision"]["assessment"]["evidence_ids"], [ref])
        self.assertNotIn("never-retain-this", json.dumps(state))
        self.assertNotEqual(scrub(ref), ref)
        self.assertNotEqual(scrub("ffffffffffffffff", reference_ids={ref}), "ffffffffffffffff")
        with self.assertRaisesRegex(ValueError, "unavailable evidence"):
            validate_assessment(candidate, {"E1"}, {"one"})

    def test_revision_evidence_survives_full_request_budget_including_review(self):
        self.context["evidence"] = [
            {"id": f"E-{index}", "domain": "log_template", "revision_priority": True,
             "summary": "Error connection timeout. " * 20}
            for index in range(40)
        ] + [{"id": "A-new", "domain": "image_evidence", "revision_addition": True,
              "summary": "Observed endpoint returned HTTP 404.",
              "limitation": "Visible state only; no cause established.",
              "time_range": {"observed_at": "2026-09-23T02:00:00Z"}}]
        self.context["priority_evidence_ids"] = [item["id"] for item in self.context["evidence"]]
        state, client = self.run_case([{"action": "finish", "assessment": assessment("A-new")}],
                                      max_prompt_tokens=2100, max_checks=1, max_total_tokens=12000)
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["review"]["status"], "completed")
        self.assertEqual(len(client.requests), 2)
        for call, request in zip(state["calls"], client.requests):
            self.assertIn("A-new", call["visible_evidence_ids"])
            self.assertLessEqual(call["estimated_prompt_tokens"], 2100)
            evidence = json.loads(request.messages[1]["content"])["episode"]["evidence"]
            self.assertEqual(evidence[0]["id"], "A-new")
            self.assertIn("404", evidence[0]["summary"])

    def test_unavailable_query_is_not_citable(self):
        self.kit.execute.side_effect = RuntimeError("password=never-persist-this")
        state, _ = self.run_case([{"action": "finish", "assessment": assessment()}], max_checks=0)
        self.assertEqual(state["checks"][0]["status"], "unavailable")
        self.assertEqual(state["status"], "inconclusive")
        self.assertEqual(state["assessment"]["hypotheses"][0]["status"], "unresolved")
        self.assertNotIn("never-persist", json.dumps(state))

    def test_provider_failure_keeps_checks_and_marks_unknown_usage(self):
        state, _ = self.run_case([RuntimeError("provider failed password=do-not-save")])
        self.assertEqual(state["status"], "inconclusive")
        self.assertTrue(state["assessment"]["next_action"])
        self.assertFalse(state["usage"]["complete"])
        self.assertEqual(state["checks"][0]["status"], "completed")
        self.assertNotIn("do-not-save", json.dumps(state))

    def test_one_schema_repair_uses_existing_budget_and_keeps_rejected_decision(self):
        state, client = self.run_case([{"action": "check", "tool": "review_omitted", "arguments": {}, "question": "Other failures?", "distinguishes": "A competing cause"},
                                       {"action": "finish", "assessment": assessment("invented")},
                                       {"action": "finish", "assessment": assessment()}])
        self.assertEqual(state["status"], "ready")
        self.assertEqual(len(client.requests), 4)
        self.assertEqual(client.requests[0].reasoning_effort, "none")
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
        self.assertEqual(client.requests[-1].reasoning_effort, "none")
        instruction = json.loads(client.requests[-1].messages[1]["content"])["instruction"]
        self.assertIn("convert memory quantities to bytes", instruction)
        self.assertIn("actual peak remains unsampled", instruction)
        self.assertEqual(len(client.requests), 2)
        self.assertFalse(any(item["status"] == "ready" and item["assessment"] == assessment() for item in self.progress))

    def test_validated_draft_survives_an_optional_review_failure(self):
        state, _ = self.run_case([
            {"action": "finish", "assessment": assessment()},
            RuntimeError("review provider failed password=do-not-save"),
        ], max_checks=0)

        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["assessment"]["likely_mechanism"], assessment()["likely_mechanism"])
        self.assertEqual(state["review"]["status"], "unavailable")
        self.assertFalse(state["usage"]["complete"])
        self.assertNotIn("do-not-save", json.dumps(state))

    def test_review_schema_omission_gets_one_bounded_repair_attempt(self):
        malformed = assessment()
        malformed["hypotheses"][0].pop("evidence_ids")
        corrected = assessment()
        state, client = self.run_case([
            {"action": "finish", "assessment": assessment()},
            {"action": "finish", "assessment": malformed},
            {"action": "finish", "assessment": corrected},
        ], max_checks=0)
        self.assertEqual(state["status"], "ready")
        self.assertTrue(state["review"]["schema_repair"])
        self.assertEqual(client.requests[-1].reasoning_effort, "none")
        self.assertEqual(state["calls"][-1]["phase"], "evidence_review_repair")
        self.assertIn("validation_error", state["calls"][-2])

    def test_reserved_review_can_repair_excess_known_citations_without_more_calls(self):
        refs = [f"E{index}" for index in range(10)]
        self.context["evidence"] = [{"id": ref} for ref in refs]
        draft = assessment()
        draft["evidence_ids"] = refs
        corrected = copy.deepcopy(draft)
        corrected["evidence_ids"] = refs[:8]
        state, client = self.run_case([{"action": "finish", "assessment": draft},
                                      {"action": "finish", "assessment": corrected}], max_checks=0)
        self.assertEqual(state["status"], "ready")
        self.assertTrue(state["review"]["schema_repair"])
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(len(state["draft_assessment"]["evidence_ids"]), 10)
        self.assertEqual(len(state["assessment"]["evidence_ids"]), 8)
        self.assertFalse(any(item["status"] == "ready" and item["assessment"] == draft for item in self.progress))

    def test_initial_nested_citation_omission_uses_the_reserved_evidence_review(self):
        draft = assessment()
        draft["hypotheses"][0]["evidence_ids"] = []
        state, client = self.run_case([
            {"action": "finish", "assessment": draft},
            {"action": "finish", "assessment": assessment()},
        ], max_checks=0)
        self.assertEqual(state["status"], "ready")
        self.assertTrue(state["review"]["schema_repair"])
        self.assertEqual(len(client.requests), 2)
        self.assertIn("validation_error", state["calls"][0])
        self.assertEqual(state["calls"][-1]["phase"], "evidence_review")

    def test_overflow_with_unknown_citations_is_not_silently_repaired(self):
        draft = assessment()
        draft["evidence_ids"] = ["Q001"] * 8 + ["invented"]
        state, client = self.run_case([{"action": "finish", "assessment": draft}], max_checks=0)
        self.assertEqual(state["status"], "inconclusive")
        self.assertNotEqual(state["assessment"], draft)
        self.assertEqual(len(client.requests), 1)

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

    def test_review_accepts_unambiguous_assessment_only_envelope(self):
        corrected = assessment()
        corrected["summary"] = "Corrected after evidence review."
        state, _ = self.run_case([
            {"action": "finish", "assessment": assessment()},
            {"type": "json_object", "assessment": corrected},
        ], max_checks=0)
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["assessment"]["summary"], corrected["summary"])
        self.assertIn("Accepted assessment-only review envelope", state["calls"][-1]["schema_adjustments"])

    def test_review_accepts_direct_assessment_without_inventing_fields(self):
        corrected = assessment()
        corrected["summary"] = "Direct corrected assessment."
        state, _ = self.run_case([
            {"action": "finish", "assessment": assessment()},
            corrected,
        ], max_checks=0)
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["assessment"]["summary"], corrected["summary"])
        self.assertIn("Accepted direct assessment review", state["calls"][-1]["schema_adjustments"])

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

    def test_no_history_placeholder_is_omitted_without_rejecting_assessment(self):
        value = assessment()
        value["historical_comparison"] = {
            "episode_id": "none", "status": "insufficient_evidence",
            "summary": "No historical candidates were supplied.", "evidence_ids": ["Q001"],
        }
        result = validate_assessment(value, {"Q001"}, {"one"})
        self.assertNotIn("historical_comparison", result)

    def test_unavailable_history_claim_is_omitted_without_overturning_current_assessment(self):
        value = assessment()
        value["historical_comparison"] = {
            "episode_id": "invented-prior-episode", "status": "similar_mechanism",
            "summary": "This unprovided episode is allegedly related.", "evidence_ids": ["Q001"],
        }

        result = validate_assessment(value, {"Q001"}, {"one"})

        self.assertNotIn("historical_comparison", result)

    def test_last_investigation_turn_reserves_tokens_for_structured_output(self):
        state, client = self.run_case([{"action": "finish", "assessment": assessment()}], max_checks=0, model_max_tokens=2000)
        self.assertEqual(state["status"], "ready")
        self.assertEqual(client.requests[0].reasoning_effort, "none")
        self.assertEqual(client.requests[0].max_tokens, 1200)
        self.assertEqual(client.requests[1].max_tokens, 1200)
        payload = json.loads(client.requests[0].messages[1]["content"])
        self.assertEqual(payload["tools"], {})
        self.assertEqual(payload["allowed_pods"], [])

    def test_full_request_including_instructions_and_catalogue_fits_the_input_cap(self):
        self.context["evidence"] = [
            {"id": f"E{index}", "domain": "log_template", "summary": "diagnostic context " + "x" * 1800}
            for index in range(16)
        ]
        state, client = self.run_case([{"action": "finish", "assessment": assessment()}], max_checks=0,
                                      max_total_tokens=5000, max_prompt_tokens=1600)
        self.assertEqual(state["status"], "ready")
        for request in client.requests:
            self.assertLessEqual(
                estimate_tokens(request.messages[0]["content"]) + estimate_tokens(request.messages[1]["content"]),
                1600,
            )

    def test_required_observations_and_citations_are_compacted_before_the_final_request(self):
        self.context.update({
            "live_capture": True,
            "historical_candidates": [{"episode_id": "older-episode", "summary": "x" * 1800}],
            "evidence": [{"id": "E1", "domain": "log_template", "summary": "diagnostic context " + "x" * 1800}],
        })
        self.kit.execute.return_value = {
            "observations": [{"kind": "PodSpec", "resources": [{"limits": {"memory": "160Mi"}}],
                              "container_states": [{"last_state": {"terminated": {"reason": "OOMKilled"}}}]}],
            "patterns": [{"count": 5000, "examples": [{"message": "x" * 4000}]}],
        }
        draft = assessment("E1")
        draft["historical_comparison"] = {
            "episode_id": "older-episode",
            "status": "insufficient_evidence",
            "summary": "The retained current evidence does not establish a repeated mechanism.",
            "evidence_ids": ["E1"],
        }
        state, client = self.run_case([{"action": "finish", "assessment": draft}], max_checks=0,
                                      max_total_tokens=5000, max_prompt_tokens=1600)
        self.assertEqual(state["status"], "ready")
        self.assertGreaterEqual(len(state["checks"]), 3)
        for request in client.requests:
            self.assertLessEqual(
                estimate_tokens(request.messages[0]["content"]) + estimate_tokens(request.messages[1]["content"]),
                1600,
            )

    def test_missing_provider_usage_still_blocks_unreserved_follow_up_calls(self):
        decision = {"action": "check", "tool": "resource_history", "arguments": {},
                    "question": "Resource pressure?", "distinguishes": "CPU or memory"}
        client = NoUsageClient([decision] * 5)
        state = run_investigation(
            self.context, self.kit, "test-model", 1000, lambda state: None, client=client,
            max_checks=4, max_total_tokens=4000, max_prompt_tokens=1600,
        )
        budget = state["token_budget"]
        self.assertFalse(state["usage"]["complete"])
        self.assertLess(len(client.requests), 5)
        self.assertGreater(budget["accounted_total_tokens"], 0)
        self.assertEqual(budget["accounted_total_tokens"], budget["reserved_total_tokens"])
        self.assertLessEqual(budget["accounted_total_tokens"], budget["maximum_total_tokens"])

    def test_hard_call_budget_and_disallowed_tools(self):
        decision = {"action": "check", "tool": "resource_history", "arguments": {}, "question": "Resource pressure?", "distinguishes": "CPU or memory"}
        state, client = self.run_case([decision, decision], max_checks=1)
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(len(state["checks"]), 2)
        self.assertEqual(state["status"], "inconclusive")
        self.assertEqual(state["stop_reason"], "validated_assessment_unavailable")
        self.kit.execute.reset_mock()
        state, _ = self.run_case([{**decision, "tool": "kubectl_exec"}])
        self.assertEqual(self.kit.execute.call_count, 1)  # Preservation only.

    def test_multi_alert_relationship_omission_defaults_to_an_evidence_cited_abstention(self):
        normalized = validate_assessment(assessment(), {"Q001"}, {"one", "two"})
        self.assertEqual(normalized["connections"], [{
            "from": "one", "to": "two", "relationship": "no_link_established",
            "reason": "No causal relationship is established by the retained observations.",
            "evidence_ids": ["Q001"], "provenance": "structural_default",
        }])
        value = assessment()
        value["connections"] = [{"from": "one", "to": "two", "relationship": "no_link_established", "reason": "Timing only", "evidence_ids": ["Q001"]}]
        connection = validate_assessment(value, {"Q001"}, {"one", "two"})["connections"][0]
        self.assertEqual(connection["provenance"], "model")

    def test_uncited_model_connection_becomes_a_non_causal_structural_abstention(self):
        value = assessment()
        value["connections"] = [{
            "from": "one", "to": "two", "relationship": "possibly_related",
            "reason": "The model did not provide a retained reference.", "evidence_ids": [],
        }]

        connection = validate_assessment(value, {"Q001"}, {"one", "two"})["connections"][0]

        self.assertEqual(connection["relationship"], "no_link_established")
        self.assertEqual(connection["evidence_ids"], ["Q001"])
        self.assertEqual(connection["provenance"], "structural_default")

    def test_unresolved_hypothesis_without_citations_inherits_assessment_evidence(self):
        value = assessment()
        value["hypotheses"] = [{
            "explanation": "The retained evidence does not establish an alternative mechanism.",
            "status": "unresolved",
            "reason": "No separate discriminator was retained for this alternative.",
            "evidence_ids": [],
        }]

        normalized = validate_assessment(value, {"Q001"}, {"one"})

        self.assertEqual(normalized["hypotheses"][0]["evidence_ids"], ["Q001"])

    def test_repeated_alert_identity_does_not_require_an_artificial_relationship(self):
        self.context["alerts"] = [
            {"incident_id": "one", "alert_identity": "TargetMissing"},
            {"incident_id": "two", "alert_identity": "TargetMissing"},
        ]
        state, _ = self.run_case([{"action": "finish", "assessment": assessment()}], max_checks=0)
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["assessment"]["connections"], [])

    def test_live_log_query_is_preserved_before_the_model_concludes(self):
        self.context.update(live_capture=True, evidence=[{"id": "E1", "domain": "log_template"}])
        state, _ = self.run_case([{"action": "finish", "assessment": assessment()}], max_checks=0)
        self.assertEqual(state["status"], "ready")
        self.assertEqual([item["tool"] for item in state["checks"]], ["workload_state", "search_logs"])
        self.assertTrue(state["checks"][1]["required_observation"])

    def test_historical_candidate_is_preserved_without_spending_an_optional_model_check(self):
        self.context["historical_candidates"] = [{"episode_id": "prior-episode"}]
        value = assessment("Q002")
        value["historical_comparison"] = {
            "episode_id": "prior-episode", "status": "insufficient_evidence",
            "summary": "The retained candidate has not established a shared mechanism.", "evidence_ids": ["Q002"],
        }
        state, _ = self.run_case([{"action": "finish", "assessment": value}], max_checks=0)
        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["checks"][-1]["tool"], "historical_episode")
        self.assertTrue(state["checks"][-1]["required_observation"])
        self.assertEqual(state["token_budget"]["maximum_checks"], 0)

    def test_discovery_capture_preserves_selector_evidence_before_concluding(self):
        self.context.update(
            live_capture=True,
            evidence=[{"id": "E1", "domain": "log_template"}],
            alerts=[{"incident_id": "one", "labels": {"target_service": "app-metrics", "target_workload": "worker"}}],
        )

        state, _ = self.run_case([{"action": "finish", "assessment": assessment("Q002")}], max_checks=0)

        self.assertEqual(state["status"], "ready")
        self.assertEqual([item["tool"] for item in state["checks"]], ["workload_state", "scrape_discovery"])
        self.assertTrue(all(item["automatic_preservation"] for item in state["checks"]))

    def test_repeated_automatic_check_uses_the_reserved_final_decision(self):
        self.context.update(
            live_capture=True,
            evidence=[{"id": "E1", "domain": "log_template"}],
            alerts=[{"incident_id": "one", "labels": {"target_service": "app-metrics"}}],
        )
        repeated = {
            "action": "check", "tool": "scrape_discovery", "arguments": {},
            "question": "Which selector missed the target?", "distinguishes": "A discovery failure or workload failure",
        }
        state, client = self.run_case([repeated, {"action": "finish", "assessment": assessment("Q002")}], max_checks=1)
        self.assertEqual(state["status"], "ready")
        self.assertEqual(len(state["checks"]), 2)
        self.assertEqual(client.requests[0].reasoning_effort, "none")
        self.assertEqual(state["calls"][0]["validation_error"], "Selected check has already completed")


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
        self.assertEqual(self.logs.collect_logs.call_args.kwargs, {"limit": 300, "terms": ["decoder"], "focus": self.kit.focus_time})
        self.assertEqual(self.logs.collect_logs.call_args.args[:2], ("ns", "worker-1"))

    def test_scrape_discovery_explains_the_selection_chain_without_claiming_a_typo(self):
        self.prom.scrape_targets.return_value = {
            "active": [{"state": "active", "health": "down", "pod": "worker-1", "service": "worker"}],
            "dropped": [{"state": "dropped", "pod": "worker-2", "service": "worker"}],
        }
        self.kube.monitoring_resources.return_value = [
            {"kind": "ServiceMonitor", "name": "worker", "match_labels": {"metrics": "enabled"}},
            {"kind": "PodMonitor", "name": "worker-pods", "match_labels": {"metrics": "pod-enabled"}},
        ]
        self.kube.list_services.return_value = [
            {"name": "worker", "labels": {"metrics": "enabled"}, "selector": {"app": "worker"}},
            {"name": "other", "labels": {"metrics": "disabled"}, "selector": {}},
        ]
        self.kube.list_pods.return_value = [
            {"name": "worker-1", "workload": "worker", "labels": {"metrics": "pod-enabled"}},
            {"name": "worker-2", "workload": "worker", "labels": {"metrics": "misspelled"}},
        ]

        result = self.kit.execute("scrape_discovery", {})

        self.assertEqual(result["active_targets"][0]["health"], "down")
        self.assertEqual(result["dropped_targets"][0]["state"], "dropped")
        self.assertEqual(result["monitor_selection"][0]["matched_services"], ["worker"])
        self.assertEqual(result["monitor_selection"][1]["matched_pods"], ["worker-1"])
        self.assertIn("ServiceMonitor selectors apply to Service labels", result["limitation"])

    def test_alert_rule_logic_keeps_detection_separate_from_root_cause(self):
        self.entries[0]["report"]["fault_alerts"] = [{
            "name": "WorkerMissingMetrics", "rule": {"query": "absent_over_time(up{job=\"worker\"}[5m])"},
        }]
        self.kit.alert_names = {"WorkerMissingMetrics"}
        self.prom.alert_rules.return_value = {"WorkerMissingMetrics": {"query": "absent_over_time(up{job=\"worker\"}[5m])"}}

        result = self.kit.execute("alert_rule_logic", {})

        self.assertEqual(result["current_definitions"]["WorkerMissingMetrics"]["query"], "absent_over_time(up{job=\"worker\"}[5m])")
        self.assertIn("does not establish the underlying cause", result["limitation"])

    def test_dependency_checks_require_a_declared_service(self):
        self.kube.list_pods.return_value = [{"name": "worker-1", "workload": "worker"}]
        self.kube.declared_services.return_value = [{"service": "inventory", "namespace": "ns"}]
        with self.assertRaisesRegex(ValueError, "not declared"):
            self.kit.execute("dependency_evidence", {"service": "unrelated"})
        self.kube.service_pods.assert_not_called()
        self.logs.collect_logs.assert_not_called()

    def test_dependency_query_is_bounded_and_keeps_partial_evidence(self):
        self.kube.list_pods.return_value = [{"name": "worker-1", "workload": "worker"}]
        self.kube.declared_services.return_value = [{"service": "inventory", "namespace": "ns"}]
        self.kube.service_pods.return_value = [{"name": "inventory-1"}, {"name": "inventory-2"}]
        self.logs.collect_logs.return_value = [{"message": '{"mysql_error_code":1054}', "@timestamp": "now"}]
        self.prom.collect_pod_metrics.side_effect = RuntimeError("password=private")
        self.kube.configuration_snapshot.return_value = [{"kind": "PodSpec", "name": "inventory-1"}]
        result = self.kit.execute("dependency_evidence", {"service": "inventory", "terms": ["1054"]})
        self.assertEqual(result["matching_pods"], 2)
        self.assertEqual(result["unavailable_sources"], ["Prometheus"])
        self.assertEqual(result["patterns"][0]["fields"]["mysql_error_code"], "1054")
        self.assertEqual(self.logs.collect_logs.call_args.args[:2], ("ns", "inventory-1"))
        self.assertEqual(self.logs.collect_logs.call_args.kwargs, {"limit": 200, "terms": ["1054"], "focus": self.kit.focus_time})
        self.assertIn("not historical", result["limitation"])
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(self.kit.pods, ["worker-1"])

    def test_sql_error_codes_do_not_collapse_into_one_pattern(self):
        groups = log_patterns([{"message": '{"mysql_error_code":1054}'}, {"message": '{"mysql_error_code":1205}'}])
        self.assertEqual(groups["matching_patterns"], 2)

    def test_post_alert_metrics_do_not_mix_in_healthy_baseline(self):
        series = [{"metric": "connections", "values": [["2026-09-20T12:00:00Z", 1],
                  ["2026-09-20T12:05:00Z", 40], ["2026-09-20T12:06:00Z", 40]]}]
        result = metric_summary(series, stamp("2026-09-20T12:04:00Z"))[0]
        self.assertEqual(result["min"], 1)
        self.assertEqual(result["at_or_after_latest_alert"]["min"], 40)
        self.assertEqual(result["at_or_after_latest_alert"]["samples"], 2)
        self.assertEqual(metric_summary(series, stamp("2026-09-20T12:10:00Z"))[0]["at_or_after_latest_alert"], {"samples": 0})

    def test_log_focus_uses_latest_member_alert(self):
        self.entries[0]["incident"]["started_at"] = "2026-09-20T12:02:00Z"
        later = copy.deepcopy(self.entries[0])
        later["incident"]["started_at"] = "2026-09-20T12:06:00Z"
        kit = InvestigationTools([*self.entries, later], self.kit.application, self.sources)
        self.logs.collect_logs.return_value = []
        kit.execute("search_logs", {"terms": ["connection"]})
        self.assertEqual(self.logs.collect_logs.call_args.kwargs["focus"], stamp("2026-09-20T12:06:00Z"))

    def test_dedup_keeps_member_provenance(self):
        other = copy.deepcopy(self.entries[0])
        other["incident"]["incident_id"] = "two"
        context = episode_context({"episode_id": "episode"}, self.entries + [other])
        self.assertEqual(len(context["evidence"]), 1)
        self.assertEqual(len(context["evidence"][0]["provenance"]), 2)

    def test_episode_context_prioritizes_the_current_primary_incident(self):
        earlier = copy.deepcopy(self.entries[0])
        earlier["incident"]["incident_id"] = "earlier"
        earlier["incident"]["started_at"] = "2026-09-20T12:00:00Z"
        current = copy.deepcopy(self.entries[0])
        current["incident"]["incident_id"] = "current"
        current["incident"]["started_at"] = "2026-09-20T12:08:00Z"
        current["report"]["supporting_evidence"] = [{
            "evidence_id": "current-log", "type": "log_template", "title": "Current failure marker",
            "summary": "The newly captured diagnostic error.", "time_range": {}, "representative_lines": [],
        }]

        context = episode_context({"episode_id": "episode", "primary_incident_id": "current"}, [earlier, current])

        self.assertEqual(context["alerts"][0]["incident_id"], "current")
        self.assertEqual(context["evidence"][0]["title"], "Current failure marker")
        self.assertIn(context["evidence"][0]["id"], context["priority_evidence_ids"])

        focused = episode_context({"episode_id": "episode", "primary_incident_id": "current"}, [earlier, current], "earlier")
        self.assertEqual(focused["alerts"][0]["incident_id"], "earlier")

    def test_active_alert_does_not_inherit_legacy_capture_window_end(self):
        self.entries[0]["incident"].update(status="firing", ended_at="2026-09-20T12:10:00Z")
        context = episode_context({"episode_id": "episode"}, self.entries)
        self.assertIsNone(context["alerts"][0]["ended_at"])
        self.assertIn("Independent failure phases", context["grouping_basis"])

    def test_scope_is_primary_retained_identity_with_field_masking(self):
        old = copy.deepcopy(self.entries[0])
        old["incident"]["incident_id"] = "old"
        old["capsule"]["case"].update(pod="old-pod", namespace="old-ns")
        current = copy.deepcopy(self.entries[0])
        current["incident"].update(incident_id="current", started_at="2026-09-23T02:00:00Z",
                                   resource={"kind": "Pod", "name": "worker-1", "annotations": "private-resource"})
        current["capsule"]["case"].update(namespace="current-ns", cluster="prod", service="worker",
                                         labels={"private_label": "private-label"}, credentials="private-case")
        current["capsule"]["case"]["window"]["private"] = "private-window"
        current["report"]["incident"].update(namespace="stale-report-ns", pod="stale-report-pod")
        before = copy.deepcopy([old, current])
        context = episode_context({"episode_id": "episode", "primary_incident_id": "current"}, [old, current])
        self.assertEqual(context["scope"], {
            "namespace": "current-ns", "pod": "worker-1", "cluster": "prod", "service": "worker",
            "resource": {"kind": "Pod", "name": "worker-1"}, "alert_started_at": "2026-09-23T02:00:00Z",
            "window": {"start": "2026-09-20T12:00:00Z", "end": "2026-09-20T12:10:00Z"},
        })
        self.assertEqual([old, current], before)
        self.assertNotIn("private", json.dumps(context["scope"]))
        focused = episode_context({"episode_id": "episode", "primary_incident_id": "current"}, [old, current], "old")
        self.assertEqual(focused["scope"]["pod"], "old-pod")
        self.assertEqual(focused["scope"]["namespace"], "old-ns")

    def test_scope_report_resource_fallback_is_explicit_bounded_and_scrubbed(self):
        self.entries[0]["capsule"]["case"] = {}
        self.entries[0]["report"]["incident"].update({
            "namespace": "report-ns", "service": "worker password=never-scope",
            "cluster": "c" * 900, "resource": {"kind": "Pod", "name": "report-pod", "token": "never-resource"},
        })
        scope = episode_context({"episode_id": "episode"}, self.entries)["scope"]
        self.assertEqual(scope["pod"], "report-pod")
        self.assertEqual(scope["namespace"], "report-ns")
        self.assertLessEqual(len(scope["cluster"]), 253)
        self.assertNotIn("never-", json.dumps(scope))
        self.entries[0]["report"]["incident"] = {"summary": "pod=guessed-pod namespace=guessed-ns"}
        scope = episode_context({"episode_id": "episode"}, self.entries)["scope"]
        self.assertNotIn("pod", scope)
        self.assertNotIn("namespace", scope)

    def test_recurrence_exposes_only_bounded_prior_candidate_count(self):
        for count in (0, 4, 9999, 10000):
            with self.subTest(count=count):
                episode = {"episode_id": "episode", "recurrence": {
                    "previous_count": count, "occurrence_count": 10001, "pattern_id": "private-pattern",
                    "candidates": [{"episode_id": "prior", "cause": "unsupported cause"}],
                    "prior_hypothesis": "unsupported diagnosis",
                }}
                recurrence = episode_context(episode, self.entries)["recurrence"]
                self.assertEqual(set(recurrence), {"previous_count", "count_capped", "limitation"})
                self.assertEqual(recurrence["previous_count"], min(count, 9999))
                self.assertEqual(recurrence["count_capped"], count > 9999)
                self.assertIn("not evidence of the same cause", recurrence["limitation"])
        for invalid in (None, True, -1, "4", 1.5, []):
            with self.subTest(invalid=invalid):
                context = episode_context({"episode_id": "episode", "recurrence": {"previous_count": invalid}}, self.entries)
                self.assertEqual(context["recurrence"], {})

    def test_selected_rule_metric_propagates_without_raw_points_and_distinct_samples_do_not_deduplicate(self):
        first = copy.deepcopy(self.entries[0])
        item = first["report"]["supporting_evidence"][0]
        item.update(signal_origin="alert_rule", series_id="rule-series", metric_observation={
            "metric": "alert_rule_value", "expression": 'sum(worker_errors_total{namespace="ns"})',
            "operator": ">", "threshold": 3,
            "rule": {"name": "WorkerErrors", "duration": "5m", "file": "never-private-file"},
            "condition": {"observed_samples": 4, "matching_samples": 3, "missing_samples": 1, "max": 5},
            "values": [["now", "never-raw-point"]], "private": "never-extra-field",
        })
        other = copy.deepcopy(first)
        other["incident"]["incident_id"] = "other"
        other["report"]["supporting_evidence"][0]["metric_observation"]["condition"]["max"] = 6
        context = episode_context({"episode_id": "episode"}, [first, other])
        self.assertEqual(len(context["evidence"]), 2)
        self.assertNotEqual(context["evidence"][0]["id"], context["evidence"][1]["id"])
        for row in context["evidence"]:
            self.assertEqual(row["signal_origin"], "alert_rule")
            self.assertEqual(row["series_id"], "rule-series")
            self.assertEqual(row["metric_observation"]["threshold"], 3)
            self.assertEqual(row["metric_observation"]["rule"]["duration"], "5m")
            self.assertIn("not proof", row["metric_observation"]["condition"]["limitation"])
            self.assertNotIn("never-", json.dumps(row))

    def test_unavailable_rule_metrics_remain_a_citable_limitation_not_a_measurement(self):
        self.entries[0]["report"]["supporting_evidence"][0]["alert_metric_evidence"] = {
            "status": "unavailable", "reason": "no_finite_samples", "rule": {"private": "never-private"},
        }
        row = episode_context({"episode_id": "episode"}, self.entries)["evidence"][0]
        self.assertIn("metric capture unavailable: no_finite_samples", row["limitation"])
        self.assertNotIn("metric_observation", row)
        self.assertNotIn("never-private", json.dumps(row))

    def test_episode_context_retains_only_explicit_discovery_identities(self):
        self.entries[0]["report"]["fault_alerts"] = [
            {"name": "MetricsMissing", "rule": {"labels": {
                "service": "orders-api", "target_service": "app-metrics", "target_workload": "orders-api",
                "unrelated": "not-used",
            }}}
        ]

        context = episode_context({"episode_id": "episode"}, self.entries)

        self.assertEqual(context["alerts"][0]["labels"], {"target_service": "app-metrics", "target_workload": "orders-api"})
        self.assertEqual(context["alerts"][0]["alert_identity"], "MetricsMissing")

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
