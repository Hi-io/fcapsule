import tempfile
import unittest
from pathlib import Path

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
            overview = store.overview()
            self.assertEqual(overview["totals"]["applications"], 1)
            self.assertEqual(overview["totals"]["degraded_applications"], 1)
            self.assertEqual(overview["totals"]["raw_bytes_observed"], 100000)
            self.assertEqual(len(overview["models"]), 2)

    def test_model_profile_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FCAPSuleStore(Path(directory) / "state.db")
            profile = store.update_model_profile("deepseek-v4-flash", False, 1800)
            self.assertFalse(profile["enabled"])
            self.assertEqual(profile["max_tokens"], 1800)
            with self.assertRaises(ValueError):
                store.update_model_profile("deepseek-v4-flash", True, 100)


if __name__ == "__main__":
    unittest.main()
