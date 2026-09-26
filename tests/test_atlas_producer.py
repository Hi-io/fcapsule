from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fcapsule.estima_client import EstimaClient, EstimaClientError, estima_client_from_env, estima_settings_from_env, validate_estima_url
from fcapsule.estima_projection import project_estima_record
from fcapsule.control_plane import ControlPlane
from fcapsule.store import FCAPSuleStore


STAMP = "2026-09-26T03:00:00Z"


def retained_fixture(pod: str = "queue-7", category: str = "scrape_health"):
    episode = {
        "episode_id": f"episode-{pod}", "app_id": "queue-service", "started_at": STAMP,
        "last_activity_at": STAMP, "resource_kind": "pod", "resource_name": pod,
        "recurrence_key": f"queue-service|pod|{pod}|QueueHigh",
        "resource": {"kind": "pod", "name": pod},
    }
    capsule = {
        "case": {"service": "queue-api", "window": {"end": STAMP}},
        "alerts": [{"alertname": "QueueHigh", "labels": {"cnfc": "edge-a", "vnfc": "blue"}}],
        "selected_evidence": [
            {"evidence_id": "ev_alert_001", "type": "alert", "title": "QueueHigh",
             "time_range": {"start": STAMP, "end": STAMP}},
            {"evidence_id": "ev_log_001", "type": "log_template", "title": "raw line must not ship",
             "representative_lines": ["password=supersecret raw log body"],
             "diagnostic_fields": {"error_type": "TimeoutError", "password": "supersecret", "error_message": "raw body"},
             "time_range": {"end": STAMP}},
            {"evidence_id": "ev_metric_001", "type": "metric_anomaly", "metric": "queue_depth",
             "metric_observation": {"metric": "queue_depth", "unit": "messages",
                                    "condition": {"latest": {"timestamp": STAMP, "value": 27.0}}},
             "time_range": {"end": STAMP}},
            {"evidence_id": "ev_config_001", "type": "configuration",
             "configuration": {"kind": "ConfigMap", "name": "queue-policy", "resource_version": "12",
                               "data": {"queue_limit": "30", "password": "supersecret", "endpoint": "mysql://user:pw@db"}}},
        ],
    }
    investigation = {
        "status": "ready", "finished_at": STAMP,
        "assessment": {"likely_mechanism": "The queue scrape selection may be misconfigured, but cause is unverified.",
                       "evidence_ids": ["Q001"]},
        "findings": [{"state": "observed", "category": category, "evidence_ids": ["Q001"]}],
    }
    app = {"name": "queue-worker", "namespace": "jobs", "cluster": "cluster-1", "environment": "prod"}
    return episode, investigation, [{"capsule": capsule, "report": {"pm_signals": [
        {"metric": "queue_depth", "peak_value": 27.0, "unit": "messages", "alert_timestamp": STAMP,
         "evidence_id": "ev_metric_001"},
    ]}}], app


class CapturingAtlas:
    def __init__(self, error: Exception | None = None, response: dict | None = None):
        self.calls = []
        self.error = error
        self.response = response if response is not None else {"created": True}

    def create_case(self, payload):
        self.calls.append(payload)
        if self.error:
            raise self.error
        return self.response


