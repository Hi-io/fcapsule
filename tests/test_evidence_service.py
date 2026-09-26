import base64
import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZipFile

from fcapsule.control_plane import ControlPlane
from fcapsule.evidence_service import TEXT_LIMIT, _extract_json
from fcapsule.reasoning.context_budget import compact_for_model
from fcapsule.ui.app import FCAPSuleHTTPServer, create_app_server


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
        self.incident_id = incident["incident_id"]
        self.episode_id = self.plane.store.episode_for_incident(incident["incident_id"])["episode_id"]

    def tearDown(self):
        self.plane.evidence.shutdown()
        self.plane.briefing_executor.shutdown(wait=True, cancel_futures=True)
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

    def _ready_core(self):
        os.environ["DEEPSEEK_API_KEY"] = "test-core-key"
        self.plane._set_capability(
            "ai_core_capability", "test-core-key", self.plane.ai_configuration()["model"], "ready", "Test validation"
        )

    def test_text_is_ready_without_specialist_configuration_executor_or_provider_calls(self):
        self._ready_core()
        before = datetime.now(timezone.utc)
        with (
            patch.object(self.plane, "media_configuration", side_effect=AssertionError("No specialist needed")),
            patch.object(self.plane.evidence.executor, "submit") as enqueue,
            patch("fcapsule.evidence_service.OpenRouterClient") as media_client,
            patch("fcapsule.control_plane.DeepSeekChatClient") as core_client,
            patch.object(self.plane.investigator, "start") as investigate,
        ):
            attachment = self.plane.submit_evidence(self.episode_id, {
                "kind": "text", "content_text": "The rollout may have changed the retry policy.",
                "uploaded_at": "2000-01-01T00:00:00Z",
            })
            self.plane.evidence._process(attachment["attachment_id"])
            enqueue.assert_not_called()
            media_client.assert_not_called()
            core_client.assert_not_called()
            investigate.assert_not_called()
        self.assertEqual(attachment["kind"], "text")
        self.assertEqual(attachment["status"], "ready")
        self.assertEqual(attachment["filename"], "context.txt")
        self.assertEqual(attachment["mime_type"], "text/plain; charset=utf-8")
        self.assertIsNone(attachment["provider"])
        self.assertIsNone(attachment["model"])
        self.assertEqual(attachment["usage"], {})
        self.assertIsNone(attachment["observed_at"])
        self.assertEqual(attachment["uploaded_at"], attachment["created_at"])
        uploaded = datetime.fromisoformat(attachment["uploaded_at"].replace("Z", "+00:00"))
        self.assertLessEqual(before, uploaded)
        self.assertLessEqual(uploaded, datetime.now(timezone.utc))
        self.assertNotIn("storage_path", attachment)
        self.assertFalse(attachment["source_redacted"])

    def test_text_requires_current_core_validation_but_media_still_requires_specialist(self):
        payload = {"kind": "text", "content_text": "Operator observation"}
        with self.assertRaisesRegex(ValueError, "core investigator"):
            self.plane.submit_evidence(self.episode_id, payload)
        os.environ["DEEPSEEK_API_KEY"] = "test-core-key"
        with self.assertRaisesRegex(ValueError, "core investigator"):
            self.plane.submit_evidence(self.episode_id, payload)
        self.assertEqual(list(self.plane.evidence.root.rglob("*.txt")), [])
        self._ready_core()
        self.plane.submit_evidence(self.episode_id, payload)
        for kind, capability in (("image", "vision"), ("audio", "audio")):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, f"selected {capability} model"):
                self.plane.submit_evidence(self.episode_id, {"kind": kind})
        self.plane.store.set_setting("ai_active_model", "unvalidated-model")
        with self.assertRaisesRegex(ValueError, "core investigator"):
            self.plane.submit_evidence(self.episode_id, payload)
        self._ready_core()
        os.environ["DEEPSEEK_API_KEY"] = "replacement-core-key"
        with self.assertRaisesRegex(ValueError, "core investigator"):
            self.plane.submit_evidence(self.episode_id, payload)
        self.assertEqual(len(self.plane.evidence.list(self.episode_id)), 1)

    def test_media_specialist_validation_does_not_bypass_core_validation(self):
        os.environ["OPENROUTER_API_KEY"] = "test-media-key"
        config = self.plane.media_configuration()
        for kind, capability in (("image", "vision"), ("audio", "audio")):
            self.plane._set_capability(
                f"media_{capability}_capability", "test-media-key", config[capability]["model"], "ready", "Test validation"
            )
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "core investigator"):
                self.plane.submit_evidence(self.episode_id, {"kind": kind})
        self.assertEqual(self.plane.evidence.list(self.episode_id), [])

    def test_text_rejects_empty_non_string_oversize_and_invalid_unicode_before_storage(self):
        self._ready_core()
        for content in (None, "", " \n\t", 123, {}, [], "x" * (TEXT_LIMIT + 1), "\ud800"):
            with self.subTest(content_type=type(content).__name__), self.assertRaises(ValueError):
                self.plane.submit_evidence(self.episode_id, {"kind": "text", "content_text": content})
        with self.assertRaisesRegex(ValueError, "content_text"):
            self.plane.submit_evidence(self.episode_id, {"kind": "text", "content_base64": "YWJj"})
        self.assertEqual(self.plane.evidence.list(self.episode_id), [])
        self.assertEqual(list(self.plane.evidence.root.rglob("*")), [])

    def test_text_character_limit_accepts_full_utf8_content_without_truncating_original(self):
        self._ready_core()
        content = "\U0001f4dd" * TEXT_LIMIT
        attachment = self.plane.submit_evidence(self.episode_id, {"kind": "text", "content_text": content})
        self.assertEqual(attachment["extraction"]["content_text"], content)
        self.assertEqual(attachment["size_bytes"], 4 * TEXT_LIMIT)
        self.assertEqual(attachment["sha256"], hashlib.sha256(content.encode("utf-8")).hexdigest())
        path, mime_type = self.plane.evidence.file(attachment["attachment_id"])
        self.assertEqual(path.read_bytes(), content.encode("utf-8"))
        self.assertEqual(mime_type, "text/plain; charset=utf-8")
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertLessEqual(len(self.plane.evidence.model_evidence(self.episode_id)[0]["summary"]), 1800)

    def test_context_notes_share_text_limit_for_all_kinds_and_corrections(self):
        self._ready_core()
        contents = {
            "text": {"content_text": "Operator observation"},
            "image": {"content_base64": base64.b64encode(PIXEL_PNG).decode("ascii")},
            "audio": {"content_base64": base64.b64encode(b"RIFF\x00\x00\x00\x00WAVE").decode("ascii")},
        }
        with patch.object(self.plane, "media_submission_allowed", return_value=(True, "")), \
                patch.object(self.plane.evidence.executor, "submit"):
            for kind, content in contents.items():
                with self.subTest(kind=kind):
                    attachment = self.plane.submit_evidence(self.episode_id, {
                        "kind": kind, **content, "context_note": "\u65e5" * TEXT_LIMIT,
                    })
                    self.assertEqual(attachment["context_note"], "\u65e5" * TEXT_LIMIT)
                    corrected = self.plane.correct_evidence(attachment["attachment_id"], {
                        "context_note": "\u6708" * TEXT_LIMIT,
                    })
                    self.assertEqual(corrected["context_note"], "\u6708" * TEXT_LIMIT)
        self.assertTrue(all(item["context_note"] == "\u6708" * TEXT_LIMIT
                            for item in self.plane.evidence.manifest(self.episode_id)))

    def test_context_note_rejects_invalid_input_before_submission_or_correction(self):
        self._ready_core()
        invalid_notes = ("x" * (TEXT_LIMIT + 1), "\ud800", {}, [], 123)
        payload = {"kind": "text", "content_text": "Operator observation"}
        for note in invalid_notes:
            with self.subTest(note_type=type(note).__name__), self.assertRaisesRegex(ValueError, "context_note"):
                self.plane.submit_evidence(self.episode_id, {**payload, "context_note": note})
        self.assertEqual(list(self.plane.evidence.root.rglob("*")), [])
        attachment = self.plane.submit_evidence(self.episode_id, {**payload, "context_note": "Original note"})
        for note in invalid_notes:
            with self.subTest(note_type=type(note).__name__), self.assertRaisesRegex(ValueError, "context_note"):
                self.plane.correct_evidence(attachment["attachment_id"], {"context_note": note})
        self.assertEqual(self.plane.evidence.list(self.episode_id)[0], attachment)

    def test_redaction_expansion_is_rejected_instead_of_silently_truncating_text(self):
        self._ready_core()
        content = "password=x " * 1400
        self.assertLess(len(content), TEXT_LIMIT)
        for field in ("content_text", "context_note"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "after redaction"):
                self.plane.submit_evidence(self.episode_id, {
                    "kind": "text", "content_text": "Operator observation", field: content,
                })
        self.assertEqual(list(self.plane.evidence.root.rglob("*")), [])

    def test_text_redacts_source_notes_and_filename_before_storage_and_export(self):
        self._ready_core()
        attachment = self.plane.submit_evidence(self.episode_id, {
            "kind": "text", "filename": "operator@example.com.log",
            "content_text": '  Error from 10.2.3.4 user=alice@example.com\n'
                            'password=hunter-two token=private-token\n'
                            '{"api_key":"private-key"}\nAuthorization: Bearer private-bearer\n',
            "context_note": "secret=private-note",
        })
        record = self.plane.store.get_evidence_attachment(attachment["attachment_id"])
        stored_text = Path(record["storage_path"]).read_text(encoding="utf-8")
        self.assertEqual(stored_text, record["extraction"]["content_text"])
        self.assertTrue(stored_text.startswith("  Error"))
        self.assertTrue(stored_text.endswith("\n"))
        retained = stored_text + json.dumps([
            record, self.plane.evidence.model_evidence(self.episode_id), self.plane.evidence.manifest(self.episode_id),
        ])
        for secret in ("10.2.3.4", "alice@example.com", "hunter-two", "private-token", "private-key",
                       "private-bearer", "private-note", "operator@example.com"):
            self.assertNotIn(secret, retained)
        self.assertIn("<SECRET>", retained)
        self.assertIn("<EMAIL>", retained)
        self.assertTrue(record["source_redacted"])
        self.assertEqual(record["sha256"], hashlib.sha256(stored_text.encode("utf-8")).hexdigest())

    def test_text_claims_remain_distinct_from_telemetry_and_unknown_observation_time(self):
        self._ready_core()
        for observed_at in (None, "", "2026-09-22T10:00:00Z"):
            with self.subTest(observed_at=observed_at):
                attachment = self.plane.submit_evidence(self.episode_id, {
                    "kind": "text", "content_text": "The cache caused the outage.", "observed_at": observed_at,
                    "context_note": "Unconfirmed handover report",
                })
                item = next(item for item in self.plane.evidence.model_evidence(self.episode_id)
                            if item["attachment_id"] == attachment["attachment_id"])
                self.assertEqual(item["id"], "A-" + attachment["attachment_id"])
                self.assertEqual(item["domain"], "operator_context")
                self.assertTrue(item["summary"].startswith("Operator-supplied text (unverified claim):"))
                self.assertIn("not independently verified telemetry", item["limitation"])
                self.assertEqual(item["examples"], [])
                self.assertNotIn("observations", attachment["extraction"])
                self.assertEqual(item["operator_context"]["note"], "Unconfirmed handover report")
                self.assertEqual(item["time_range"]["observed_at"], observed_at or None)
                self.assertEqual(item["time_range"]["uploaded_at"], attachment["uploaded_at"])

    def test_text_original_is_immutable_and_corrections_are_additive(self):
        self._ready_core()
        attachment = self.plane.submit_evidence(self.episode_id, {
            "kind": "text", "content_text": "The target is down.", "context_note": "n" * TEXT_LIMIT,
        })
        corrected = self.plane.correct_evidence(attachment["attachment_id"], {
            "content_text": "Replacement must not overwrite the original.",
            "correction": "The target was disabled. password=correction-secret",
            "context_note": "u" * TEXT_LIMIT,
        })
        self.assertEqual(len(attachment["context_note"]), TEXT_LIMIT)
        self.assertEqual(len(corrected["context_note"]), TEXT_LIMIT)
        self.assertEqual(self.plane.evidence.manifest(self.episode_id)[0]["context_note"], corrected["context_note"])
        self.assertEqual(corrected["extraction"], attachment["extraction"])
        self.assertEqual(corrected["sha256"], attachment["sha256"])
        self.assertEqual(corrected["uploaded_at"], attachment["uploaded_at"])
        path, _ = self.plane.evidence.file(attachment["attachment_id"])
        self.assertEqual(path.read_text(encoding="utf-8"), "The target is down.")
        with self.assertRaises(FileExistsError):
            self.plane.evidence._write(attachment["attachment_id"], ".txt", b"replacement")
        item = self.plane.evidence.model_evidence(self.episode_id)[0]
        self.assertIn("target was disabled", item["operator_context"]["correction"])
        self.assertNotIn("correction-secret", json.dumps(corrected))
        self.assertNotIn("target was disabled", item["summary"])

    def test_text_invalid_metadata_unknown_episode_and_failed_insert_leave_no_artifact(self):
        self._ready_core()
        payload = {"kind": "text", "content_text": "Operator observation"}
        with self.assertRaisesRegex(ValueError, "ISO-8601"):
            self.plane.submit_evidence(self.episode_id, {**payload, "observed_at": "yesterday"})
        with self.assertRaises(KeyError):
            self.plane.submit_evidence("unknown-episode", payload)
        with patch.object(self.plane.store, "record_evidence_attachment", side_effect=ValueError("Insert failed")):
            with self.assertRaisesRegex(ValueError, "Insert failed"):
                self.plane.submit_evidence(self.episode_id, payload)
        self.assertEqual(self.plane.evidence.list(self.episode_id), [])
        self.assertEqual(list(self.plane.evidence.root.rglob("*.txt")), [])

    def test_text_artifact_resolution_cannot_escape_evidence_root(self):
        self._ready_core()
        attachment = self.plane.submit_evidence(self.episode_id, {
            "kind": "text", "content_text": "Operator observation", "filename": "../../context.html",
        })
        self.assertEqual(attachment["filename"], "context.html.txt")
        record = self.plane.store.get_evidence_attachment(attachment["attachment_id"])
        with patch.object(self.plane.store, "get_evidence_attachment", return_value={
            **record, "storage_path": str(REFERENCE_CASE / "alert.json"),
        }):
            self.assertIsNone(self.plane.evidence.file(attachment["attachment_id"]))

    def test_text_manifest_exports_and_revision_priority_use_existing_attachment_path(self):
        with patch.object(self.plane.investigator, "start_by_incident"):
            capsule = self.plane._build_capsule(self.incident_id)
        self._ready_core()
        attachment = self.plane.submit_evidence(self.episode_id, {
            "kind": "text", "content_text": "Operator reports the endpoint returned HTTP 404.",
        })
        with ZipFile(capsule["archive_path"]) as archive:
            manifests = [name for name in archive.namelist() if name.endswith("evidence_manifest.json")]
            self.assertEqual(len(manifests), 1)
            exported = json.loads(archive.read(manifests[0]))["attachments"]
            self.assertFalse(any(name.endswith(".txt") for name in archive.namelist()))
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported, self.plane.evidence.manifest(self.episode_id))
        self.assertEqual(exported[0]["kind"], "text")
        self.assertEqual(exported[0]["status"], "ready")
        self.assertIsNone(exported[0]["observed_at"])
        report = self.plane.incident_report_payload(self.incident_id)
        self.assertEqual(len(report["media_evidence"]), 1)
        with patch.object(self.plane.briefing_executor, "submit"):
            queued = self.plane.update_investigation_with_evidence(self.episode_id)
        with patch("fcapsule.investigation_service.run_investigation") as investigate:
            self.plane.investigator._run(self.episode_id, queued)
        investigate.assert_called_once()
        context = investigate.call_args.args[0]
        citation = "A-" + attachment["attachment_id"]
        self.assertIn(citation, context["priority_evidence_ids"])
        addition = next(item for item in context["evidence"] if item["id"] == citation)
        self.assertTrue(addition["revision_addition"])
        compact, visible = compact_for_model(context, [], max_prompt_tokens=1200,
                                             priority_evidence_ids=context["priority_evidence_ids"])
        self.assertIn(citation, visible)
        self.assertEqual(compact["evidence"][0]["id"], citation)
        self.assertIn("unverified claim", compact["evidence"][0]["summary"])
        self.plane.remove_evidence(attachment["attachment_id"])
        self.assertEqual(self.plane.evidence.manifest(self.episode_id), [])
        self.assertIsNone(self.plane.evidence.file(attachment["attachment_id"]))

    def test_text_http_submission_download_list_and_validation(self):
        server = FCAPSuleHTTPServer(("127.0.0.1", 0), self.plane)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        endpoint = f"{base}/api/episodes/{self.episode_id}/evidence"

        def request(content):
            return Request(endpoint, data=json.dumps({"kind": "text", "content_text": content}).encode("utf-8"),
                           headers={"Content-Type": "application/json"}, method="POST")

        try:
            with self.assertRaises(HTTPError) as blocked:
                urlopen(request("operator note"), timeout=3)
            self.assertEqual(blocked.exception.code, 400)
            blocked.exception.close()
            self._ready_core()
            with urlopen(request("<script>alert(1)</script> password=private-http"), timeout=3) as response:
                self.assertEqual(response.status, 202)
                attachment = json.loads(response.read())
            self.assertEqual(attachment["status"], "ready")
            with urlopen(base + attachment["artifact_url"], timeout=3) as response:
                self.assertEqual(response.headers["Content-Type"], "text/plain; charset=utf-8")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                self.assertTrue(response.headers["Content-Disposition"].startswith("attachment;"))
                self.assertNotIn(b"private-http", response.read())
            with urlopen(endpoint, timeout=3) as response:
                self.assertEqual(json.loads(response.read()), [attachment])
            for content in ("x" * (TEXT_LIMIT + 1), "\ud800"):
                with self.assertRaises(HTTPError) as invalid:
                    urlopen(request(content), timeout=3)
                self.assertEqual(invalid.exception.code, 400)
                invalid.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_server_startup_resumes_media_evidence_left_queued(self):
        self._ready_core()
        os.environ["OPENROUTER_API_KEY"] = "test-media-key"
        model = self.plane.media_configuration()["vision"]["model"]
        self.plane._set_capability("media_vision_capability", "test-media-key", model, "ready", "Test validation")
        with patch.object(self.plane.evidence.executor, "submit"), patch.object(
            self.plane, "media_submission_allowed", return_value=(True, "")
        ):
            attachment = self.plane.submit_evidence(self.episode_id, {
                "kind": "image", "content_base64": base64.b64encode(PIXEL_PNG).decode("ascii"),
            })
        self.assertEqual(attachment["status"], "queued")
        self.plane.evidence.shutdown(wait=True)
        self.plane.briefing_executor.shutdown(wait=True, cancel_futures=True)

        server = None
        try:
            with patch("fcapsule.evidence_service.OpenRouterClient") as client:
                client.return_value.visual_extract.return_value = {
                    "content": '{"visible_text":["Target down"],"observations":[],"ambiguities":[],"limitation":"Visible state only."}',
                    "usage": {"total_tokens": 21},
                }
                server = create_app_server("127.0.0.1", 0, Path(self.directory.name) / "state")
                deadline = time.time() + 3
                stored = None
                while time.time() < deadline:
                    stored = server.control_plane.store.get_evidence_attachment(attachment["attachment_id"])
                    if stored and stored["status"] in {"ready", "failed", "blocked"}:
                        break
                    time.sleep(0.02)

                self.assertEqual(stored["status"], "ready")
                self.assertEqual(stored["extraction"]["visible_text"], ["Target down"])
                client.assert_called_once_with(timeout_seconds=90)
                client.return_value.visual_extract.assert_called_once()
        finally:
            if server is not None:
                server.control_plane.evidence.shutdown(wait=True)
                server.control_plane.briefing_executor.shutdown(wait=True, cancel_futures=True)
                server.server_close()

    def test_server_startup_does_not_replay_media_evidence_already_processing(self):
        self._ready_core()
        os.environ["OPENROUTER_API_KEY"] = "test-media-key"
        model = self.plane.media_configuration()["vision"]["model"]
        self.plane._set_capability("media_vision_capability", "test-media-key", model, "ready", "Test validation")
        with patch.object(self.plane.evidence.executor, "submit"), patch.object(
            self.plane, "media_submission_allowed", return_value=(True, "")
        ):
            attachment = self.plane.submit_evidence(self.episode_id, {
                "kind": "image", "content_base64": base64.b64encode(PIXEL_PNG).decode("ascii"),
            })
        self.plane.store.update_evidence_attachment(attachment["attachment_id"], status="processing")
        self.plane.evidence.shutdown(wait=True)
        self.plane.briefing_executor.shutdown(wait=True, cancel_futures=True)

        server = None
        try:
            with patch("fcapsule.evidence_service.OpenRouterClient") as client:
                server = create_app_server("127.0.0.1", 0, Path(self.directory.name) / "state")
                stored = server.control_plane.store.get_evidence_attachment(attachment["attachment_id"])

                self.assertEqual(stored["status"], "failed")
                self.assertIn("interrupted", stored["extraction"]["limitation"].lower())
                self.assertIn("upload", stored["extraction"]["limitation"].lower())
                client.assert_not_called()
        finally:
            if server is not None:
                server.control_plane.evidence.shutdown(wait=True)
                server.control_plane.briefing_executor.shutdown(wait=True, cancel_futures=True)
                server.server_close()

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
        self.assertEqual(evidence[0]["visible_text"], ["Target down"])
        public = self.plane.evidence.list(self.episode_id)[0]
        self.assertNotIn("storage_path", public)
        self.assertIn("artifact_url", public)
        manifest = self.plane.evidence.manifest(self.episode_id)[0]
        self.assertEqual(manifest["extraction"]["visible_text"], ["Target down"])
        self.assertEqual(manifest["context_note"], "Screenshot after the alert")

    def test_audio_still_uses_validated_specialist_and_remains_a_transcript(self):
        self._ready_core()
        os.environ["OPENROUTER_API_KEY"] = "test-media-key"
        model = self.plane.media_configuration()["audio"]["model"]
        self.plane._set_capability("media_audio_capability", "test-media-key", model, "ready", "Test validation")
        content = b"RIFF\x00\x00\x00\x00WAVE"
        with patch("fcapsule.evidence_service.OpenRouterClient") as client:
            client.return_value.transcribe.return_value = {
                "transcript": "The target was disabled. password=audio-secret",
                "usage": {"total_tokens": 5}, "segments": [],
            }
            attachment = self.plane.submit_evidence(self.episode_id, {
                "kind": "audio", "content_base64": base64.b64encode(content).decode("ascii"),
            })
            stored = self._wait(attachment["attachment_id"])
            client.return_value.transcribe.assert_called_once_with(content, "audio/wav", model)
            client.return_value.visual_extract.assert_not_called()
        self.assertEqual(attachment["status"], "queued")
        self.assertEqual(stored["status"], "ready")
        self.assertEqual(stored["provider"], "openrouter")
        self.assertEqual(stored["model"], model)
        self.assertEqual(stored["usage"], {"total_tokens": 5})
        self.assertNotIn("audio-secret", json.dumps(stored["extraction"]))
        self.assertEqual(self.plane.evidence.model_evidence(self.episode_id)[0]["domain"], "audio_transcript")

    def test_media_validates_file_types_even_when_models_are_ready(self):
        with patch.object(self.plane, "media_submission_allowed", return_value=(True, "")):
            for kind in ("image", "audio"):
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    self.plane.submit_evidence(self.episode_id, {
                        "kind": kind, "content_base64": base64.b64encode(b"plain text").decode("ascii"),
                    })
        self.assertEqual(self.plane.evidence.list(self.episode_id), [])

    def test_image_extraction_recovers_a_json_object_wrapped_by_provider_prose(self):
        extraction = _extract_json(
            'Here is the requested object: {"visible_text":["Target down"],'
            '"observations":[{"fact":"checkout target is down","confidence":"high","region":"table"}],'
            '"ambiguities":[],"limitation":"Visible status only."}'
        )
        self.assertEqual(extraction["visible_text"], ["Target down"])
        self.assertEqual(extraction["observations"][0]["confidence"], "high")

    def test_image_extraction_normalizes_numeric_provider_confidence(self):
        extraction = _extract_json(
            '{"visible_text":[],"observations":[{"fact":"Target is down","confidence":0.9,"region":"table"}],'
            '"ambiguities":[],"limitation":"Visible status only."}'
        )
        self.assertEqual(extraction["observations"][0]["confidence"], "high")

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
