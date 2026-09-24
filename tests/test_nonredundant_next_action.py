import json
import unittest

from fcapsule.episode_investigation import (
    EVIDENCE_REVIEW_SYSTEM,
    REVIEW_INSTRUCTION,
    repeats_completed_check,
    run_investigation,
)
from fcapsule.investigation_tools import InvestigationTools


class FakeClient:
    provider = "deepseek"

    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.requests = []

    def chat(self, request):
        self.requests.append(request)
        return {
            "content": json.dumps(next(self.decisions)),
            "provider": self.provider,
            "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
        }


class FakeTools:
    CATALOG = InvestigationTools.CATALOG
    pods = ["worker-1"]

    def execute(self, name, arguments):
        if name == "workload_state":
            return {"observed_at": "2026-09-24T13:46:10Z", "observations": [{
                "kind": "PodSpec", "name": "worker-1", "ready": True,
                "resources": [{"limits": {"memory": "160Mi"}}],
                "container_states": [{"restart_count": 0}],
            }]}
        if name == "search_logs":
            return {"patterns": [], "limitation": "Bounded incident-window sample."}
        if name == "historical_episode":
            return {"observations": [{"summary": "A prior CPU-limit episode on another worker."}]}
        if name == "resource_history":
            return {
                "captured_at": "2026-09-24T13:46:10Z",
                "latest_alert_at": "2026-09-24T13:45:48Z",
                "observations": [{
                    "metric": "pod_memory_working_set_bytes",
                    "labels": {"namespace": "production", "pod": "worker-1"},
                    "samples": 8,
                    "freshness": {"status": "sampled", "latest_sample_at": "2026-09-24T13:45:42Z",
                                  "age_seconds": 28},
                    "nearest_alert": {"timestamp": "2026-09-24T13:45:42Z", "value": 126877696.0},
                    "sampled_peak": {"timestamp": "2026-09-24T13:45:42Z", "value": 126877696.0},
                    "max": 126877696.0,
                }],
                "limitation": "Sampled history only; missing values are unknown.",
            }
        raise AssertionError(f"Unexpected tool: {name}")


def assessment(next_action, *, historical_status="insufficient_evidence", historical_summary=""):
    return {
        "summary": "The worker's sampled memory and retained-page buffer increased during the alert window.",
        "likely_mechanism": "Retained export pages may have contributed to worker memory pressure.",
        "next_action": next_action,
        "expected_finding": "Continued retention after delivery resumes would support buffering pressure; drainage would weaken it.",
        "uncertainty": "The completed sample does not show whether retained pages later drained.",
        "basis": "The sampled working set and buffer values overlap the alert window; neither alone proves cause.",
        "evidence_ids": ["E-buffer", "Q002", "Q004"],
        "hypotheses": [{
            "explanation": "Retained pages contributed to memory pressure.",
            "status": "supported",
            "reason": "Incident-window logs show pages retained near their configured buffer bound.",
            "evidence_ids": ["E-buffer", "Q002"],
        }],
        "connections": [],
        "historical_comparison": {
            "episode_id": "episode-prior",
            "status": historical_status,
            "summary": historical_summary,
            "evidence_ids": ["Q003"],
        },
    }


