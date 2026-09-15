import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen
from zipfile import ZipFile

from fcapsule.control_plane import ControlPlane
from fcapsule.ui.app import create_app_server


def wait_for_idle(control_plane: ControlPlane, timeout: float = 15) -> None:
    deadline = time.time() + timeout
    while control_plane.snapshot()["running"] and time.time() < deadline:
        time.sleep(0.05)
    if control_plane.snapshot()["running"]:
        raise AssertionError("Control-plane job did not finish")


class ControlPlaneTests(unittest.TestCase):
    def test_simulation_and_capsule_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            control_plane = ControlPlane(Path(directory) / "state")
            started = control_plane.start_simulation(
                {
                    "app_id": "checkout-platform",
                    "app_name": "Checkout Platform",
                    "baseline_requests": 30,
                    "incident_requests": 60,
                    "concurrency": 16,
                }
            )
            self.assertTrue(started)
            wait_for_idle(control_plane)
            state = control_plane.snapshot()
            self.assertIsNone(state["error"])
            self.assertEqual(state["overview"]["totals"]["incidents"], 1)
            self.assertEqual(state["phases"]["alerts"]["status"], "done")
            self.assertTrue(control_plane.start_capsule(state["current_incident_id"]))
            wait_for_idle(control_plane)
            state = control_plane.snapshot()
            self.assertIsNone(state["error"])
            self.assertEqual(state["overview"]["totals"]["capsules"], 1)
            self.assertEqual(state["phases"]["capsule"]["status"], "done")
            self.assertEqual(state["live"]["evaluation"]["important_signal_preservation"], 1.0)
            self.assertEqual(state["phases"]["models"]["status"], "skipped")
            report_payload = control_plane.incident_report_payload(state["current_incident_id"])
            self.assertIsNotNone(report_payload)
            report = report_payload["report"]
            self.assertEqual(report["incident"]["service"], "checkout-platform")
            self.assertTrue(report["impact"])
            self.assertTrue(report["actions"])
            self.assertEqual(report["actions"][0]["priority"], "urgent")
            self.assertEqual(report["report_version"], "1.2")
            self.assertTrue(report["fault_alerts"])
            self.assertTrue(report["pm_signals"])
            self.assertTrue(report["log_patterns"])
            self.assertIn("meaning", report["impact"][0])
            self.assertIn("values", report["pm_signals"][0])
            self.assertGreaterEqual(len(report["pm_signals"][0]["values"]), 2)
            self.assertTrue(report["log_patterns"][0]["examples"])
            self.assertTrue(report["retention"]["trace_available"])
            self.assertFalse(report["retention"]["raw_traces_retained"])
            trace_coverage = next(item for item in report["coverage"] if item["domain"] == "On-demand traces")
            self.assertEqual(trace_coverage["detail"], "available on demand")
            capsule = state["overview"]["capsules"][0]
            self.assertTrue((Path(capsule["output_dir"]) / "incident_report.json").is_file())
            with ZipFile(capsule["archive_path"]) as archive:
                self.assertIn("incident_report.json", archive.namelist())
                self.assertNotIn("llm_comparison.json", archive.namelist())
            restored = ControlPlane(Path(directory) / "state").snapshot()
            self.assertEqual(restored["current_incident_id"], state["current_incident_id"])
            self.assertGreater(restored["live"]["log_count"], 100)
            self.assertEqual(len(restored["live"]["alerts"]), 3)
            self.assertEqual(restored["phases"]["alerts"]["status"], "done")
            self.assertEqual(restored["phases"]["capsule"]["status"], "done")

    def test_http_app_exposes_both_views_and_state_api(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            server = create_app_server("127.0.0.1", 0, Path(directory) / "state")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(f"{base}/console", timeout=3) as response:
                    html = response.read().decode("utf-8")
                self.assertIn("Operations", html)
                self.assertIn("Incident Lab", html)
                with urlopen(f"{base}/api/state", timeout=3) as response:
                    state = json.loads(response.read())
                self.assertFalse(state["running"])
                self.assertIn("overview", state)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
