import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from fcapsule.control_plane import ControlPlane
from fcapsule.investigation_service import InvestigationService
from fcapsule.ui.app import create_app_server
from tests.common import REFERENCE_CASE


class _InlineExecutor:
    def submit(self, function, *args, **kwargs):
        return function(*args, **kwargs)

    def shutdown(self, **_kwargs):
        return None


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

    def test_provider_switch_does_not_auto_queue_unrelated_retained_backlog(self):
        self.control.briefing_executor.shutdown(wait=True)
        self.control.briefing_executor = _InlineExecutor()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": ""}):
            self.control._build_capsule(self.id)
            second = self.control.store.record_incident({
                **self.incident,
                "incident_id": "unrelated-backlog",
                "scenario": "Unrelated retained report",
                "recurrence_key": "unrelated-backlog-family",
                "resource_name": "payments-worker",
                "alert_identity": "PaymentsAlert",
            })
            second_episode_id = self.control.store.episode_for_incident(second["incident_id"])["episode_id"]
            self.assertNotEqual(second_episode_id, self.episode_id)
            self.control._build_capsule(second["incident_id"])
            self.control.investigator.path(second_episode_id).unlink()

        episode_ids = [self.episode_id, second_episode_id]
        prior = {episode_id: self.control.investigator.read(episode_id) for episode_id in episode_ids}
        self.assertEqual(prior[self.episode_id]["status"], "not_configured")
        self.assertEqual(prior[second_episode_id]["status"], "not_started")

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "already-configured-router-key"}), patch(
            "fcapsule.investigation_service.run_investigation"
        ) as generate:
            self.control.update_ai_configuration({"provider": "openrouter"})

        generate.assert_not_called()
        for episode_id in episode_ids:
            current = self.control.investigator.read(episode_id)
            self.assertEqual(current["status"], prior[episode_id]["status"])
            self.assertEqual(current.get("revision_id"), prior[episode_id].get("revision_id"))

    def test_configuring_previously_missing_provider_key_resumes_blocked_work(self):
        self.control.briefing_executor.shutdown(wait=True)
        self.control.briefing_executor = _InlineExecutor()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": ""}):
            self.control._build_capsule(self.id)

        contexts = []

        def finish(context, _tools, _model, _max_tokens, publish):
            contexts.append(context)
            publish({"episode_id": context["episode_id"], "status": "ready", "checks": [],
                     "assessment": {"summary": "Retained"}})

        with patch.object(
            self.control, "_validate_core", return_value={"status": "ready", "message": "Validated."}
        ), patch("fcapsule.investigation_service.run_investigation", side_effect=finish) as generate:
            self.control.update_ai_configuration({"api_key": "new-deepseek-key-123"})

        generate.assert_called_once()
        self.assertEqual(contexts[0]["episode_id"], self.episode_id)
        self.assertEqual(contexts[0]["investigation_limits"]["provider"], "deepseek")
        self.assertEqual(self.control.investigator.read(self.episode_id)["status"], "ready")

    def test_402_failure_stays_historical_until_explicit_openrouter_retry(self):
        self.control.briefing_executor.shutdown(wait=True)
        self.control.briefing_executor = _InlineExecutor()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": ""}):
            self.control._build_capsule(self.id)

        def fail_with_402(context, _tools, _model, _max_tokens, publish):
            self.assertEqual(context["investigation_limits"]["provider"], "deepseek")
            publish({
                "episode_id": context["episode_id"], "status": "incomplete",
                "finished_at": "2026-09-24T00:00:00Z",
                "message": "DeepSeek API returned HTTP 402: insufficient credit",
                "checks": [], "assessment": None,
                "calls": [{"status": "failed", "provider": "deepseek", "error": "HTTP 402"}],
            })

        with patch.dict(os.environ, {
            "DEEPSEEK_API_KEY": "existing-deepseek-key", "OPENROUTER_API_KEY": "existing-openrouter-key",
        }), patch("fcapsule.investigation_service.run_investigation", side_effect=fail_with_402):
            self.control.investigator.start(self.episode_id, retry=True)

            failed = self.control.investigator.read(self.episode_id)
            original_revision_id = failed["revision_id"]
            retained_fingerprint = failed["input_fingerprint"]
            self.assertEqual(failed["status"], "incomplete")

            self.control.update_ai_configuration({"provider": "openrouter"})
            after_switch = self.control.investigator.read(self.episode_id)
            self.assertEqual(after_switch["revision_id"], original_revision_id)
            self.assertEqual(after_switch["status"], "incomplete")
            self.assertEqual(after_switch["input_fingerprint"], retained_fingerprint)
            self.assertIn("402", after_switch["message"])

            retry_contexts = []

            def finish_on_openrouter(context, _tools, _model, _max_tokens, publish):
                retry_contexts.append(context)
                publish({
                    "episode_id": context["episode_id"], "status": "ready", "checks": [],
                    "assessment": {"summary": "Retained evidence reviewed."},
                    "calls": [{"status": "completed", "provider": "openrouter"}],
                })

            with patch("fcapsule.investigation_service.run_investigation", side_effect=finish_on_openrouter):
                self.control.investigator.start(self.episode_id, retry=True)

            retried = self.control.investigator.read(self.episode_id)
            self.assertEqual(retry_contexts[0]["episode_id"], self.episode_id)
            self.assertEqual(retry_contexts[0]["investigation_limits"]["provider"], "openrouter")
            self.assertEqual(retried["input_fingerprint"], retained_fingerprint)
            self.assertEqual(retried["parent_revision_id"], original_revision_id)
            self.assertEqual(retried["status"], "ready")

            revisions = self.control.investigator.revisions(self.episode_id)
            old_revision = next(item for item in revisions if item["revision_id"] == original_revision_id)
            old_state = json.loads(Path(old_revision["state_path"]).read_text())
            self.assertEqual(old_revision["status"], "incomplete")
            self.assertIn("402", old_state["message"])
            self.assertEqual(old_state["calls"][0]["provider"], "deepseek")

    def test_evidence_revision_marks_newest_media_ahead_of_member_priorities(self):
        self.control._build_capsule(self.id)
        media = [{"id": "A-old", "time_range": {"uploaded_at": "2026-09-22T10:00:00Z"}},
                 {"id": "A-new", "time_range": {"uploaded_at": "2026-09-23T10:00:00Z"}}]
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), patch.object(
            self.control.evidence, "model_evidence", return_value=media
        ), patch("fcapsule.investigation_service.run_investigation") as generate:
            self.control.investigator.start(self.episode_id, retry=True, reason="evidence_added")
            self.control.briefing_executor.shutdown(wait=True)
        generate.assert_called_once()
        context = generate.call_args.args[0]
        revisions = [item for item in context["evidence"] if item.get("revision_addition")]
        self.assertEqual([item["id"] for item in revisions], ["A-new", "A-old"])
        self.assertIn("A-new", context["priority_evidence_ids"])
        self.assertNotIn("revision_addition", media[0])

    def test_failure_is_persisted_and_not_retried_by_resume(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), patch(
            "fcapsule.investigation_service.run_investigation", side_effect=RuntimeError("provider down")
        ) as generate:
            self.control._build_capsule(self.id)
            self.control.briefing_executor.shutdown(wait=True)
            self.assertEqual(self.control.incident_report_payload(self.id)["investigation"]["status"], "incomplete")
            self.control.investigator.resume()
            self.assertEqual(generate.call_count, 1)

    def test_restart_does_not_replace_a_completed_manual_evidence_revision(self):
        self.control._build_capsule(self.id)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), patch(
            "fcapsule.investigation_service.run_investigation",
            side_effect=lambda *args: args[4]({"status": "ready", "checks": [], "assessment": {}}),
        ) as generate:
            self.control.investigator.start(self.episode_id, retry=True, reason="evidence_added")
            self.control.briefing_executor.shutdown(wait=True)
            prior = self.control.investigator.read(self.episode_id)
            self.control.investigator.resume()
            current = self.control.investigator.read(self.episode_id)
            legacy = dict(prior)
            legacy.pop("primary_incident_id", None)
            episode = self.control.store.get_episode(self.episode_id)
            legacy["input_fingerprint"] = self.control.investigator.fingerprint(
                self.control.investigator.entries(episode), self.control.evidence.manifest(self.episode_id), None
            )
            self.control._write_briefing_state(self.control.investigator.path(self.episode_id), legacy)
            self.control.investigator.resume()
            self.assertEqual(self.control.investigator.read(self.episode_id)["revision_id"], prior["revision_id"])
        generate.assert_called_once()
        self.assertEqual(current["revision_id"], prior["revision_id"])
        self.assertEqual(current["revision_reason"], "evidence_added")
        self.assertEqual(current["primary_incident_id"], self.id)

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
        episode = self.control.store.get_episode(self.episode_id)
        entries = self.control.investigator.entries(episode)
        fingerprint = self.control.investigator.fingerprint(
            entries, self.control.evidence.manifest(self.episode_id), self.id
        )
        self.control._write_briefing_state(path, {
            "episode_id": self.episode_id, "revision_id": "revision-interrupted",
            "parent_revision_id": None, "revision_reason": "evidence_added", "source_mode": "retained_only",
            "status": "running", "started_at": "2026-09-26T00:00:00Z", "attempt": 3,
            "primary_incident_id": self.id, "input_fingerprint": fingerprint,
            "checks": [{"id": "Q001", "tool": "search_logs", "status": "completed",
                        "result": {"observations": [{"reason": "Retained log line"}]}}],
            "calls": [{"phase": "evidence_review", "status": "running"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "complete": True},
        })
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), patch(
            "fcapsule.investigation_service.run_investigation",
            side_effect=lambda *args: args[4]({"status": "ready", "checks": [], "assessment": {}}),
        ) as generate:
            self.control.investigator.resume()
            self.control.briefing_executor.shutdown(wait=True)
            self.assertEqual(generate.call_count, 1)
            resumed = json.loads(path.read_text())
            self.assertEqual(resumed["status"], "ready")
            self.assertFalse(resumed["lifetime_usage"]["complete"])
            self.assertEqual(resumed["revision_reason"], "evidence_added")
            self.assertEqual(resumed["source_mode"], "retained_only")
            interrupted = resumed["previous_runs"][-1]
            self.assertEqual(interrupted["status"], "incomplete")
            self.assertEqual(interrupted["checks"][0]["status"], "completed")
            self.assertEqual(interrupted["calls"][0]["status"], "failed")
            self.assertTrue(interrupted["calls"][0].get("finished_at"))
            revision = next(
                item for item in self.control.investigator.revisions(self.episode_id)
                if item["revision_id"] == "revision-interrupted"
            )
            self.assertEqual(revision["status"], "incomplete")

    def test_startup_automatically_recovers_an_interruption_at_most_once(self):
        self.control._build_capsule(self.id)
        episode = self.control.store.get_episode(self.episode_id)
        entries = self.control.investigator.entries(episode)
        fingerprint = self.control.investigator.fingerprint(
            entries, self.control.evidence.manifest(self.episode_id), self.id
        )
        self.control._write_briefing_state(self.control.investigator.path(self.episode_id), {
            "episode_id": self.episode_id, "revision_id": "revision-interrupted-once",
            "revision_reason": "evidence_added", "source_mode": "retained_only",
            "status": "running", "started_at": "2026-09-26T00:00:00Z", "attempt": 1,
            "primary_incident_id": self.id, "input_fingerprint": fingerprint,
            "checks": [{"id": "Q001", "status": "completed", "result": {"fact": "retained"}}],
            "calls": [{"status": "running"}],
        })

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), patch.object(
            self.control.briefing_executor, "submit"
        ) as first_submit:
            self.control.investigator.resume()
            first_submit.assert_called_once()
            first_state = self.control.investigator.read(self.episode_id)
            self.assertEqual(first_state["status"], "queued")
            self.assertEqual(first_state["recovery_attempt"], 1)

            self.control.investigator = InvestigationService(self.control)
            with patch.object(self.control.briefing_executor, "submit") as second_submit:
                self.control.investigator.resume()
                second_submit.assert_not_called()

        stopped = self.control.investigator.read(self.episode_id)
        self.assertEqual(stopped["status"], "incomplete")
        self.assertEqual(stopped["previous_runs"][-1]["checks"][0]["status"], "completed")
        self.assertEqual(stopped["recovery_attempt"], 1)

    def test_new_evidence_waits_for_explicit_reassessment_across_restart(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}):
            config = self.control.ai_configuration()
            self.control._set_capability(
                "ai_core_capability", "test-key", config["model"], "ready", "Test validation"
            )

            def finish(*args, **_kwargs):
                args[4]({"status": "ready", "checks": [], "assessment": {"summary": "Retained result"}})

            with patch("fcapsule.investigation_service.run_investigation", side_effect=finish) as assess:
                self.control._build_capsule(self.id)
                self.control.briefing_executor.shutdown(wait=True)
                self.assertEqual(assess.call_count, 1)
                prior = self.control.investigator.read(self.episode_id)

            attachment = self.control.submit_evidence(self.episode_id, {
                "kind": "text", "content_text": "An operator added context after the assessment.",
            })
            self.assertEqual(attachment["status"], "ready")
            self.control.evidence.shutdown(wait=True)

            server = None
            try:
                with patch("fcapsule.investigation_service.run_investigation", side_effect=finish) as assess:
                    server = create_app_server("127.0.0.1", 0, Path(self.directory.name))
                    after_restart = server.control_plane.investigator.read(self.episode_id)
                    self.assertEqual(after_restart["revision_id"], prior["revision_id"])
                    assess.assert_not_called()

                    server.control_plane.update_investigation_with_evidence(self.episode_id)
                    server.control_plane.briefing_executor.shutdown(wait=True)
                    assess.assert_called_once()

                reassessed = server.control_plane.investigator.read(self.episode_id)
                self.assertEqual(reassessed["status"], "ready")
                self.assertEqual(reassessed["revision_reason"], "evidence_added")
                self.assertEqual(reassessed["parent_revision_id"], prior["revision_id"])
            finally:
                if server is not None:
                    server.control_plane.evidence.shutdown(wait=True)
                    server.control_plane.briefing_executor.shutdown(wait=True, cancel_futures=True)
                    server.server_close()

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

    def test_member_arriving_during_investigation_gets_a_focused_follow_up(self):
        entered, release, follow_up_entered = threading.Event(), threading.Event(), threading.Event()
        contexts = []

        def provider(context, kit, model, limit, publish):
            contexts.append(context)
            if len(contexts) == 1:
                publish({"status": "running", "checks": [], "assessment": None})
                entered.set()
                release.wait(5)
            else:
                follow_up_entered.set()
            publish({"status": "ready", "checks": [], "assessment": {"summary": "Retained"}})

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}), patch(
            "fcapsule.investigation_service.run_investigation", side_effect=provider
        ) as generate:
            try:
                self.control._build_capsule(self.id)
                self.assertTrue(entered.wait(2))
                self.control.store.record_incident({**self.incident, "incident_id": "second-signal"})
                self.control._build_capsule("second-signal")
                queued = self.control.investigator.read(self.episode_id)
                self.assertEqual(queued["pending_primary_incident_id"], "second-signal")
                self.assertEqual(queued["follow_up_status"], "queued")
            finally:
                release.set()
                follow_up_entered.wait(2)
                self.control.briefing_executor.shutdown(wait=True)

        self.assertEqual(generate.call_count, 2, json.dumps({
            "targets": [item["primary_incident_id"] for item in contexts],
            "current_primary": self.control.store.get_episode(self.episode_id)["primary_incident_id"],
            "final": self.control.investigator.read(self.episode_id),
        }))
        self.assertEqual(contexts[0]["primary_incident_id"], self.id)
        self.assertEqual(contexts[1]["primary_incident_id"], "second-signal")
        self.assertEqual(self.control.investigator.read(self.episode_id)["primary_incident_id"], "second-signal")
