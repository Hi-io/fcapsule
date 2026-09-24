import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fcapsule.store import FCAPSuleStore


class StoreTests(unittest.TestCase):
    def test_control_plane_records_application_incident_capsule_and_models(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FCAPSuleStore(Path(directory) / "state.db")
            app = store.upsert_application("checkout", "Checkout", "shop", "local")
            self.assertEqual(app["status"], "healthy")
            incident = store.record_incident(
                {
                    "incident_id": "incident-001",
                    "app_id": "checkout",
                    "scenario": "retry-storm",
                    "started_at": "2026-08-18T00:00:00Z",
                    "case_dir": "/tmp/case",
                    "alert_count": 3,
                    "log_count": 2000,
                    "metric_series_count": 18,
                    "raw_bytes": 100000,
                    "trace_access": {"available": True, "raw_spans_retained": False},
                    "summary": "A retry storm saturated the inventory pool.",
                }
            )
            self.assertEqual(incident["trace_access"]["raw_spans_retained"], False)
            capsule = store.record_capsule(
                {
                    "capsule_id": "capsule-001",
                    "incident_id": "incident-001",
                    "app_id": "checkout",
                    "output_dir": "/tmp/output",
                    "archive_path": "/tmp/output/archive.zip",
                    "size_bytes": 5000,
                    "selected_evidence": 12,
                    "compression": 0.98,
                    "signal_preservation": 1.0,
                    "grounding": 1.0,
                    "runtime_seconds": 0.5,
                }
            )
            self.assertEqual(capsule["incident_id"], "incident-001")
            store.update_capsule_model_winner("capsule-001", "deepseek-v4-pro")
            self.assertEqual(store.get_capsule("capsule-001")["model_winner"], "deepseek-v4-pro")
            overview = store.overview()
            self.assertEqual(overview["totals"]["applications"], 1)
            self.assertEqual(overview["totals"]["degraded_applications"], 1)
            self.assertEqual(overview["totals"]["raw_bytes_observed"], 100000)
            self.assertEqual(len(overview["models"]), 2)

    def test_related_signals_are_grouped_into_operator_episodes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FCAPSuleStore(Path(directory) / "state.db")
            store.upsert_application("checkout", "Checkout", "shop", "local")
            start = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

            def record(number: int, minutes: int, severity: str, summary: str, pod: str) -> None:
                store.record_incident(
                    {
                        "incident_id": f"signal-{number}",
                        "app_id": "checkout",
                        "scenario": summary,
                        "status": "firing",
                        "severity": severity,
                        "started_at": (start + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z"),
                        "case_dir": f"/tmp/case-{number}",
                        "summary": summary,
                        "resource_kind": "pod",
                        "resource_name": pod,
                        "alert_identity": "PodNotReady",
                    }
                )

            record(1, 0, "warning", "Pod readiness degraded", "orders-0")
            record(2, 6, "critical", "Pod readiness degraded", "orders-1")
            record(3, 30, "warning", "A later degradation", "orders-2")

            episodes = store.list_episodes()
            self.assertEqual(len(episodes), 2)
            grouped = next(item for item in episodes if item["signal_count"] == 2)
            self.assertEqual(grouped["severity"], "critical")
            self.assertEqual(grouped["primary_incident_id"], "signal-2")
            self.assertEqual([item["incident_id"] for item in grouped["signals"]], ["signal-1", "signal-2"])
            self.assertEqual(store.overview()["totals"]["signals"], 3)
            self.assertEqual(store.overview()["totals"]["incidents"], 2)

            store.set_episode_archived(grouped["episode_id"], True)
            self.assertEqual(len(store.list_episodes()), 1)
            self.assertEqual(store.list_episodes(archived=True)[0]["signal_count"], 2)

    def test_unrelated_alerts_on_different_resources_are_not_time_grouped(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FCAPSuleStore(Path(directory) / "state.db")
            store.upsert_application("checkout", "Checkout", "shop", "local")
            for incident_id, resource, alert in (
                ("database", "mysql-0", "DatabaseConnectionsHigh"),
                ("worker", "worker-0", "WorkerMemoryHigh"),
            ):
                store.record_incident({
                    "incident_id": incident_id, "app_id": "checkout", "status": "firing",
                    "started_at": "2026-09-20T10:00:00Z", "case_dir": f"/tmp/{incident_id}",
                    "summary": alert, "resource_kind": "pod", "resource_name": resource,
                    "alert_identity": alert,
                })
            self.assertEqual(len(store.list_episodes()), 2)

    def test_different_alerts_on_same_resource_correlate_only_within_two_minutes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FCAPSuleStore(Path(directory) / "state.db")
            store.upsert_application("checkout", "Checkout", "shop", "local")
            def record(incident_id: str, minute: int, alert: str) -> None:
                store.record_incident({
                    "incident_id": incident_id, "app_id": "checkout", "status": "firing",
                    "started_at": f"2026-09-20T10:{minute:02d}:00Z", "case_dir": f"/tmp/{incident_id}",
                    "summary": alert, "resource_kind": "pod", "resource_name": "orders-0",
                    "alert_identity": alert,
                })
            record("latency", 0, "CheckoutLatencyHigh")
            record("errors", 1, "CheckoutErrorsHigh")
            record("late", 5, "CheckoutQueueHigh")
            episodes = store.list_episodes()
            self.assertEqual(len(episodes), 2)
            correlated = next(item for item in episodes if item["signal_count"] == 2)
            self.assertEqual({item["incident_id"] for item in correlated["signals"]}, {"latency", "errors"})

    def test_pending_alerts_do_not_create_operator_episodes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FCAPSuleStore(Path(directory) / "state.db")
            store.upsert_application("checkout", "Checkout", "shop", "local")
            store.record_incident(
                {
                    "incident_id": "pending-signal",
                    "app_id": "checkout",
                    "scenario": "Threshold pending",
                    "status": "pending",
                    "started_at": "2026-09-20T10:00:00Z",
                    "case_dir": "/tmp/pending",
                    "summary": "Threshold pending",
                }
            )
            self.assertEqual(store.list_episodes(), [])
            self.assertTrue(store.activate_live_incident("pending-signal"))
            self.assertEqual(store.list_episodes()[0]["status"], "active")
            self.assertEqual(store.list_episodes()[0]["signal_count"], 1)

    def test_late_observation_reactivates_an_older_episode_at_the_top_of_the_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FCAPSuleStore(Path(directory) / "state.db")
            store.upsert_application("checkout", "Checkout", "shop", "local")

            def record(incident_id: str, started_at: str, observed_at: str) -> None:
                with patch("fcapsule.store.utc_now", return_value=observed_at):
                    store.record_incident(
                        {
                            "incident_id": incident_id,
                            "app_id": "checkout",
                            "scenario": "Checkout requests are failing",
                            "status": "firing",
                            "severity": "warning",
                            "started_at": started_at,
                            "case_dir": f"/tmp/{incident_id}",
                            "summary": "Checkout requests are failing",
                        }
                    )

            record("early", "2026-09-20T10:00:00Z", "2026-09-23T10:00:00Z")
            record("newer-episode", "2026-09-20T10:30:00Z", "2026-09-23T10:01:00Z")
            record("late-arrival", "2026-09-20T10:04:00Z", "2026-09-23T10:02:00Z")

            episodes = store.list_episodes()
            self.assertEqual(episodes[0]["primary_incident_id"], "late-arrival")
            self.assertEqual(episodes[0]["last_activity_at"], "2026-09-23T10:02:00Z")
            self.assertEqual([signal["incident_id"] for signal in episodes[0]["signals"]], ["early", "late-arrival"])

    def test_resolved_critical_alert_does_not_title_a_different_active_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FCAPSuleStore(Path(directory) / "state.db")
            store.upsert_application("app", "App", "shop", "local")
            old = {"incident_id": "old", "app_id": "app", "case_dir": "/tmp/old",
                   "started_at": "2026-09-20T10:04:00Z", "ended_at": "2026-09-20T10:04:30Z",
                   "status": "resolved", "severity": "critical", "summary": "Past schema error"}
            new = {"incident_id": "new", "app_id": "app", "case_dir": "/tmp/new",
                   "started_at": "2026-09-20T10:05:00Z", "status": "firing", "severity": "warning",
                   "summary": "Current lock contention"}
            store.record_incident(old)
            store.record_incident(new)
            episode = store.list_episodes()[0]
            self.assertEqual(episode["primary_incident_id"], "new")
            self.assertEqual(episode["title"], "Current lock contention")
            self.assertEqual(episode["severity"], "warning")
            self.assertEqual(episode["signal_count"], 2)
            store.record_incident({**new, "status": "resolved", "ended_at": "2026-09-20T10:07:00Z"})
            self.assertEqual(store.list_episodes()[0]["severity"], "critical")

    def test_model_profile_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FCAPSuleStore(Path(directory) / "state.db")
            profile = store.update_model_profile("deepseek-v4-flash", False, 1800)
            self.assertFalse(profile["enabled"])
            self.assertEqual(profile["max_tokens"], 1800)
            with self.assertRaises(ValueError):
                store.update_model_profile("deepseek-v4-flash", True, 100)

    def test_custom_deepseek_profile_and_settings_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FCAPSuleStore(Path(directory) / "state.db")
            profile = store.upsert_model_profile("deepseek-v4-experimental", "deepseek", True, 2200)
            self.assertTrue(profile["enabled"])
            self.assertEqual(profile["max_tokens"], 2200)
            store.set_setting("ai_active_model", profile["model_id"])
            self.assertEqual(store.get_setting("ai_active_model"), "deepseek-v4-experimental")
            with self.assertRaises(ValueError):
                store.upsert_model_profile("other-model", "unsupported", True, 1200)


if __name__ == "__main__":
    unittest.main()