class AtlasProjectionTests(unittest.TestCase):
    def test_projection_is_compact_cited_and_excludes_raw_logs_and_secrets(self):
        episode, investigation, retained, app = retained_fixture()
        payload = project_estima_record("instance-a", episode, investigation, retained, app)
        encoded = json.dumps(payload)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["scope"]["environment"], "prod")
        self.assertEqual(payload["scope"]["cnfc_id"], "edge-a")
        self.assertEqual(payload["scope"]["vnfc_id"], "blue")
        self.assertTrue(payload["fingerprint"].startswith("fcapsule-atlas-normalization-v1:"))
        self.assertEqual(payload["hypotheses"][0]["confidence"], None)
        self.assertIn("Unverified assessment:", payload["hypotheses"][0]["statement"])
        self.assertIn("queue_depth=27 messages", payload["summary"])
        self.assertIn("queue_limit", encoded)
        self.assertNotIn("supersecret", encoded)
        self.assertNotIn("raw log body", encoded)
        metric = next(item for item in payload["observations"] if item["kind"] == "PM")
        self.assertEqual(metric["value"], 27.0)
        self.assertEqual(metric["unit"], "messages")
        self.assertEqual(metric["observed_at"], STAMP)
        self.assertEqual(payload["hypotheses"][0]["supporting_refs"], ["Q001"])

    def test_uuid_instance_identity_stays_distinct_without_exposing_unsafe_ids(self):
        episode, investigation, retained, app = retained_fixture()
        first_id = "fcapsule-9ddb5d57-d0e2-4f13-88d5-8c8d36f5089c"
        second_id = "fcapsule-20988638-7f41-44ce-9fad-148d2e85cf23"
        first = project_estima_record(first_id, episode, investigation, retained, app)
        second = project_estima_record(second_id, episode, investigation, retained, app)
        self.assertEqual(first["instance_id"], first_id)
        self.assertEqual(second["instance_id"], second_id)
        self.assertNotEqual(first["instance_id"], second["instance_id"])
        self.assertNotEqual(first["episode_id"], second["episode_id"])
        self.assertEqual(first["observations"], second["observations"])
        unsafe = project_estima_record("cluster/a password=hidden", episode, investigation, retained, app)
        self.assertTrue(unsafe["instance_id"].startswith("fcapsule-"))
        self.assertNotIn("hidden", json.dumps(unsafe))

    def test_normalized_fingerprint_ignores_pod_name_but_tracks_mechanism(self):
        episode_a, investigation_a, retained_a, app = retained_fixture("queue-1", "scrape_health")
        episode_b, investigation_b, retained_b, _ = retained_fixture("queue-99", "scrape_health")
        episode_c, investigation_c, retained_c, _ = retained_fixture("queue-2", "monitoring_selection")
        a = project_estima_record("instance-a", episode_a, investigation_a, retained_a, app)
        b = project_estima_record("instance-a", episode_b, investigation_b, retained_b, app)
        c = project_estima_record("instance-a", episode_c, investigation_c, retained_c, app)
        self.assertEqual(a["fingerprint"], b["fingerprint"])
        self.assertNotEqual(a["episode_id"], b["episode_id"])
        self.assertNotEqual(a["fingerprint"], c["fingerprint"])

    def test_naive_timestamps_are_not_serialized_as_invalid_values(self):
        episode, investigation, retained, app = retained_fixture()
        episode["last_activity_at"] = "2026-09-26T03:00:00"
        self.assertIsNone(project_estima_record("instance-a", episode, investigation, retained, app))

    def test_model_assessment_never_becomes_a_fact_or_fingerprint_input(self):
        episode, investigation, retained, app = retained_fixture()
        baseline = project_estima_record("instance-a", episode, investigation, retained, app)
        investigation["findings"].append({
            "state": "likely_explanation", "category": "investigator_assessment",
            "evidence_ids": ["Q001"],
        })
        projected = project_estima_record("instance-a", episode, investigation, retained, app)
        self.assertEqual(projected["fingerprint"], baseline["fingerprint"])
        self.assertFalse(any(item["value"] == "investigator_assessment"
                             for item in projected["observations"]))
        self.assertTrue(projected["hypotheses"])


