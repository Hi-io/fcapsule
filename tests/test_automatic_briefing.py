import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from fcapsule.control_plane import ControlPlane
from tests.common import REFERENCE_CASE


class AutomaticInvestigationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""})
        self.env.start()
        self.control = ControlPlane(Path(self.directory.name))
        self.incident = self.control.ingest_case(REFERENCE_CASE, "checkout")
        self.id = self.incident["incident_id"]
        self.episode_id = self.control.store.episode_for_incident(self.id)["episode_id"]

    def tearDown(self):
        self.control.briefing_executor.shutdown(wait=True)
        self.env.stop()
        self.directory.cleanup()

    def test_report_build_starts_analysis_without_blocking_and_deduplicates(self):
        entered, release = threading.Event(), threading.Event()

        def provider(*args, **kwargs):
            args[4]({"status": "running", "checks": [], "assessment": None})
            entered.set()
            release.wait(5)
            args[4]({"status": "ready", "checks": [], "assessment": {"summary": "Retained"}})

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), patch(
            "fcapsule.investigation_service.run_investigation", side_effect=provider
        ) as generate:
            try:
                capsule = self.control._build_capsule(self.id)
                self.assertTrue(entered.wait(2))
                self.assertTrue(Path(capsule["archive_path"]).is_file())
                payload = self.control.incident_report_payload(self.id)
                self.assertIsNotNone(payload["report"])
                self.assertEqual(payload["investigation"]["status"], "running")
                self.assertEqual(self.control.investigator.start(self.episode_id, retry=True)["status"], "running")
                self.assertEqual(generate.call_count, 1)
            finally:
                release.set()
                self.control.briefing_executor.shutdown(wait=True)
        self.assertEqual(self.control.incident_report_payload(self.id)["investigation"]["status"], "ready")

    def test_missing_key_is_explicit_and_report_is_available(self):
        self.control._build_capsule(self.id)
        result = self.control.incident_report_payload(self.id)
        self.assertIsNotNone(result["report"])
        self.assertEqual(result["investigation"]["status"], "not_configured")

    def test_failure_is_persisted_and_not_retried_by_resume(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), patch(
            "fcapsule.investigation_service.run_investigation", side_effect=RuntimeError("provider down")
        ) as generate:
            self.control._build_capsule(self.id)
            self.control.briefing_executor.shutdown(wait=True)
            self.assertEqual(self.control.incident_report_payload(self.id)["investigation"]["status"], "incomplete")
            self.control.investigator.resume()
            self.assertEqual(generate.call_count, 1)

    def test_deleted_incident_is_not_recreated_by_late_provider_response(self):
        entered, release = threading.Event(), threading.Event()

        def provider(*args, **kwargs):
            entered.set()
            release.wait(5)
            args[4]({"status": "ready", "checks": [], "assessment": {}})

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), patch(
            "fcapsule.investigation_service.run_investigation", side_effect=provider
        ):
            try:
                capsule = self.control._build_capsule(self.id)
                self.assertTrue(entered.wait(2))
                self.control.delete_incident(self.id)
            finally:
                release.set()
                self.control.briefing_executor.shutdown(wait=True)
            self.assertFalse(Path(capsule["output_dir"]).exists())
            self.assertIsNone(self.control.store.get_incident(self.id))

    def test_startup_resumes_interrupted_retained_work(self):
        capsule = self.control._build_capsule(self.id)
        path = self.control.investigator.path(self.episode_id)
        self.control._write_briefing_state(path, {"status": "running", "calls": [{"status": "running"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "complete": True}})
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), patch(
            "fcapsule.investigation_service.run_investigation",
            side_effect=lambda *args: args[4]({"status": "ready", "checks": [], "assessment": {}}),
        ) as generate:
            self.control.investigator.resume()
            self.control.briefing_executor.shutdown(wait=True)
            self.assertEqual(generate.call_count, 1)
            self.assertEqual(json.loads(path.read_text())["status"], "ready")
            self.assertFalse(json.loads(path.read_text())["lifetime_usage"]["complete"])

    def test_joint_job_waits_for_members_and_deletion_invalidates_shared_exports(self):
        self.control.store.record_incident({**self.incident, "incident_id": "second-signal"})

        def provider(context, kit, model, limit, publish):
            self.assertEqual(len(context["alerts"]), 2)
            publish({"status": "ready", "checks": [], "assessment": {"summary": "Joint"}, "context": context})

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), patch(
            "fcapsule.investigation_service.run_investigation", side_effect=provider
        ) as generate:
            first = self.control._build_capsule(self.id)
            self.assertEqual(self.control.investigator.read(self.episode_id)["status"], "waiting")
            generate.assert_not_called()
            second = self.control._build_capsule("second-signal")
            self.control.briefing_executor.shutdown(wait=True)
            self.assertEqual(generate.call_count, 1)
            for record in (first, second):
                with ZipFile(record["archive_path"]) as archive:
                    self.assertIn("episode_investigation.json", archive.namelist())
                    self.assertIn("investigation_revisions.json", archive.namelist())
            self.control.delete_incident(self.id)
            self.assertEqual(self.control.investigator.read(self.episode_id)["status"], "not_started")
            with ZipFile(second["archive_path"]) as archive:
                self.assertNotIn("episode_investigation.json", archive.namelist())
                self.assertNotIn("investigation_revisions.json", archive.namelist())
