import tempfile
import unittest
from pathlib import Path

from fcapsule.control_plane import ControlPlane, _resource_identity
from fcapsule.episode_investigation import validate_assessment
from fcapsule.investigation_tools import InvestigationTools
from fcapsule.store import FCAPSuleStore


def assessment(reference="Q001", historical=None):
    value = {
        "summary": "Checkout requests failed during the captured interval.",
        "likely_mechanism": "The retained observations support a bounded connection failure.",
        "next_action": "Compare the connection configuration before changing it.",
        "expected_finding": "The retained setting or error pattern will differ if the mechanism changed.",
        "uncertainty": "The short captured interval cannot prove a persistent cause.",
        "evidence_ids": [reference],
        "hypotheses": [{
            "explanation": "Connection handling failure",
            "status": "supported",
            "reason": "The cited retained observation records the failure.",
            "evidence_ids": [reference],
        }],
        "connections": [],
    }
    if historical:
        value["historical_comparison"] = historical
    return value


class OperationsEvolutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = FCAPSuleStore(Path(self.directory.name) / "fcapsule.db")
        self.store.upsert_application("orders", "Orders API", "commerce", "cluster-a")

    def tearDown(self):
        self.directory.cleanup()

    def record(self, incident_id, started_at, resource_name="orders-api", alert_identity="CheckoutFailureRateHigh"):
        return self.store.record_incident({
            "incident_id": incident_id,
            "app_id": "orders",
            "scenario": "Checkout requests are failing",
            "summary": "Checkout requests are failing",
            "status": "resolved",
            "severity": "critical",
            "started_at": started_at,
            "ended_at": started_at,
            "case_dir": self.directory.name,
            "resource_kind": "workload",
            "resource_name": resource_name,
            "alert_identity": alert_identity,
        })

    def test_recurrence_requires_the_same_resource_and_alert_identity(self):
        self.record("one", "2026-09-20T01:00:00Z")
        self.record("two", "2026-09-20T03:00:00Z")
        self.record("three", "2026-09-20T05:00:00Z", resource_name="inventory-api")

        episodes = self.store.list_episodes()
        repeated = next(item for item in episodes if item["primary_incident_id"] == "two")
        different = next(item for item in episodes if item["primary_incident_id"] == "three")

        self.assertEqual(repeated["recurrence"]["previous_count"], 1)
        self.assertEqual(repeated["recurrence"]["occurrence_count"], 2)
        self.assertEqual(repeated["recurrence"]["observed_interval_seconds"], 7200.0)
        self.assertEqual(different["recurrence"]["previous_count"], 0)
        self.assertEqual(len(self.store.list_patterns()), 1)

    def test_node_identity_prefers_the_node_over_the_exporter_application(self):
        identity = _resource_identity(
            {"alertname": "NodeMemoryHighUtilization", "labels": {"instance": "go15:9100"}},
            "prometheus-node-exporter",
        )
        self.assertEqual(identity["kind"], "node")
        self.assertEqual(identity["name"], "go15")

    def test_historical_comparison_must_reference_a_supplied_episode_and_evidence(self):
        comparison = {
            "episode_id": "episode-prior",
            "status": "changed_or_different",
            "summary": "The earlier retained evidence has a different configuration snapshot.",
            "evidence_ids": ["Q002"],
        }
        result = validate_assessment(
            assessment("Q001", comparison), {"Q001", "Q002"}, {"incident-current"}, {"episode-prior"}
        )
        self.assertEqual(result["historical_comparison"]["status"], "changed_or_different")
        with self.assertRaises(ValueError):
            validate_assessment(assessment(), {"Q001"}, {"incident-current"}, {"episode-prior"})

    def test_historical_tool_is_limited_to_recurrence_candidates(self):
        tools = InvestigationTools(
            [{
                "incident": {"started_at": "2026-09-20T03:00:00Z", "source_kind": "external"},
                "capsule": {
                    "case": {
                        "pod": "orders-api-7f9d",
                        "window": {"start": "2026-09-20T02:55:00Z", "end": "2026-09-20T03:05:00Z"},
                    },
                },
                "report": {},
            }],
            {"namespace": "commerce"},
            {},
            [{"episode_id": "episode-prior", "reference": "EP-12345678", "captured_evidence": []}],
        )

        result = tools.execute("historical_episode", {"episode_id": "episode-prior"})

        self.assertEqual(result["source"], "Retained FCAPSule historical episode")
        self.assertEqual(result["episode"]["reference"], "EP-12345678")
        with self.assertRaises(ValueError):
            tools.execute("historical_episode", {"episode_id": "unrelated-episode"})

    def test_triage_is_bounded_and_deduplicates_a_recurring_pattern(self):
        overview = {
            "episodes": [
                {
                    "episode_id": "active", "reference": "EP-A", "title": "Active failure",
                    "resource": {"kind": "workload", "name": "orders"}, "started_at": "2026-09-20T05:00:00Z",
                    "last_activity_at": "2026-09-20T05:00:00Z", "severity": "critical", "status": "active",
                    "recurrence": {}, "investigation": {},
                },
                {
                    "episode_id": "changed", "reference": "EP-C", "title": "Changed failure",
                    "resource": {"kind": "workload", "name": "orders"}, "started_at": "2026-09-20T04:00:00Z",
                    "last_activity_at": "2026-09-20T04:00:00Z", "severity": "warning", "status": "resolved",
                    "recurrence": {},
                    "investigation": {"historical_comparison": {"status": "changed_or_different", "summary": "The retained config differs."}},
                },
                *[
                    {
                        "episode_id": f"repeat-{number}", "reference": f"EP-R{number}", "title": "Repeated failure",
                        "resource": {"kind": "workload", "name": "orders"}, "started_at": f"2026-09-20T0{number}:00:00Z",
                        "last_activity_at": f"2026-09-20T0{number}:00:00Z", "severity": "warning", "status": "resolved",
                        "recurrence": {"pattern_id": "PAT-ORDERS", "previous_count": number}, "investigation": {},
                    }
                    for number in (2, 3, 4)
                ],
            ]
        }

        triage = ControlPlane._operations_triage(overview)

        self.assertLessEqual(len(triage), 3)
        self.assertEqual([item["kind"] for item in triage], ["active", "changed", "recurring"])
        self.assertEqual(sum(item["kind"] == "recurring" for item in triage), 1)


if __name__ == "__main__":
    unittest.main()
