import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from zipfile import ZipFile

from fcapsule.control_plane import ControlPlane
from fcapsule.ui.app import create_app_server


REFERENCE_CASE = Path(__file__).resolve().parent / "fixtures" / "checkout_dependency_failure"


def wait_for_idle(control_plane: ControlPlane, timeout: float = 15) -> None:
    deadline = time.time() + timeout
    while control_plane.snapshot()["running"] and time.time() < deadline:
        time.sleep(0.05)
    if control_plane.snapshot()["running"]:
        raise AssertionError("Control-plane job did not finish")


class ControlPlaneTests(unittest.TestCase):
    def test_external_case_and_capsule_are_persisted(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            control_plane = ControlPlane(Path(directory) / "state")
            incident = control_plane.ingest_case(REFERENCE_CASE, "checkout-platform", "Checkout Platform")
            state = control_plane.snapshot()
            self.assertIsNone(state["error"])
            self.assertEqual(state["overview"]["totals"]["incidents"], 1)
            self.assertEqual(incident["incident_id"], "case_001")
            self.assertEqual(state["phases"]["capsule"]["status"], "waiting")
            self.assertTrue(control_plane.start_capsule(incident["incident_id"]))
            wait_for_idle(control_plane)
            state = control_plane.snapshot()
            self.assertIsNone(state["error"])
            self.assertEqual(state["overview"]["totals"]["capsules"], 1)
            self.assertEqual(state["phases"]["capsule"]["status"], "done")
            self.assertEqual(state["live"]["evaluation"]["important_signal_preservation"], 1.0)
            report_payload = control_plane.incident_report_payload(incident["incident_id"])
            self.assertIsNotNone(report_payload)
            report = report_payload["report"]
            self.assertEqual(report["incident"]["service"], "checkout-service")
            self.assertTrue(report["impact"])
            self.assertTrue(report["actions"])
            self.assertIn(report["actions"][0]["priority"], {"urgent", "next"})
            self.assertEqual(report["report_version"], "1.2")
            self.assertTrue(report["fault_alerts"])
            self.assertTrue(report["log_patterns"])
            self.assertTrue(report["log_patterns"][0]["examples"])
            pm_coverage = next(item for item in report["coverage"] if item["domain"] == "Performance management (PM)")
            self.assertTrue(pm_coverage["available"])
            capsule = state["overview"]["capsules"][0]
            self.assertTrue((Path(capsule["output_dir"]) / "incident_report.json").is_file())
            with ZipFile(capsule["archive_path"]) as archive:
                self.assertIn("incident_report.json", archive.namelist())
                self.assertNotIn("llm_comparison.json", archive.namelist())
            restored = ControlPlane(Path(directory) / "state").snapshot()
            self.assertEqual(restored["current_incident_id"], incident["incident_id"])
            self.assertEqual(restored["phases"]["capsule"]["status"], "done")

    def test_http_app_exposes_operations_settings_and_state_api(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            server = create_app_server("127.0.0.1", 0, Path(directory) / "state")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(f"{base}/console", timeout=3) as response:
                    html = response.read().decode("utf-8")
                self.assertIn("Operations", html)
                self.assertIn("AI settings", html)
                self.assertNotIn("Incident Lab", html)
                with urlopen(f"{base}/settings", timeout=3) as response:
                    self.assertIn("AI settings", response.read().decode("utf-8"))
                with urlopen(f"{base}/api/state", timeout=3) as response:
                    state = json.loads(response.read())
                self.assertFalse(state["running"])
                self.assertIn("overview", state)
                self.assertFalse(state["ai"]["api_key_configured"])
                request = Request(
                    f"{base}/api/settings/ai",
                    data=json.dumps({"model": "deepseek-v4-flash", "max_tokens": 1800}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=3) as response:
                    settings = json.loads(response.read())
                self.assertEqual(settings["model"], "deepseek-v4-flash")
                self.assertEqual(settings["max_tokens"], 1800)
                self.assertNotIn("api_key", settings)
                self.assertTrue((Path(directory) / "state" / "ai-settings.json").is_file())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
