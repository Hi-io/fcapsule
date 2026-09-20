import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from datetime import datetime, timedelta
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
    def test_retained_report_does_not_reopen_expired_source(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            source = Path(directory) / "source"
            shutil.copytree(REFERENCE_CASE, source)
            plane = ControlPlane(Path(directory) / "state")
            incident = plane.ingest_case(source, "checkout", "Checkout")
            plane._build_capsule(incident["incident_id"])
            before = plane.incident_report_payload(incident["incident_id"])
            shutil.rmtree(source)
            with patch("fcapsule.control_plane.load_case", side_effect=AssertionError("Source must not be reopened")):
                after = plane.incident_report_payload(incident["incident_id"])
            self.assertEqual(before["report"], after["report"])
            storage = after["storage"]
            self.assertEqual(storage["archive_bytes"], Path(after["record"]["archive_path"]).stat().st_size)
            self.assertEqual(storage["report_bytes"], (Path(storage["directory"]) / "incident_report.json").stat().st_size)
            expected = datetime.fromisoformat(incident["created_at"].replace("Z", "+00:00")) + timedelta(days=30)
            self.assertEqual(datetime.fromisoformat(storage["expires_at"].replace("Z", "+00:00")), expected)
            # Older/missing reports can still be derived from a retained capsule.
            (Path(storage["directory"]) / "incident_report.json").unlink()
            rebuilt = plane.incident_report_payload(incident["incident_id"])
            self.assertEqual(rebuilt["report"]["report_version"], "1.3")
            self.assertTrue(rebuilt["report"]["log_patterns"])

    def test_source_sync_builds_every_new_incident_report(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            control_plane = ControlPlane(Path(directory) / "state")
            sync_result = {
                "captured": [
                    {"case_dir": "/case/one", "app_id": "app-1", "app_name": "App One"},
                    {"case_dir": "/case/two", "app_id": "app-2", "app_name": "App Two"},
                ],
                "pods_visible": 2,
                "applications_visible": 2,
                "active_alerts": 2,
                "last_sync_at": "2026-09-20T00:00:00Z",
                "targets": {},
                "ok": True,
                "configuration": control_plane.source_configuration(),
            }
            with (
                patch.object(control_plane.live_sources, "synchronize", return_value=sync_result),
                patch.object(
                    control_plane,
                    "ingest_case",
                    side_effect=[{"incident_id": "incident-1"}, {"incident_id": "incident-2"}],
                ),
                patch.object(control_plane, "_build_capsule") as build_capsule,
            ):
                control_plane.running = True
                control_plane.active_job = "sources"
                control_plane._run_source_sync()

            self.assertEqual(
                [call.args[0] for call in build_capsule.call_args_list],
                ["incident-1", "incident-2"],
            )

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
            self.assertEqual(report["report_version"], "1.3")
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
                self.assertIn("Settings", html)
                self.assertNotIn("Incident Lab", html)
                with urlopen(f"{base}/settings", timeout=3) as response:
                    self.assertIn("Settings", response.read().decode("utf-8"))
                with urlopen(f"{base}/targets", timeout=3) as response:
                    targets_html = response.read().decode("utf-8")
                self.assertIn("Targets", targets_html)
                with urlopen(f"{base}/api/state", timeout=3) as response:
                    state = json.loads(response.read())
                self.assertFalse(state["running"])
                self.assertIn("overview", state)
                self.assertFalse(state["ai"]["api_key_configured"])
                self.assertIn("sources", state)
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
                source_request = Request(
                    f"{base}/api/settings/sources",
                    data=json.dumps(
                        {
                            "prometheus_url": "http://prometheus:9090",
                            "opensearch_url": "http://opensearch:9200",
                            "opensearch_index": "logs-*",
                            "cluster_name": "test-cluster",
                            "namespaces": "default",
                            "enabled": False,
                        }
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(source_request, timeout=3) as response:
                    sources = json.loads(response.read())
                self.assertEqual(sources["cluster_name"], "test-cluster")
                self.assertEqual(sources["namespaces"], ["default"])
                retention_request = Request(
                    f"{base}/api/settings/general",
                    data=json.dumps({"incident_retention_days": 45}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(retention_request, timeout=3) as response:
                    general = json.loads(response.read())
                self.assertEqual(general["incident_retention_days"], 45)
                incident = server.control_plane.ingest_case(REFERENCE_CASE, "checkout")
                server.control_plane._build_capsule(incident["incident_id"])
                episode = server.control_plane.store.episode_for_incident(incident["incident_id"])
                endpoint = f"{base}/api/episodes/{episode['episode_id']}/investigation"
                with urlopen(endpoint, timeout=3) as response:
                    self.assertEqual(json.loads(response.read())["status"], "not_configured")
                with urlopen(Request(endpoint, data=b"", method="POST"), timeout=3) as response:
                    result = json.loads(response.read())
                    self.assertEqual(result["episode_id"], episode["episode_id"])
                    self.assertEqual(result["status"], "not_configured")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_incident_archive_restore_and_delete_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            control_plane = ControlPlane(Path(directory) / "state")
            incident = control_plane.ingest_case(REFERENCE_CASE, "checkout-platform", "Checkout Platform")
            incident_id = incident["incident_id"]

            control_plane.set_incident_archived(incident_id, True)
            state = control_plane.snapshot()
            self.assertEqual(state["overview"]["incidents"], [])
            self.assertEqual(state["overview"]["archived_incidents"][0]["incident_id"], incident_id)

            control_plane.set_incident_archived(incident_id, False)
            self.assertEqual(control_plane.snapshot()["overview"]["incidents"][0]["incident_id"], incident_id)

            control_plane.delete_incident(incident_id)
            self.assertIsNone(control_plane.store.get_incident(incident_id))

    def test_episode_lifecycle_applies_to_every_related_signal(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            control_plane = ControlPlane(Path(directory) / "state")
            first = control_plane.ingest_case(REFERENCE_CASE, "checkout-platform", "Checkout Platform")
            second_payload = dict(first)
            second_payload.update(
                {
                    "incident_id": "case_002",
                    "started_at": first["started_at"],
                    "case_dir": str(REFERENCE_CASE),
                    "summary": "Related checkout signal",
                }
            )
            control_plane.store.record_incident(second_payload)
            episode = control_plane.store.list_episodes()[0]
            self.assertEqual(episode["signal_count"], 2)

            control_plane.set_episode_archived(episode["episode_id"], True)
            self.assertEqual(control_plane.store.list_episodes(), [])
            self.assertEqual(control_plane.store.list_episodes(archived=True)[0]["signal_count"], 2)

            control_plane.set_episode_archived(episode["episode_id"], False)
            control_plane.delete_episode(episode["episode_id"])
            self.assertIsNone(control_plane.store.get_incident(first["incident_id"]))
            self.assertIsNone(control_plane.store.get_incident("case_002"))

    def test_retention_removes_incidents_by_capture_age(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            control_plane = ControlPlane(Path(directory) / "state")
            incident = control_plane.ingest_case(REFERENCE_CASE, "checkout-platform", "Checkout Platform")
            with control_plane.store._connect() as connection:
                connection.execute(
                    "UPDATE incidents SET created_at = ? WHERE incident_id = ?",
                    ("2020-01-01T00:00:00Z", incident["incident_id"]),
                )

            self.assertEqual(control_plane.purge_expired_incidents(force=True), 1)
            self.assertIsNone(control_plane.store.get_incident(incident["incident_id"]))


if __name__ == "__main__":
    unittest.main()
