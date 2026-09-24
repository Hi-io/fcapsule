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

    def test_history_candidates_rank_exact_target_before_live_same_workload_other_pod(self):
        def capture(incident_id, started_at, pod, alert="CheckoutFailureRateHigh", app_id="orders",
                    source_kind="live", resource_kind="pod"):
            return self.store.record_incident({
                "incident_id": incident_id, "app_id": app_id, "scenario": "Checkout requests are failing",
                "summary": "Checkout requests are failing", "status": "resolved", "severity": "warning",
                "started_at": started_at, "ended_at": started_at, "case_dir": self.directory.name,
                "resource_kind": resource_kind, "resource_name": pod, "alert_identity": alert,
                "source_kind": source_kind,
            })

        capture("older-cross-pod", "2026-09-20T01:00:00Z", "orders-api-old")
        capture("newer-exact-pod", "2026-09-20T02:00:00Z", "orders-api-current")
        capture("current-pod", "2026-09-20T03:00:00Z", "orders-api-current")
        current_episode = self.store.episode_for_incident("current-pod")

        candidates = self.store.recurrence_candidates_for_incident(current_episode["episode_id"], "current-pod")

        self.assertEqual([item["episode_id"] for item in candidates], [
            self.store.episode_for_incident("newer-exact-pod")["episode_id"],
            self.store.episode_for_incident("older-cross-pod")["episode_id"],
        ])
        self.assertEqual([item["match_type"] for item in candidates], [
            "same_target", "same_workload_different_pod",
        ])

    def test_cross_pod_candidates_require_live_pod_scope_and_exact_alert(self):
        def capture(incident_id, started_at, pod, *, app_id, alert="QueueHigh", source_kind="live",
                    resource_kind="pod"):
            return self.store.record_incident({
                "incident_id": incident_id, "app_id": app_id, "scenario": "Query failures",
                "summary": "Query failures", "status": "resolved", "severity": "warning",
                "started_at": started_at, "ended_at": started_at, "case_dir": self.directory.name,
                "resource_kind": resource_kind, "resource_name": pod, "alert_identity": alert,
                "source_kind": source_kind,
            })

        cases = [
            ("different-app", {"app_id": "outside"}, {"app_id": "orders"}),
            ("different-alert", {"app_id": "orders", "alert": "QueueLow"}, {"app_id": "orders"}),
            ("external-source", {"app_id": "orders", "source_kind": "external"}, {"app_id": "orders", "source_kind": "external"}),
            ("not-pod", {"app_id": "orders", "resource_kind": "workload"}, {"app_id": "orders", "resource_kind": "workload"}),
            ("missing-stable-scope", {"app_id": "missing-scope"}, {"app_id": "missing-scope"}),
        ]
        for offset, (label, old_options, current_options) in enumerate(cases):
            with self.subTest(case=label):
                current_app_id = f"app-{label}"
                self.store.upsert_application(current_app_id, "Processor", "commerce", "cluster-a")
                old_options = {**old_options}
                current_options = {**current_options}
                if old_options["app_id"] == "orders":
                    old_options["app_id"] = current_app_id
                if current_options["app_id"] == "orders":
                    current_options["app_id"] = current_app_id
                if old_options["app_id"] == "missing-scope":
                    old_options["app_id"] = current_app_id
                if current_options["app_id"] == "missing-scope":
                    current_options["app_id"] = current_app_id
                if old_options["app_id"] != current_app_id:
                    self.store.upsert_application(old_options["app_id"], "Other Processor", "commerce", "cluster-a")
                if label == "missing-stable-scope":
                    self.store.upsert_application(current_app_id, "", "", "")
                old_id = f"{label}-old"
                current_id = f"{label}-current"
                old = capture(old_id, f"2026-09-20T{offset + 1:02d}:00:00Z", f"processor-old-{label}", **old_options)
                current = capture(current_id, f"2026-09-20T{offset + 1:02d}:30:00Z", f"processor-new-{label}", **current_options)
                current_episode = self.store.episode_for_incident(current_id)
                candidates = self.store.recurrence_candidates_for_incident(current_episode["episode_id"], current_id)
                self.assertEqual(candidates, [], (old, current))

    def test_node_identity_prefers_the_node_over_the_exporter_application(self):
        identity = _resource_identity(
            {"alertname": "NodeMemoryHighUtilization", "labels": {"instance": "go15:9100"}},
            "prometheus-node-exporter",
        )
        self.assertEqual(identity["kind"], "node")
        self.assertEqual(identity["name"], "go15")

    def test_node_identity_uses_the_captured_collector_pod_node_before_an_ip_fallback(self):
        identity = _resource_identity(
            {"alertname": "NodeMemoryHighUtilization", "labels": {"instance": "10.0.0.248:9100", "pod": "node-exporter-worker"}},
            "prometheus-node-exporter",
            [{"kind": "PodSpec", "name": "node-exporter-worker", "node": "pc-worker"}],
        )
        self.assertEqual(identity["kind"], "node")
        self.assertEqual(identity["name"], "pc-worker")

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
        result = validate_assessment(assessment(), {"Q001"}, {"incident-current"}, {"episode-prior"})
        self.assertNotIn("historical_comparison", result)
        self.assertEqual(result["historical_comparison_review"]["reason"], "not_supplied")

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
            [{"episode_id": "episode-prior", "reference": "EP-12345678", "captured_evidence": [],
              "prior_hypothesis": {"summary": "Earlier explanation", "provenance": "Earlier model output; not independent evidence and not citable."}}],
        )

        result = tools.execute("historical_episode", {"episode_id": "episode-prior"})

        self.assertEqual(result["source"], "Retained FCAPSule historical episode")
        self.assertEqual(result["episode"]["reference"], "EP-12345678")
        self.assertIn("prior_hypothesis", result["episode"])
        self.assertNotIn("assessment", result["episode"])
        self.assertIn("not citable", result["limitation"])
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