class AtlasClientTests(unittest.TestCase):
    def test_env_constructor_is_opt_in_and_urls_are_restricted(self):
        self.assertIsNone(estima_client_from_env(environ={"FCAPSULE_ATLAS_URL": "https://atlas.example"}))
        client = estima_client_from_env("read", {"FCAPSULE_ATLAS_URL": "https://atlas.example",
                                                 "FCAPSULE_ATLAS_READ": "true"})
        self.assertIsInstance(client, EstimaClient)
        self.assertEqual(validate_estima_url("http://atlas.namespace.svc.cluster.local"),
                         "http://atlas.namespace.svc.cluster.local")
        with self.assertRaises(ValueError):
            validate_estima_url("http://atlas.example")
        with self.assertRaises(ValueError):
            validate_estima_url("https://user:pass@atlas.example")

    def test_estima_environment_names_take_precedence_with_atlas_fallback(self):
        settings = estima_settings_from_env({
            "FCAPSULE_ESTIMA_URL": "https://estima.example",
            "FCAPSULE_ESTIMA_TOKEN": "estima-service-token",
            "FCAPSULE_ESTIMA_READ": "false",
            "FCAPSULE_ESTIMA_PUBLISH": "true",
            "FCAPSULE_ATLAS_URL": "https://old-atlas.example",
            "FCAPSULE_ATLAS_TOKEN": "old-token",
            "FCAPSULE_ATLAS_READ": "true",
        })
        self.assertEqual(settings["url"], "https://estima.example")
        self.assertEqual(settings["token"], "estima-service-token")
        self.assertFalse(settings["read_enabled"])
        self.assertTrue(settings["publish_enabled"])
        legacy = estima_settings_from_env({"FCAPSULE_ATLAS_URL": "https://old-atlas.example",
                                           "FCAPSULE_ATLAS_READ": "true"})
        self.assertEqual(legacy["url"], "https://old-atlas.example")
        self.assertTrue(legacy["read_enabled"])

    def test_estima_connection_stays_disabled_without_read_or_publish_opt_in(self):
        self.assertIsNone(estima_client_from_env(environ={"FCAPSULE_ESTIMA_URL": "https://estima.example"}))

    def test_search_maps_before_to_observed_before_and_sends_bearer(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"cases":[]}'
        with patch("fcapsule.estima_client.urlopen", return_value=response) as open_url:
            client = EstimaClient("https://atlas.example", "test-token")
            self.assertEqual(client.search({"cluster": "c"}, "queue", 5, STAMP), {"cases": []})
        request = open_url.call_args.args[0]
        self.assertEqual(json.loads(request.data), {"limit": 5, "scope": {"cluster": "c"},
                                                   "query": "queue", "observed_before": STAMP})
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")

    def test_error_exposes_status_without_body(self):
        from urllib.error import HTTPError
        error = HTTPError("https://atlas.example", 422, "bad", {}, None)
        with patch("fcapsule.estima_client.urlopen", side_effect=error):
            with self.assertRaises(EstimaClientError) as raised:
                EstimaClient("https://atlas.example").create_case({})
        self.assertEqual(raised.exception.status_code, 422)
        self.assertNotIn("bad", str(raised.exception))

    def test_search_limit_matches_atlas_contract(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"cases":[]}'
        with patch("fcapsule.estima_client.urlopen", return_value=response) as open_url:
            EstimaClient("https://atlas.example").search(None, "timeout", 50)
        self.assertEqual(json.loads(open_url.call_args.args[0].data)["limit"], 10)


class AtlasOutboxTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.plane = ControlPlane(Path(self.directory.name) / "state")
        self.addCleanup(lambda: self.plane.atlas_publisher.shutdown(drain=False))

    def add_retained_episode(self):
        store = self.plane.store
        app = store.upsert_application("queue-service", "queue-worker", "jobs", "cluster-1", "prod")
        incident_id = "incident-local-id"
        store.record_incident({"incident_id": incident_id, "app_id": app["app_id"], "scenario": "queue",
                               "status": "resolved", "severity": "warning", "started_at": STAMP,
                               "ended_at": STAMP, "case_dir": str(Path(self.directory.name) / "expired-case"),
                               "summary": "Queue alert", "alert_count": 1, "log_count": 0,
                               "metric_series_count": 0, "raw_bytes": 0})
        episode = store.episode_for_incident(incident_id)
        output = Path(self.directory.name) / "state" / "capsules" / incident_id
        output.mkdir(parents=True)
        capsule = retained_fixture()[2][0]["capsule"]
        (output / "capsule.json").write_text(json.dumps(capsule), encoding="utf-8")
        report = {"pm_signals": [{"metric": "queue_depth", "peak_value": 27, "unit": "messages",
                                  "alert_timestamp": STAMP, "evidence_id": "ev_metric_001"}]}
        (output / "incident_report.json").write_text(json.dumps(report), encoding="utf-8")
        store.record_capsule({"capsule_id": f"capsule-{incident_id}", "incident_id": incident_id,
                              "app_id": app["app_id"], "output_dir": output, "selected_evidence": 4,
                              "compression": 0, "signal_preservation": 1, "grounding": 1,
                              "runtime_seconds": 1})
        state_path = self.plane.investigator.path(episode["episode_id"])
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"episode_id": episode["episode_id"], "revision_id": "investigation-revision-local",
                                          "status": "ready",
                                          "finished_at": STAMP, "assessment": {"likely_mechanism": "The queue may be saturated.",
                                          "evidence_ids": ["Q001"]},
                                          "findings": [{"category": "queue_saturation", "evidence_ids": ["Q001"]}]}),
                              encoding="utf-8")
        return episode

    def test_instance_ids_are_unique_and_stable_per_state_directory(self):
        one = self.plane.atlas_configuration()["instance_id"]
        self.assertEqual(one, self.plane.atlas_configuration()["instance_id"])
        other = ControlPlane(Path(self.directory.name) / "other")
        self.addCleanup(lambda: other.atlas_publisher.shutdown(drain=False))
        self.assertNotEqual(one, other.atlas_configuration()["instance_id"])

    def test_legacy_atlas_settings_are_copied_to_estima_path_without_removing_source(self):
        state_dir = Path(self.directory.name) / "migration-state"
        state_dir.mkdir()
        legacy = state_dir / "atlas-settings.json"
        legacy.write_text(json.dumps({
            "url": "https://shared-memory.example",
            "token": "service-token",
            "instance_id": "fcapsule-existing",
            "read_enabled": True,
            "publish_enabled": True,
            "instance_id_auto": False,
        }), encoding="utf-8")
        plane = ControlPlane(state_dir)
        self.addCleanup(lambda: plane.atlas_publisher.shutdown(drain=False))
        config = plane.atlas_configuration()
        migrated = state_dir / "estima-settings.json"
        self.assertEqual(config["url"], "https://shared-memory.example")
        self.assertEqual(config["instance_id"], "fcapsule-existing")
        self.assertTrue(config["read_enabled"])
        self.assertTrue(config["publish_enabled"])
        self.assertTrue(config["token_configured"])
        self.assertTrue(migrated.is_file())
        self.assertTrue(legacy.is_file())
        self.assertEqual(json.loads(migrated.read_text(encoding="utf-8"))["token"], "service-token")

    def test_revision_survives_pruned_sent_outbox_row(self):
        episode, investigation, retained, app = retained_fixture()
        payload = project_estima_record("instance-a", episode, investigation, retained, app)
        first = self.plane.store.enqueue_atlas_publication(payload)
        self.assertEqual(first["revision"], 1)
        self.plane.store.complete_atlas_publication(first["outbox_id"])
        with self.plane.store._connect() as connection:
            connection.execute("DELETE FROM atlas_outbox WHERE outbox_id = ?", (first["outbox_id"],))
        self.assertFalse(self.plane.store.enqueue_atlas_publication(payload)["is_new"])
        changed = {**payload, "summary": payload["summary"] + " Updated observation."}
        second = self.plane.store.enqueue_atlas_publication(changed)
        self.assertEqual(second["revision"], 2)
        self.assertTrue(second["is_new"])

    def test_identical_publication_links_local_revisions_without_creating_remote_revision(self):
        episode, investigation, retained, app = retained_fixture()
        payload = project_estima_record("instance-a", episode, investigation, retained, app)
        first = self.plane.store.enqueue_atlas_publication(
            payload, local_episode_id=episode["episode_id"], investigation_revision_id="investigation-rev-1",
        )
        duplicate = self.plane.store.enqueue_atlas_publication(
            payload, local_episode_id=episode["episode_id"], investigation_revision_id="investigation-rev-2",
        )
        self.assertEqual(first["revision"], duplicate["revision"])
        self.assertFalse(duplicate["is_new"])
        provenance = self.plane.store.publication_provenance(episode["episode_id"])
        self.assertEqual(provenance[0]["status"], "queued")
        self.assertEqual(provenance[0]["local_episode_link_status"], "linked")
        self.assertEqual(provenance[0]["receipt_status"], "not_yet_confirmed")
        self.assertEqual(provenance[0]["investigation_revision_ids"], [
            "investigation-rev-1", "investigation-rev-2",
        ])
        self.assertNotIn("payload", provenance[0])

    def test_publication_revision_links_are_bounded_and_mark_truncation(self):
        episode, investigation, retained, app = retained_fixture()
        payload = project_estima_record("instance-a", episode, investigation, retained, app)
        for revision in range(40):
            self.plane.store.enqueue_atlas_publication(
                payload, local_episode_id=episode["episode_id"],
                investigation_revision_id=f"investigation-rev-{revision}",
            )
        provenance = self.plane.store.publication_provenance(episode["episode_id"])[0]
        self.assertEqual(len(provenance["investigation_revision_ids"]), 32)
        self.assertEqual(provenance["investigation_revision_ids"][0], "investigation-rev-8")
        self.assertEqual(provenance["investigation_revision_ids"][-1], "investigation-rev-39")
        self.assertTrue(provenance["investigation_revision_links_truncated"])

    def test_successful_publish_without_case_id_is_not_presented_as_a_remote_receipt(self):
        with patch.object(self.plane.atlas_publisher, "start"):
            self.plane.update_atlas_configuration({
                "url": "https://collective.example", "token": "supersecret",
                "instance_id": "producer-a", "publish_enabled": True,
            })
        episode = self.add_retained_episode()
        client = CapturingAtlas(response={"case_id": "supersecret"})
        result = self.plane.process_estima_outbox_once(client=client)
        self.assertEqual(result["sent"], 1)
        self.assertNotEqual(client.calls[0]["episode_id"], episode["episode_id"])
        self.assertNotIn(episode["episode_id"], json.dumps(client.calls[0]))
        self.assertNotIn("local_episode_id", client.calls[0])
        self.assertNotIn("investigation_revision_id", client.calls[0])
        provenance = self.plane.store.publication_provenance(episode["episode_id"])
        self.assertEqual(provenance[0]["status"], "published")
        self.assertEqual(provenance[0]["receipt_status"], "remote_id_not_returned")
        self.assertIsNone(provenance[0]["remote_case_id"])
        self.assertNotIn("supersecret", json.dumps(provenance))
        report = self.plane.incident_report_payload("incident-local-id")
        self.assertEqual(report["publication_provenance"], provenance)

    def test_retry_receipt_survives_store_reopen_and_idempotent_redelivery(self):
        with patch.object(self.plane.atlas_publisher, "start"):
            self.plane.update_atlas_configuration({
                "url": "https://collective.example", "instance_id": "producer-a", "publish_enabled": True,
            })
        episode = self.add_retained_episode()
        unavailable = CapturingAtlas(EstimaClientError(
            "Estima request unavailable (TimeoutError) Authorization: Bearer supersecret",
        ))
        failed = self.plane.process_estima_outbox_once(client=unavailable)
        self.assertEqual(failed["retried"], 1)
        reopened = type(self.plane.store)(self.plane.store.path)
        retry = reopened.publication_provenance(episode["episode_id"])[0]
        self.assertEqual(retry["status"], "retry_scheduled")
        self.assertEqual(retry["attempts"], 1)
        self.assertEqual(retry["receipt_status"], "not_yet_confirmed")
        self.assertEqual(retry["last_error"], "Collective is unavailable; delivery will retry.")
        self.assertNotIn("supersecret", json.dumps(retry))
        report = self.plane.incident_report_payload("incident-local-id")
        self.assertEqual(report["investigation"]["status"], "ready")
        self.assertEqual(report["publication_provenance"][0]["status"], "retry_scheduled")
        with reopened._connect() as connection:
            row = connection.execute(
                "SELECT outbox_id FROM atlas_outbox WHERE local_episode_id = ?",
                (episode["episode_id"],),
            ).fetchone()
        with reopened._connect() as connection:
            connection.execute(
                "UPDATE atlas_outbox SET next_attempt_at = '2000-01-01T00:00:00Z' WHERE outbox_id = ?",
                (row["outbox_id"],),
            )
        delivered = CapturingAtlas(response={"case": {"case_id": "collective-case-9"}})
        result = self.plane.process_estima_outbox_once(client=delivered)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(delivered.calls[0]["revision"], unavailable.calls[0]["revision"])
        self.assertEqual(delivered.calls[0], unavailable.calls[0])
        self.assertNotIn(episode["episode_id"], json.dumps(delivered.calls[0]))
        receipt = reopened.publication_provenance(episode["episode_id"])[0]
        self.assertEqual(receipt["status"], "published")
        self.assertEqual(receipt["receipt_status"], "remote_id_recorded")
        self.assertEqual(receipt["remote_case_id"], "collective-case-9")

    def test_pre_ledger_upgrade_uses_global_outbox_high_water(self):
        episode, investigation, retained, app = retained_fixture()
        payload = project_estima_record("instance-a", episode, investigation, retained, app)
        first = self.plane.store.enqueue_atlas_publication(payload)
        with self.plane.store._connect() as connection:
            connection.execute("DELETE FROM atlas_outbox WHERE outbox_id = ?", (first["outbox_id"],))
            connection.execute("DELETE FROM atlas_publication_heads")
        changed = {**payload, "summary": payload["summary"] + " New fact."}
        self.assertGreater(self.plane.store.enqueue_atlas_publication(changed)["revision"], first["revision"])

    def test_legacy_outbox_migration_keeps_rows_with_explicit_unknown_local_link(self):
        state_dir = Path(self.directory.name) / "legacy-outbox"
        state_dir.mkdir()
        database_path = state_dir / "fcapsule.db"
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """
                CREATE TABLE atlas_outbox (
                    outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instance_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(instance_id, episode_id, payload_digest)
                )
                """
            )
            connection.execute(
                """INSERT INTO atlas_outbox
                   (instance_id, episode_id, revision, fingerprint, payload_digest, payload,
                    status, attempts, next_attempt_at, last_error, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("instance-old", "ep-hashed-old", 3, "fp-old", "digest-old",
                 '{"instance_id":"instance-old","episode_id":"ep-hashed-old","revision":3}',
                 "sent", 0, STAMP, None, STAMP, STAMP),
            )
        connection.close()

        store = FCAPSuleStore(database_path)
        with store._connect() as connection:
            row = connection.execute("SELECT * FROM atlas_outbox WHERE outbox_id = 1").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["local_episode_id"], "")
        self.assertEqual(row["local_episode_link_status"], "unknown")
        self.assertEqual(row["investigation_revision_ids"], "[]")
        self.assertEqual(row["investigation_revision_links_truncated"], 0)
        self.assertEqual(row["receipt_status"], "legacy_receipt_unknown")
        self.assertIsNone(row["remote_case_id"])
        self.assertEqual(store.publication_provenance("episode-local-id"), [])

    def test_runtime_settings_mask_token_and_manual_drain_publishes_queued_projection(self):
        with patch.object(self.plane.atlas_publisher, "start"):
            config = self.plane.update_atlas_configuration({"url": "https://atlas.example", "token": "secret-token",
                                                             "publish_enabled": True})
        self.assertTrue(config["publish_enabled"])
        self.assertTrue(config["token_configured"])
        self.assertNotIn("secret-token", json.dumps(config))
        self.add_retained_episode()
        client = CapturingAtlas()
        result = self.plane.process_atlas_outbox_once(client=client)
        self.assertEqual(result["queued"], 1)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["revision"], 1)
        self.assertEqual(self.plane.atlas_configuration()["pending_count"], 0)

    def test_401_is_quarantined_then_retried_after_credential_change(self):
        with patch.object(self.plane.atlas_publisher, "start"):
            self.plane.update_atlas_configuration({"url": "https://atlas.example", "token": "old-token",
                                                   "publish_enabled": True})
        payload = project_estima_record("one", *retained_fixture()[:3], retained_fixture()[3])
        self.plane.store.enqueue_atlas_publication(payload)
        result = self.plane.process_estima_outbox_once(client=CapturingAtlas(EstimaClientError("Estima returned HTTP 401", 401)))
        self.assertEqual(result["failed"], 1)
        self.assertEqual(self.plane.atlas_configuration()["failed_count"], 1)
        with patch.object(self.plane.atlas_publisher, "start"):
            self.plane.update_atlas_configuration({"token": "new-token"})
        self.assertEqual(self.plane.atlas_configuration()["failed_count"], 0)
        client = CapturingAtlas()
        result = self.plane.process_atlas_outbox_once(client=client)
        self.assertEqual(result["sent"], 1)

    def test_legacy_atlas_auth_failures_remain_retryable_after_upgrade(self):
        payload = project_estima_record("one", *retained_fixture()[:3], retained_fixture()[3])
        row = self.plane.store.enqueue_atlas_publication(payload)
        self.plane.store.defer_atlas_publication(row["outbox_id"], "Atlas returned HTTP 401", 2, permanent=True)
        self.assertEqual(self.plane.store.retry_failed_atlas_publications(auth_only=True), 1)
        self.assertEqual(self.plane.atlas_configuration()["failed_count"], 0)

    def test_503_is_retried_with_attempt_count_and_future_retry_time(self):
        payload = project_estima_record("one", *retained_fixture()[:3], retained_fixture()[3])
        self.plane.store.enqueue_atlas_publication(payload)
        result = self.plane.process_estima_outbox_once(client=CapturingAtlas(EstimaClientError("Estima returned HTTP 503", 503)))
        self.assertEqual(result["retried"], 1)
        row = self.plane.store.due_atlas_publications()[0] if self.plane.store.due_atlas_publications() else None
        self.assertIsNone(row)
        self.assertEqual(self.plane.atlas_configuration()["pending_count"], 1)


if __name__ == "__main__":
    unittest.main()
