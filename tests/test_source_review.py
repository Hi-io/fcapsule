import json
import unittest

from fcapsule.reasoning.context_budget import estimate_tokens
from fcapsule.reasoning.source_review import SYSTEM, run_source_disconnected_review, validate_source_review


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def chat(self, request):
        self.requests.append(request)
        return {"content": json.dumps(self.response), "usage": {"prompt_tokens": 120, "completion_tokens": 45, "total_tokens": 165}}


class SourceDisconnectedReviewTests(unittest.TestCase):
    def setUp(self):
        self.context = {
            "episode_id": "episode-1", "alerts": [{"incident_id": "incident-1", "alertname": "TargetDown"}],
            "evidence": [{"id": "E001", "domain": "fault_event", "title": "TargetDown", "summary": "Target is down."}],
            "historical_candidates": [],
        }

    def test_review_uses_retained_context_and_does_not_need_a_live_tool(self):
        client = FakeClient({
            "sufficiency": "partially_sufficient",
            "answer": "The retained target record establishes a failed scrape, not its network cause.",
            "missing_discriminator": "The target error at incident time is not retained.",
            "supporting_evidence_ids": ["E001", "Q001"],
        })
        states = []

        result = run_source_disconnected_review(
            self.context,
            [{"id": "Q001", "tool": "scrape_discovery", "status": "completed", "result": {"active_targets": []}}],
            "Can the capsule distinguish a failed scrape from a missing target?", "test-model", 640,
            lambda state: states.append(dict(state)), client=client,
        )

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["result"]["sufficiency"], "partially_sufficient")
        self.assertEqual(result["usage"]["total_tokens"], 165)
        prompt = client.requests[0].messages[1]["content"]
        self.assertIn("retained_only", prompt)
        self.assertNotIn("likely_mechanism", prompt)
        self.assertTrue(states)

    def test_unavailable_citation_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_source_review({
                "sufficiency": "sufficient", "answer": "Answer", "missing_discriminator": "None",
                "supporting_evidence_ids": ["unknown"],
            }, {"E001"})

    def test_valid_attachment_reference_is_preserved_without_unmasking_unrelated_ids(self):
        reference = "A-attachment-12345678123456781234567812345678"
        result = validate_source_review({
            "sufficiency": "partially_sufficient", "answer": "Unconfirmed operator claim ffffffffffffffff",
            "missing_discriminator": "Independent telemetry", "supporting_evidence_ids": [reference],
        }, {reference})
        self.assertEqual(result["supporting_evidence_ids"], [reference])
        self.assertNotIn("ffffffffffffffff", result["answer"])

    def test_empty_record_can_abstain_but_cannot_claim_sufficiency(self):
        value = {"sufficiency": "unresolved", "answer": "No usable observation was retained.",
                 "missing_discriminator": "Incident-time measurements", "supporting_evidence_ids": []}
        self.assertEqual(validate_source_review(value, set())["supporting_evidence_ids"], [])
        for state in ("sufficient", "partially_sufficient"):
            with self.subTest(state=state), self.assertRaises(ValueError):
                validate_source_review({**value, "sufficiency": state}, set())

    def test_nested_prior_hypotheses_and_unavailable_checks_are_not_evidence(self):
        client = FakeClient({"sufficiency": "unresolved", "answer": "Only the current symptom is retained.",
            "missing_discriminator": "Historical measurements", "supporting_evidence_ids": ["E001"]})
        checks = [
            {"id": "Q001", "tool": "historical_episode", "status": "completed", "result": {"episode": {
                "episode_id": "prior", "prior_hypothesis": {"summary": "UnsupportedEarlierCause"}}}},
            {"id": "Q002", "tool": "search_logs", "status": "unavailable", "result": {"error": "UnusableFailure"}},
        ]
        result = run_source_disconnected_review(self.context, checks, "What is established?", "test-model", 600,
                                                 lambda state: None, client=client)
        self.assertEqual(result["status"], "ready")
        self.assertNotIn("UnsupportedEarlierCause", client.requests[0].messages[1]["content"])
        self.assertNotIn("UnusableFailure", client.requests[0].messages[1]["content"])
        self.assertNotIn("Q002", result["available_evidence_ids"])
        self.assertNotIn("UnsupportedEarlierCause", json.dumps(result["retained_checks"]))
        self.assertIn("prior_hypothesis", checks[0]["result"]["episode"])

    def test_review_request_includes_system_instruction_within_the_input_cap(self):
        self.context["evidence"] = [
            {"id": f"E{index:03d}", "domain": "log_template", "title": "Event",
             "summary": "bounded retained observation " + "x" * 1600}
            for index in range(16)
        ]
        client = FakeClient({
            "sufficiency": "unresolved", "answer": "The retained record does not establish a network cause.",
            "missing_discriminator": "The target error was not retained.", "supporting_evidence_ids": ["E000"],
        })
        result = run_source_disconnected_review(
            self.context, [], "Can the retained capsule establish the exact cause?", "test-model", 700,
            lambda state: None, max_prompt_tokens=1600, max_total_tokens=3500, client=client,
        )
        self.assertEqual(result["status"], "ready")
        request = client.requests[0]
        self.assertLessEqual(
            estimate_tokens(request.messages[0]["content"]) + estimate_tokens(request.messages[1]["content"]),
            1600,
        )
        self.assertGreater(result["token_budget"]["accounted_total_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
