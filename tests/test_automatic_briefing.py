import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fcapsule.control_plane import ControlPlane
from tests.common import REFERENCE_CASE


class AutomaticBriefingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""})
        self.env.start()
        self.control = ControlPlane(Path(self.directory.name))
        self.incident = self.control.ingest_case(REFERENCE_CASE, "checkout")
        self.id = self.incident["incident_id"]

    def tearDown(self):
        self.control.briefing_executor.shutdown(wait=True)
        self.env.stop()
        self.directory.cleanup()

    def test_report_build_starts_analysis_without_blocking_and_deduplicates(self):
        entered, release = threading.Event(), threading.Event()

        def provider(*args, **kwargs):
            entered.set()
            release.wait(5)
            return {"status": "ready", "briefing_version": "2", "briefing": {"operator_brief": "Retained"}}

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), patch(
            "fcapsule.control_plane.generate_incident_briefing", side_effect=provider
        ) as generate:
            try:
                capsule = self.control._build_capsule(self.id)
                self.assertTrue(entered.wait(2))
                self.assertTrue(Path(capsule["archive_path"]).is_file())
                payload = self.control.incident_report_payload(self.id)
                self.assertIsNotNone(payload["report"])
                self.assertEqual(payload["ai_briefing"]["status"], "running")
                self.assertEqual(self.control.start_ai_briefing(self.id, retry=True)["status"], "running")
                self.assertEqual(generate.call_count, 1)
            finally:
                release.set()
                self.control.briefing_executor.shutdown(wait=True)
        self.assertEqual(self.control.incident_report_payload(self.id)["ai_briefing"]["status"], "ready")

    def test_missing_key_is_explicit_and_report_is_available(self):
        self.control._build_capsule(self.id)
        result = self.control.incident_report_payload(self.id)
        self.assertIsNotNone(result["report"])
        self.assertEqual(result["ai_briefing"]["status"], "not_configured")

    def test_failure_is_persisted_and_not_retried_by_resume(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), patch(
            "fcapsule.control_plane.generate_incident_briefing", side_effect=RuntimeError("provider down")
        ) as generate:
            self.control._build_capsule(self.id)
            self.control.briefing_executor.shutdown(wait=True)
            self.assertEqual(self.control.incident_report_payload(self.id)["ai_briefing"]["status"], "unavailable")
            self.control.resume_ai_briefings()
            self.assertEqual(generate.call_count, 1)

    def test_deleted_incident_is_not_recreated_by_late_provider_response(self):
        entered, release = threading.Event(), threading.Event()

        def provider(*args, **kwargs):
            entered.set()
            release.wait(5)
            return {"status": "ready", "briefing_version": "2", "briefing": {}}

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), patch(
            "fcapsule.control_plane.generate_incident_briefing", side_effect=provider
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
        path = Path(capsule["output_dir"]) / "ai_briefing.json"
        self.control._write_briefing_state(path, {"status": "running"})
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), patch(
            "fcapsule.control_plane.generate_incident_briefing",
            return_value={"status": "ready", "briefing_version": "2", "briefing": {}},
        ) as generate:
            self.control.resume_ai_briefings()
            self.control.briefing_executor.shutdown(wait=True)
            self.assertEqual(generate.call_count, 1)
            self.assertEqual(json.loads(path.read_text())["status"], "ready")
