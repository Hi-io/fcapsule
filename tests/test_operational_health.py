import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace

from fcapsule.operational_health import render_operational_metrics
from fcapsule.ui.app import FCAPSuleHTTPServer


class OperationalHealthTests(unittest.TestCase):
    def _plane(self, **overrides):
        values = {
            "source_state": {
                "configuration": {"enabled": True},
                "targets": {
                    "prometheus": {"ok": True, "error": "internal source url"},
                    "opensearch": {"ok": False, "error": "secret=source-key"},
                    "kubernetes": {"ok": True},
                    "tenant-secret": {"ok": True},
                },
                "last_sync_at": "2026-09-26T04:00:00Z",
                "error": "exception contained a private token=source-token",
            },
            "active_job": None,
            "phases": {"sources": {"status": "done"}},
            "pending_webhook_sync": False,
            "briefing_jobs": {"incident-1", "incident-2"},
            "_last_retention_check": 90.0,
            "estima_settings_path": None,
            "ai_configuration": lambda: {
                "provider": "openrouter",
                "model": "private-model-name",
                "api_key_configured": True,
                "capability": {"status": "ready", "message": "token=provider-secret"},
            },
            "general_configuration": lambda: {"incident_retention_days": 42},
        }
        values.update(overrides)
        values["store"] = overrides.get("store", SimpleNamespace(atlas_outbox_status=lambda: {
            "pending_count": 3,
            "failed_count": 1,
            "last_error": "token=outbox-secret",
            "counts": {"pending": 3, "failed": 1, "sent": 4},
        }))
        return SimpleNamespace(**values)

    def test_reports_bounded_operational_states_and_backlogs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "estima-settings.json"
            path.write_text(json.dumps({
                "publish_enabled": True,
                "token": "private-publication-token",
                "url": "https://private.example.test/service",
            }), encoding="utf-8")
            plane = self._plane(estima_settings_path=path)

            rendered = render_operational_metrics(plane, monotonic_now=120.0)

        self.assertIn('fcapsule_source_connection_state{source="prometheus",state="healthy"} 1', rendered)
        self.assertIn('fcapsule_source_connection_state{source="opensearch",state="unavailable"} 1', rendered)
        self.assertIn('fcapsule_source_sync_state{state="succeeded"} 1', rendered)
        self.assertIn("fcapsule_source_sync_last_attempt_timestamp_seconds 1790395200", rendered)
        self.assertIn('fcapsule_provider_selected{provider="openrouter"} 1', rendered)
        self.assertIn('fcapsule_provider_capability_state{provider="openrouter",state="ready"} 1', rendered)
        self.assertIn("fcapsule_provider_work_items 2", rendered)
        self.assertIn("fcapsule_shared_publication_enabled 1", rendered)
        self.assertIn('fcapsule_shared_publication_backlog{state="pending"} 3', rendered)
        self.assertIn('fcapsule_shared_publication_backlog{state="failed"} 1', rendered)
        self.assertIn('fcapsule_shared_publication_backlog{state="sent"} 4', rendered)
        self.assertIn("fcapsule_retention_policy_days 42", rendered)
        self.assertIn("fcapsule_retention_cleanup_seconds_since_check 30", rendered)
        self.assertIn("fcapsule_retention_cleanup_due 0", rendered)

        for secret in (
            "private-publication-token", "private.example.test", "provider-secret", "outbox-secret",
            "source-token", "source-key", "private-model-name", "tenant-secret", "internal source url",
        ):
            self.assertNotIn(secret, rendered)

    def test_disabled_sources_and_unvalidated_provider_are_not_healthy(self):
        plane = self._plane(
            source_state={"configuration": {"enabled": False}, "targets": {"prometheus": {"ok": True}}},
            ai_configuration=lambda: {
                "provider": "deepseek",
                "capability": {"status": "not_validated"},
            },
            briefing_jobs=set(),
        )

        rendered = render_operational_metrics(plane, monotonic_now=120.0)

        self.assertIn('fcapsule_source_connection_state{source="prometheus",state="disabled"} 1', rendered)
        self.assertIn('fcapsule_source_sync_state{state="disabled"} 1', rendered)
        self.assertIn('fcapsule_provider_capability_state{provider="deepseek",state="not_validated"} 1', rendered)
        self.assertIn('fcapsule_provider_capability_state{provider="openrouter",state="not_selected"} 1', rendered)
        self.assertIn("fcapsule_provider_work_items 0", rendered)
        self.assertIn("fcapsule_retention_cleanup_due 0", rendered)

    def test_missing_cleanup_check_is_reported_due_and_bad_counts_are_bounded(self):
        plane = self._plane(
            _last_retention_check=0,
            store=SimpleNamespace(atlas_outbox_status=lambda: {
                "counts": {"pending": "5", "failed": -1, "customer-episode-id": 91},
            }),
            general_configuration=lambda: {"incident_retention_days": 100000},
        )

        rendered = render_operational_metrics(plane, monotonic_now=999.0)

        self.assertIn('fcapsule_shared_publication_backlog{state="pending"} 5', rendered)
        self.assertIn('fcapsule_shared_publication_backlog{state="failed"} 0', rendered)
        self.assertIn('fcapsule_shared_publication_backlog{state="other"} 91', rendered)
        self.assertIn("fcapsule_retention_policy_days 3650", rendered)
        self.assertIn("fcapsule_retention_cleanup_seconds_since_check 0", rendered)
        self.assertIn("fcapsule_retention_cleanup_due 1", rendered)
        self.assertNotIn("customer-episode-id", rendered)

    def test_metrics_route_returns_prometheus_plaintext(self):
        plane = self._plane()
        plane.investigator = SimpleNamespace(stopping=False)
        plane.stop_live_monitoring = lambda: None
        plane.briefing_executor = SimpleNamespace(shutdown=lambda **_: None)
        plane.evidence = SimpleNamespace(shutdown=lambda **_: None)
        server = FCAPSuleHTTPServer(("127.0.0.1", 0), plane)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = HTTPConnection("127.0.0.1", server.server_port)
            connection.request("GET", "/metrics")
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertTrue(response.getheader("Content-Type").startswith("text/plain; version=0.0.4"))
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            self.assertIn("# TYPE fcapsule_source_sync_state gauge", body)
            self.assertIn("fcapsule_shared_publication_backlog", body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
