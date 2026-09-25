import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from fcapsule.investigation_service import InvestigationService, _atlas_case
from fcapsule.reasoning.context_budget import compact_for_model, estimate_tokens


class AtlasInvestigationTests(unittest.TestCase):
    def setUp(self):
        self.before = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
        self.context = {
            "episode_id": "episode-current",
            "scope": {"service": "inventory-api", "cluster": "cluster-current", "namespace": "shop",
                      "resource": {"kind": "Pod", "name": "inventory-api-7f9"},
                      "alert_started_at": "2026-09-20T12:00:00Z"},
            "alerts": [{"alert_identity": "PodCrashLooping", "incident_id": "incident-current"}],
            "evidence": [{"id": "E-current", "title": "Container restarted",
                           "summary": "Crash loop after decoder rejects payload"}],
        }
        self.case = {
            "id": "atlas-case-01",
            "instance_id": "atlas-instance-remote",
            "score": 0.91,
            "relation": "fingerprint",
            "observed_at": "2026-09-20T11:55:00Z",
            "scope": {"service": "stock-api", "cluster": "cluster-remote", "namespace": "production"},
            "summary": "Prior case with repeated decoder errors before a restart.",
            "observations": [{"kind": "log", "key": "error_template", "value": "base64 decoder rejected payload",
                              "source": "opensearch", "observed_at": "2026-09-20T11:50:00Z",
                              "reference": "atlas-fact-21"}],
            "hypotheses": [{"statement": "A malformed upstream payload may have triggered retries.",
                            "confidence": "medium", "supporting_refs": ["atlas-fact-21"]}],
            "fingerprint": "must-not-leak",
        }

    def _service(self, client):
        service = InvestigationService.__new__(InvestigationService)
        service.plane = Mock()
        service.plane.atlas_client.return_value = client
        return service

    def test_retrieval_is_cross_instance_time_bounded_and_provenance_preserving(self):
        future = {**self.case, "id": "atlas-case-future", "observed_at": "2026-09-20T12:00:01Z"}
        client = Mock()
        client.search.return_value = {"cases": [future, self.case]}

        cases, status = self._service(client)._atlas_retrieval(self.context)

        client.search.assert_called_once()
        scope, query = client.search.call_args.args
        self.assertEqual(scope, {})
        self.assertLessEqual(len(query), 500)
        self.assertTrue(set(scope).issubset({"environment", "cluster", "namespace", "service", "workload", "cnfc_id", "vnfc_id"}))
        self.assertNotIn("cluster", scope)
        self.assertNotIn("namespace", scope)
        self.assertIn("inventory-api", query)
        self.assertIn("PodCrashLooping", query)
        self.assertEqual(client.search.call_args.kwargs["before"], "2026-09-20T12:00:00Z")
        self.assertEqual(status["status"], "matched")
        self.assertEqual([item["atlas_case_id"] for item in cases], ["atlas-case-01"])
        retained = cases[0]
        self.assertEqual(retained["citation"], "Atlas case atlas-case-01")
        self.assertEqual(retained["instance_id"], "atlas-instance-remote")
        self.assertEqual(retained["observations"][0]["reference"], "atlas-fact-21")
        self.assertEqual(retained["factual_reference_ids"], ["atlas-fact-21"])
        self.assertEqual(retained["prior_hypotheses"][0]["statement"],
                         "A malformed upstream payload may have triggered retries.")
        self.assertNotIn("must-not-leak", json.dumps(retained))

    def test_search_request_fields_match_atlas_search_schema_limits(self):
        client = Mock()
        client.search.return_value = {"cases": []}
        self._service(client)._atlas_retrieval(self.context)

        args, kwargs = client.search.call_args
        scope, query = args
        limit = kwargs["limit"]
        self.assertEqual(scope, {})
        self.assertLessEqual(len(query), 500)
        self.assertGreaterEqual(limit, 1)
        self.assertLessEqual(limit, 10)
        self.assertIsNotNone(datetime.fromisoformat(kwargs["before"].replace("Z", "+00:00")).tzinfo)

    def test_past_case_has_hypotheses_separated_from_captured_observations(self):
        candidate = _atlas_case(self.case, self.before)

        self.assertEqual(candidate["observations"][0]["value"], "base64 decoder rejected payload")
        self.assertNotIn("hypotheses", candidate)
        self.assertEqual(candidate["prior_hypotheses"][0]["provenance"],
                         "Prior unverified model hypothesis; not an observed fact or root-cause finding.")
        self.assertIn("not evidence for this incident", candidate["limitation"])
        self.assertIsNone(_atlas_case({**self.case, "observed_at": "2026-09-20T11:55:00"}, self.before))

    def test_atlas_compaction_informs_planning_without_becoming_an_e_or_q_citation(self):
        cases, _ = self._service(Mock(search=Mock(return_value={"cases": [self.case]})))._atlas_retrieval(self.context)
        context = {**self.context, "atlas_cases": cases}

        compact, visible = compact_for_model(context, [], max_prompt_tokens=1400)

        self.assertIn("atlas_cases", compact)
        self.assertIn("atlas_case_policy", compact)
        self.assertEqual(compact["atlas_cases"][0]["atlas_case_id"], "atlas-case-01")
        self.assertEqual(compact["atlas_cases"][0]["observations"][0]["reference"], "atlas-fact-21")
        self.assertIn("not a captured observation or RCA", compact["atlas_cases"][0]["prior_hypotheses"][0]["provenance"])
        self.assertIn("E-current", visible)
        self.assertNotIn("atlas-case-01", visible)
        self.assertNotIn("atlas-fact-21", visible)
        self.assertNotIn("must-not-leak", json.dumps(compact))

    def test_current_counterevidence_keeps_prior_hypothesis_historical_and_unverified(self):
        cases, _ = self._service(Mock(search=Mock(return_value={"cases": [self.case]})))._atlas_retrieval(self.context)
        context = {**self.context, "evidence": [{
            "id": "E-current", "title": "Incident-time request trace",
            "summary": "The decoder accepted the request and the trace shows no retry before the alert.",
        }], "atlas_cases": cases}

        compact, visible = compact_for_model(context, [], max_prompt_tokens=1400)

        self.assertIn("decoder accepted the request", compact["evidence"][0]["summary"])
        self.assertIn("historical, unverified", compact["atlas_case_policy"])
        self.assertIn("Compare them against current observations", compact["atlas_case_policy"])
        self.assertIn("prior_hypotheses", compact["atlas_cases"][0])
        self.assertNotIn("A malformed upstream payload may have triggered retries.",
                         compact["evidence"][0]["summary"])
        self.assertEqual(visible, ["E-current"])

    def test_disabled_or_unavailable_atlas_does_not_stop_local_investigation(self):
        disabled = self._service(None)
        cases, status = disabled._atlas_retrieval(self.context)
        self.assertEqual(cases, [])
        self.assertEqual(status["status"], "disabled")

        client = Mock()
        client.search.side_effect = TimeoutError("Atlas timed out")
        cases, status = self._service(client)._atlas_retrieval(self.context)
        self.assertEqual(cases, [])
        self.assertEqual(status["status"], "unavailable")
        self.assertIn("continues", status["limitation"])

    def test_unknown_capture_time_skips_search_instead_of_leaking_future_cases(self):
        client = Mock()
        context = {**self.context, "scope": {"service": "inventory-api",
                                              "alert_started_at": "2026-09-20T12:00:00"}}

        cases, status = self._service(client)._atlas_retrieval(context)

        client.search.assert_not_called()
        self.assertEqual(cases, [])
        self.assertEqual(status["status"], "skipped_no_time")

    def test_publisher_notification_is_best_effort_and_nonblocking(self):
        service = self._service(None)
        service.plane.atlas_publisher = Mock()

        service._notify_atlas_publisher("episode-current")

        service.plane.atlas_publisher.notify_episode.assert_called_once_with("episode-current")
        service.plane.atlas_publisher.notify_episode.side_effect = RuntimeError("publisher stopped")
        service._notify_atlas_publisher("episode-current")
        self.assertEqual(service.plane.atlas_publisher.notify_episode.call_count, 2)

    def test_not_configured_terminal_state_still_wakes_fact_only_publisher(self):
        with tempfile.TemporaryDirectory() as directory:
            service = InvestigationService.__new__(InvestigationService)
            service.plane = Mock()
            service.jobs = set()
            service.pending_targets = {}
            service.source_review_jobs = set()
            service.stopping = False
            service.plane.state_dir = Path(directory)
            service.plane.briefing_lock = threading.RLock()
            service.plane.store.get_episode.return_value = {
                "episode_id": "episode-current", "signals": [{"incident_id": "incident-current"}],
                "primary_incident_id": "incident-current",
            }
            service.plane.evidence.manifest.return_value = []
            service.plane.ai_configuration.return_value = {
                "provider": "deepseek", "model": "test-model", "api_key_configured": False,
            }
            service.plane.atlas_publisher = Mock()
            service.entries = Mock(return_value=[{"incident": {"incident_id": "incident-current"}}])
            service.fingerprint = Mock(return_value="fingerprint")
            service._record_revision = Mock()

            def write_state(path, state):
                path = Path(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(state), encoding="utf-8")

            service.plane._write_briefing_state.side_effect = write_state
            service.plane.atlas_publisher.notify_episode.side_effect = lambda _episode_id: self.assertTrue(
                service.path("episode-current").is_file())

            state = service.start("episode-current")

            self.assertEqual(state["status"], "not_configured")
            service.plane.atlas_publisher.notify_episode.assert_called_once_with("episode-current")

    def test_compaction_stays_within_tight_budget_and_never_lists_atlas_ids_as_evidence(self):
        context = {**self.context, "atlas_cases": [
            {**_atlas_case({**self.case, "id": f"atlas-case-{index}"}, self.before),
             "summary": "Cross-instance observations " * 12}
            for index in range(3)
        ]}

        compact, visible = compact_for_model(context, [], max_prompt_tokens=520)

        self.assertLessEqual(estimate_tokens(compact), 520)
        self.assertTrue(all(not item.startswith("atlas-") for item in visible))
        self.assertNotIn("episode-current", visible)


if __name__ == "__main__":
    unittest.main()