class NonRedundantNextActionTests(unittest.TestCase):
    def test_requery_detector_only_rejects_a_completed_matching_read(self):
        completed = [{
            "tool": "resource_history", "status": "completed",
            "question": "What sampled worker memory usage and configured container limit were present for this alert interval?",
        }]
        repeated = "Read container memory limit and current memory usage from metrics to confirm headroom."
        next_step = "Ask the workload owner to verify whether retained pages drain after delivery resumes."
        new_sample = "Read the next memory sample after delivery resumes to verify whether usage is falling."

        self.assertTrue(repeats_completed_check(repeated, completed))
        self.assertFalse(repeats_completed_check(next_step, completed))
        self.assertFalse(repeats_completed_check(new_sample, completed))
        self.assertFalse(repeats_completed_check(repeated, [{**completed[0], "status": "unavailable"}]))

    def test_review_uses_sampled_values_and_replaces_redundant_and_unfounded_actions(self):
        context = {
            "episode_id": "episode-current",
            "live_capture": True,
            "primary_incident_id": "incident-current",
            "scope": {"namespace": "production", "pod": "worker-1",
                      "alert_started_at": "2026-09-24T13:45:48Z"},
            "alerts": [{"incident_id": "incident-current", "alertname": "WorkerBufferPressure",
                        "startsAt": "2026-09-24T13:45:48Z"}],
            "historical_candidates": [{"episode_id": "episode-prior"}],
            "evidence": [{
                "id": "E-buffer", "domain": "log_template", "title": "Buffer reached configured bound",
                "summary": "Export pages remained buffered during the alert window.",
                "time_range": {"start": "2026-09-24T13:45:40Z", "end": "2026-09-24T13:45:40Z"},
                "diagnostic_fields": {"buffered_bytes": "100504940", "maximum_buffered_bytes": "100663296"},
            }],
        }
        repeated_action = "Read container memory limit and current memory usage from workload state or metrics to confirm headroom."
        draft = assessment(
            repeated_action,
            historical_status="similar_mechanism",
            historical_summary="The prior episode had the same alert, so the mechanism was similar.",
        )
        corrected = assessment(
            "Ask the workload owner to verify that export pages are acknowledged and the retained buffer drains after delivery resumes; persistent retention would support this mechanism, while normal drainage would weaken it.",
            historical_status="insufficient_evidence",
            historical_summary="The prior record contains a CPU-limit episode, not retained memory or buffer observations, so a shared mechanism is not established.",
        )
        client = FakeClient([
            {"action": "check", "tool": "resource_history", "arguments": {"pod": "worker-1"},
             "question": "What sampled worker memory usage and configured container limit were present for this alert interval?",
             "distinguishes": "Observed incident-window working set and declared limit versus an unsupported buffer-only inference."},
            {"action": "finish", "assessment": draft},
            {"action": "finish", "assessment": corrected},
        ])

        state = run_investigation(
            context, FakeTools(), "fake-model", 1000, lambda _state: None,
            max_checks=1, max_prompt_tokens=6500, max_total_tokens=20000, client=client,
        )

        self.assertEqual(state["status"], "ready")
        self.assertEqual(state["assessment"]["next_action"], corrected["next_action"])
        self.assertEqual(state["assessment"]["historical_comparison"]["status"], "insufficient_evidence")
        self.assertIn("Next action repeats a completed check", state["calls"][1]["validation_error"])
        review = client.requests[-1]
        self.assertIn("Use completed-check values, units, and times", EVIDENCE_REVIEW_SYSTEM)
        self.assertIn("never request them again", review.messages[0]["content"])
        self.assertIn("distinct safe operator step tied conditionally", REVIEW_INSTRUCTION)
        self.assertIn("Alert names, symptoms, or recurrence alone do not establish historical similarity", review.messages[0]["content"])
        self.assertIn("Later configuration cannot negate incident-time evidence", review.messages[0]["content"])

        payload = json.loads(review.messages[1]["content"])
        checks = {item["id"]: item for item in payload["episode"]["prior_checks"]}
        self.assertIn("Q004", checks)
        self.assertEqual(checks["Q004"]["observation"]["observations"][0]["metric"],
                         "pod_memory_working_set_bytes")
        self.assertEqual(checks["Q004"]["observation"]["observations"][0]["sampled_peak"],
                         {"timestamp": "2026-09-24T13:45:42Z", "value": 126877696.0})
        self.assertEqual(checks["Q001"]["observation"]["workloads"][0]["limits"]["memory"], "160Mi")
        serialized = json.dumps(payload["episode"])
        self.assertIn("100504940", serialized)
        self.assertIn("100663296", serialized)
        self.assertIn("2026-09-24T13:45:42Z", serialized)


if __name__ == "__main__":
    unittest.main()
