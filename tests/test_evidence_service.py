import base64
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fcapsule.control_plane import ControlPlane


PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/"
    "6v4jLQAAAABJRU5ErkJggg=="
)
REFERENCE_CASE = Path(__file__).resolve().parent / "fixtures" / "checkout_dependency_failure"


class _VisionClient:
    def __init__(self, **_):
        pass

    def visual_extract(self, data, mime_type, model):
        self.last = (data, mime_type, model)
        return {
            "content": '{"visible_text":["Target down"],"observations":[{"fact":"checkout-metrics is down","confidence":"high","region":"targets table"}],"ambiguities":["time not visible"],"limitation":"Only visible rows were extracted."}',
            "usage": {"total_tokens": 21},
        }


class EvidenceServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environ = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": ""})
        self.environ.start()
        self.plane = ControlPlane(Path(self.directory.name) / "state")
        incident = self.plane.ingest_case(REFERENCE_CASE, "checkout")
        self.episode_id = self.plane.store.episode_for_incident(incident["incident_id"])["episode_id"]

    def tearDown(self):
        self.plane.evidence.shutdown()
        self.plane.briefing_executor.shutdown(wait=False, cancel_futures=True)
        self.environ.stop()
        self.directory.cleanup()

    def _wait(self, attachment_id):
        deadline = time.time() + 3
        while time.time() < deadline:
            record = self.plane.store.get_evidence_attachment(attachment_id)
            if record and record["status"] in {"ready", "failed", "blocked"}:
                return record
            time.sleep(0.02)
        self.fail("Evidence processing did not finish")

    def test_image_evidence_is_checked_extracted_and_made_available_to_investigation(self):
        with patch.object(self.plane, "media_submission_allowed", return_value=(True, "")), patch(
            "fcapsule.evidence_service.OpenRouterClient", _VisionClient
        ):
            attachment = self.plane.submit_evidence(
                self.episode_id,
                {"kind": "image", "filename": "targets.png", "content_base64": base64.b64encode(PIXEL_PNG).decode("ascii"),
                 "observed_at": "2026-09-22T10:00:00Z", "context_note": "Screenshot after the alert", "source_redacted": True},
            )
            stored = self._wait(attachment["attachment_id"])
        self.assertEqual(stored["status"], "ready")
        self.assertEqual(stored["mime_type"], "image/png")
        self.assertTrue(stored["source_redacted"])
        self.assertEqual(stored["extraction"]["observations"][0]["fact"], "checkout-metrics is down")
        evidence = self.plane.evidence.model_evidence(self.episode_id)
        self.assertEqual(evidence[0]["domain"], "image_evidence")
        self.assertTrue(evidence[0]["id"].startswith("A-attachment-"))
        public = self.plane.evidence.list(self.episode_id)[0]
        self.assertNotIn("storage_path", public)
        self.assertIn("artifact_url", public)
        manifest = self.plane.evidence.manifest(self.episode_id)[0]
        self.assertEqual(manifest["extraction"]["visible_text"], ["Target down"])
        self.assertEqual(manifest["context_note"], "Screenshot after the alert")

    def test_invalid_or_unsupported_upload_is_rejected_before_storage(self):
        with patch.object(self.plane, "media_submission_allowed", return_value=(True, "")):
            with self.assertRaisesRegex(ValueError, "base64"):
                self.plane.submit_evidence(self.episode_id, {"kind": "image", "content_base64": "not base64"})
            with self.assertRaisesRegex(ValueError, "PNG, JPEG, or WebP"):
                self.plane.submit_evidence(
                    self.episode_id,
                    {"kind": "image", "content_base64": base64.b64encode(b"not-an-image").decode("ascii")},
                )
        self.assertEqual(self.plane.store.list_evidence_attachments(self.episode_id), [])

    def test_correction_stays_separate_from_extraction(self):
        with patch.object(self.plane, "media_submission_allowed", return_value=(True, "")), patch(
            "fcapsule.evidence_service.OpenRouterClient", _VisionClient
        ):
            attachment = self.plane.submit_evidence(
                self.episode_id,
                {"kind": "image", "content_base64": base64.b64encode(PIXEL_PNG).decode("ascii")},
            )
            self._wait(attachment["attachment_id"])
        corrected = self.plane.correct_evidence(attachment["attachment_id"], {"correction": "The target was intentionally disabled."})
        self.assertEqual(corrected["correction"], "The target was intentionally disabled.")
        stored = self.plane.store.get_evidence_attachment(attachment["attachment_id"])
        self.assertEqual(stored["extraction"]["visible_text"], ["Target down"])
        model_evidence = self.plane.evidence.model_evidence(self.episode_id)[0]
        self.assertEqual(model_evidence["operator_context"]["correction"], "The target was intentionally disabled.")
        self.assertNotIn("Operator correction", model_evidence["summary"])


if __name__ == "__main__":
    unittest.main()
