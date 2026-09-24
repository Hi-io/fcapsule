import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

import yaml

from fcapsule.adapters.prometheus_adapter import PrometheusAdapter
from fcapsule.control_plane import ControlPlane
from fcapsule.investigation_tools import InvestigationTools, episode_context
from fcapsule.reasoning.context_budget import compact_for_model


class RetainedClient:
    def __init__(self):
        self.prompts = []

    def chat(self, request):
        prompt = json.loads(request.messages[1]["content"])
        self.prompts.append(prompt)
        return {"content": json.dumps({
            "sufficiency": "partially_sufficient",
            "answer": "The retained observations allow a comparison, not proof of the same cause.",
            "missing_discriminator": "No retained causal discriminator establishes a common mechanism.",
            "supporting_evidence_ids": prompt["available_evidence_ids"][:2],
        }), "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}}


class HistoricalCapsuleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.env = patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        guard = patch("fcapsule.reasoning.llm_client.DeepSeekChatClient.chat",
                      side_effect=AssertionError("No real provider calls in historical tests"))
        guard.start()
        self.addCleanup(guard.stop)
        self.plane = ControlPlane(self.root / "state")
        self.addCleanup(self.plane.evidence.shutdown)
        self.addCleanup(lambda: self.plane.briefing_executor.shutdown(wait=True, cancel_futures=True))
        self.prior = self.capture("12345678-abcd-abcd-abcd-123456789012", 0, 42)
        self.current = self.capture("current-incident", 1, 7)

    def capture(self, incident_id, day, value, resource="processor"):
        start = datetime(2026, 9, 20, tzinfo=timezone.utc) + timedelta(days=day)
        end = start + timedelta(minutes=2)
        stamp = lambda value: value.isoformat().replace("+00:00", "Z")
        metadata = {
            "case_id": incident_id, "case_title": "Queue signal", "service": resource,
            "namespace": "tasks", "cluster": "test", "pod": resource + "-pod",
            "window": {"start": stamp(start), "end": stamp(end)},
        }
        alert = {
            "alertname": "QueueHigh", "status": "resolved", "severity": "warning",
            "startsAt": stamp(start), "endsAt": stamp(end),
            "labels": {"namespace": "tasks", "service": resource, "pod": resource + "-pod"},
            "rule": {"name": "QueueHigh", "query": "queue_depth > 5", "duration": 30},
        }
        adapter = PrometheusAdapter("http://unused.invalid")
        adapter.transport = Mock()
        adapter.transport.request.return_value = {"status": "success", "data": {
            "resultType": "matrix", "result": [{"metric": alert["labels"],
            "values": [[start.timestamp(), str(value)], [(start + timedelta(seconds=15)).timestamp(), str(value)]]}],
        }}
        captured = adapter.collect_alert_metrics(alert, "tasks", resource + "-pod", start, end)
        alert["metric_evidence"] = captured["alert_evidence"]
        case = self.root / incident_id
        case.mkdir()
        (case / "metadata.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
        for name, payload in {
            "alert.json": alert,
            "prometheus_metrics.json": {"series": captured["series"]},
            "opensearch_logs.json": {"hits": [{"@timestamp": stamp(start), "level": "ERROR",
                                                "message": f"Queue rejected batch; observed depth {value}"}]},
            "kubernetes_config.json": {"items": [{"kind": "ConfigMap", "name": "queue-policy",
                "namespace": "tasks", "data": {"queue_limit": str(value + 1)}}]},
        }.items():
            (case / name).write_text(json.dumps(payload), encoding="utf-8")
        incident = self.plane.ingest_case(case, "processor", source_kind="live")
        capsule = self.plane._build_capsule(incident_id)
        episode = self.plane.store.episode_for_incident(incident_id)
        return {"incident": incident, "capsule": capsule, "episode": episode, "case": case}

    def current_episode(self):
        return self.plane.store.get_episode(self.current["episode"]["episode_id"])

    def kit(self):
        episode = self.current_episode()
        return InvestigationTools(self.plane.investigator.entries(episode),
                                  self.plane.store.get_application(episode["app_id"]), self.plane.live_sources,
                                  self.plane.investigator.historical_candidates(episode))

    def expire_raw_sources(self):
        for capture in (self.prior, self.current):
            shutil.rmtree(capture["case"])
        guard = patch.object(self.plane.live_sources, "configuration",
                             side_effect=AssertionError("Live configuration must not be opened"))
        guard.start()
        self.addCleanup(guard.stop)
        guard = patch.object(self.plane.live_sources, "adapters",
                             side_effect=AssertionError("Live adapters must not be opened"))
        guard.start()
        self.addCleanup(guard.stop)

    def test_recurrence_reads_actual_retained_measurements_after_raw_deletion(self):
        self.expire_raw_sources()
        episode = self.current_episode()
        self.assertEqual(episode["recurrence"]["previous_count"], 1)
        prior_id = self.prior["episode"]["episode_id"]
        result = self.kit().execute("historical_episode", {"episode_id": prior_id})
        metric = next(item for item in result["observations"] if item.get("metric_observation"))
        self.assertEqual(metric["metric_observation"]["condition"]["max"], 42)
        self.assertEqual(metric["metric_observation"]["threshold"], 5)
        self.assertEqual(metric["source"]["episode_id"], prior_id)
        self.assertEqual(metric["source"]["provenance"][0]["incident_id"], self.prior["incident"]["incident_id"])
        self.assertIn("queue_limit", json.dumps(result["observations"]))
        current_context = episode_context(episode, self.plane.investigator.entries(episode))
        current_metric = next(item for item in current_context["evidence"] if item.get("metric_observation"))
        self.assertEqual(current_metric["metric_observation"]["condition"]["max"], 7)
        self.assertEqual(result["availability"], "retained")
        self.assertIn("not proof of the same cause", result["limitation"])

    def test_historical_observations_survive_as_an_older_bounded_check(self):
        result = self.kit().execute("historical_episode", {"episode_id": self.prior["episode"]["episode_id"]})
        context = {"episode_id": self.current_episode()["episode_id"], "evidence": [], "alerts": []}
        compact, visible = compact_for_model(context, [
            {"id": "Q001", "tool": "historical_episode", "status": "completed", "result": result},
            {"id": "Q002", "tool": "resource_history", "status": "completed", "result": {}},
        ], max_prompt_tokens=5200)
        historical = compact["prior_checks"][0]["observation"]
        self.assertIn("Q001", visible)
        self.assertIn("observations", historical)
        metric = next(item for item in historical["observations"] if item.get("metric_observation"))
        self.assertEqual(metric["metric_observation"]["metric"], "queue_depth")
        self.assertIn('"max": 42', metric["metric_observation"]["condition"])
        self.assertEqual(metric["source"]["episode_id"], self.prior["episode"]["episode_id"])
        self.assertIn(self.prior["incident"]["incident_id"], json.dumps(metric["source"]["provenance"]))

    def test_unavailable_prior_capsule_is_not_a_historical_fact(self):
        root = Path(self.prior["capsule"]["output_dir"])
        for contents in (None, "{invalid json", "null"):
            with self.subTest(contents=contents):
                report = root / "incident_report.json"
                if contents is None:
                    report.unlink()
                else:
                    report.write_text(contents, encoding="utf-8")
                result = self.kit().execute("historical_episode", {"episode_id": self.prior["episode"]["episode_id"]})
                self.assertEqual(result["availability"], "unavailable")
                self.assertEqual(result["observations"], [])
                self.assertIn("missing captures cannot establish", result["limitation"])

    def test_retained_review_resolves_history_without_any_prior_tool_run(self):
        self.expire_raw_sources()
        context, checks = self.plane.investigator._retained_review_context(self.current_episode()["episode_id"])
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["tool"], "historical_episode")
        result = checks[0]["result"]
        self.assertIn('"max": 42', json.dumps(result["observations"]))
        self.assertNotIn('"prior_hypothesis":', json.dumps(result))
        self.assertEqual(context["recurrence"]["previous_count"], 1)

    def test_missing_prior_assessment_does_not_prevent_observation_retrieval(self):
        path = self.plane.investigator.path(self.prior["episode"]["episode_id"])
        path.write_text("invalid prior assessment", encoding="utf-8")
        result = self.kit().execute("historical_episode", {"episode_id": self.prior["episode"]["episode_id"]})
        self.assertEqual(result["availability"], "retained")
        self.assertEqual(result["episode"]["prior_hypothesis"], {})
        self.assertIn('"max": 42', json.dumps(result["observations"]))

    def test_prior_source_checks_remain_observations_not_recursive_assessments(self):
        self.plane._write_briefing_state(self.plane.investigator.path(self.prior["episode"]["episode_id"]), {
            "assessment": {"summary": "UnprovenEarlierConclusion"},
            "checks": [
                {"id": "Q001", "tool": "search_logs", "status": "completed",
                 "result": {"patterns": [{"pattern": "RetainedVariantSignature", "count": 2}]}},
                {"id": "Q002", "tool": "historical_episode", "status": "completed",
                 "result": {"episode": {"prior_hypothesis": "RecursiveConclusion"}}},
                {"id": "Q003", "tool": "search_logs", "status": "unavailable", "result": {}},
            ],
        })
        self.expire_raw_sources()
        _, checks = self.plane.investigator._retained_review_context(self.current_episode()["episode_id"])
        saved = checks[0]["result"]["observations"]
        prior_check = next(item for item in saved if item.get("retained_check"))
        self.assertEqual(prior_check["source"]["check_id"], "Q001")
        self.assertEqual(prior_check["source"]["episode_id"], self.prior["episode"]["episode_id"])
        self.assertIn("RetainedVariantSignature", json.dumps(prior_check))
        self.assertNotIn("UnprovenEarlierConclusion", json.dumps(checks))
        self.assertNotIn("RecursiveConclusion", json.dumps(checks))

    def test_recurrence_candidates_are_bounded_and_cross_pod_scope_is_explicit(self):
        other = self.capture("other-resource", 2, 99, resource="other-processor")
        related = self.plane.investigator.historical_candidates(other["episode"])
        self.assertEqual(len(related), 2)
        self.assertTrue(all(item["member_selection"]["candidate_relation"] ==
                            "same_workload_different_pod" for item in related))
        self.assertTrue(all(item["resource"]["name"] != "other-processor-pod" for item in related))
        for day in (3, 4, 5, 6):
            self.current = self.capture(f"capture-{day}", day, day + 10)
        candidates = self.plane.investigator.historical_candidates(self.current_episode())
        self.assertEqual(len(candidates), 3)
        self.assertEqual([item["episode_id"] for item in candidates],
                         [self.plane.store.episode_for_incident(f"capture-{day}")["episode_id"] for day in (5, 4, 3)])
        with self.assertRaisesRegex(ValueError, "outside the deterministic"):
            self.kit().execute("historical_episode", {"episode_id": other["episode"]["episode_id"]})

    def test_retained_review_freezes_inputs_and_exports_its_actual_evidence(self):
        self.expire_raw_sources()
        os.environ["DEEPSEEK_API_KEY"] = "test-key"
        episode_id = self.current_episode()["episode_id"]
        with patch.object(self.plane.briefing_executor, "submit") as submit:
            queued = self.plane.investigator.start_source_disconnected_review(episode_id, "Has the queue signal changed?")
            duplicate = self.plane.investigator.start_source_disconnected_review(episode_id, "Has the queue signal changed?")
            self.assertEqual(queued["review_id"], duplicate["review_id"])
            submit.assert_called_once()
        callback, *args = submit.call_args.args
        # A queued review must use the snapshot named by its fingerprint.
        (Path(self.prior["capsule"]["output_dir"]) / "incident_report.json").unlink()
        client = RetainedClient()
        with patch("fcapsule.reasoning.source_review.DeepSeekChatClient", return_value=client):
            callback(*args)
        reviews = self.plane.investigator.source_disconnected_reviews(episode_id)
        self.assertEqual(reviews[0]["status"], "ready")
        self.assertEqual(reviews[0]["model_context"], client.prompts[0]["retained_episode"])
        self.assertIn("42", json.dumps(client.prompts[0]["retained_episode"]["prior_checks"]))
        self.assertIn(self.prior["incident"]["incident_id"], json.dumps(client.prompts[0]["retained_episode"]["prior_checks"]))
        self.assertEqual(reviews[0]["retained_context"]["evidence"][0]["provenance"][0]["incident_id"],
                         self.current["incident"]["incident_id"])
        self.assertIn('"max": 42', json.dumps(reviews[0]["retained_checks"]))
        self.assertNotIn('"prior_hypothesis":', json.dumps(reviews[0]["retained_checks"]))
        self.plane._write_briefing_state(self.plane.investigator.path(episode_id), {
            "status": "ready", "checks": [{"id": "Q001", "tool": "search_logs", "status": "completed",
                                            "result": {"summary": "LaterUnrelatedCheck"}}],
        })
        payload = self.plane.incident_report_payload(self.current["incident"]["incident_id"])
        self.assertEqual(payload["source_disconnected_reviews"][0]["retained_checks"], reviews[0]["retained_checks"])
        self.assertNotIn("LaterUnrelatedCheck", json.dumps(payload["source_disconnected_reviews"][0]["retained_checks"]))
        with ZipFile(self.current["capsule"]["archive_path"]) as archive:
            history = json.loads(archive.read("investigation_revisions.json"))
            self.assertNotIn("prometheus_metrics.json", archive.namelist())
            self.assertNotIn("opensearch_logs.json", archive.namelist())
            self.assertEqual(history["source_disconnected_reviews"][0]["retained_context"], reviews[0]["retained_context"])
            self.assertIn('"max": 42', json.dumps(history["source_disconnected_reviews"][0]["retained_checks"]))
            self.assertIn("incident_report.json", archive.namelist())
        with patch.object(self.plane.briefing_executor, "submit"):
            refreshed = self.plane.investigator.start_source_disconnected_review(episode_id, "Has the queue signal changed?")
        self.assertNotEqual(queued["review_id"], refreshed["review_id"])

    def test_deleted_episode_does_not_regain_a_review_from_a_queued_snapshot(self):
        os.environ["DEEPSEEK_API_KEY"] = "test-key"
        episode_id = self.current_episode()["episode_id"]
        with patch.object(self.plane.briefing_executor, "submit") as submit:
            queued = self.plane.investigator.start_source_disconnected_review(episode_id, "What changed?")
        callback, *args = submit.call_args.args
        self.plane.delete_incident(self.current["incident"]["incident_id"])
        path = self.plane.investigator.source_review_path(episode_id, queued["review_id"])
        before = path.read_bytes()
        client = RetainedClient()
        with patch("fcapsule.reasoning.source_review.DeepSeekChatClient", return_value=client):
            callback(*args)
        self.assertEqual(client.prompts, [])
        self.assertEqual(self.plane.investigator.source_disconnected_reviews(episode_id), [])
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
