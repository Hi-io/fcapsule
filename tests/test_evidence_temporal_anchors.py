import copy
import json
import unittest
from types import SimpleNamespace

from fcapsule.evidence_service import EvidenceService
from fcapsule.episode_investigation import run_investigation
from fcapsule.investigation_tools import InvestigationTools
from fcapsule.reasoning.context_budget import (
    _check_item, _minimal_check_observation, _visual_observation, compact_for_model, estimate_tokens,
)


def image_evidence(facts):
    attachment = {"attachment_id": "image-one", "kind": "image", "status": "ready", "filename": "observation.png",
                  "observed_at": "2026-02-04T10:00:00Z", "created_at": "2026-02-04T10:30:00Z",
                  "extraction": {"observations": [{"fact": fact, "confidence": "medium"} for fact in facts],
                                 "limitation": "Only the visible display was extracted."}}
    store = SimpleNamespace(list_evidence_attachments=lambda _: [attachment])
    return EvidenceService.model_evidence(SimpleNamespace(plane=SimpleNamespace(store=store)), "episode")[0]


def context(facts):
    image = {**image_evidence(facts), "revision_addition": True}
    return {"episode_id": "episode", "live_capture": True,
            "scope": {"namespace": "production", "pod": "worker", "alert_started_at": "2026-02-04T10:12:00Z"},
            "alerts": [{"incident_id": "later", "alert_identity": "CapacityAlert", "started_at": "2026-02-04T10:12:00Z",
                        "ended_at": "2026-02-04T10:14:00Z", "severity": "critical"},
                       {"incident_id": "earlier", "alert_identity": "AvailabilityAlert", "started_at": "2026-02-04T10:00:00Z",
                        "ended_at": "2026-02-04T10:04:00Z", "severity": "warning"}],
            "evidence": [image] + [{"id": f"E{index}", "summary": "Other observation " * 40} for index in range(20)],
            "priority_evidence_ids": [image["id"]],
            "historical_candidates": [{"episode_id": "old-episode"}]}


FACTS = ["Endpoint is /status.", "Label is role=worker.", "Refresh was 7 seconds ago.",
         "State is unavailable.", "Duration is 12 milliseconds.", "Response reports access denied."]


class TemporalAnchorTests(unittest.TestCase):
    def test_all_short_visual_facts_survive_service_and_compaction_without_ranking(self):
        for facts in (FACTS, list(reversed(FACTS)), [fact.replace("unavailable", "available") for fact in FACTS]):
            original = context(facts)
            snapshot = copy.deepcopy(original)
            compact, visible = compact_for_model(original, [], max_prompt_tokens=850)
            observation = compact["evidence"][0]["visual_observation"]
            self.assertEqual([row["fact"] for row in observation["facts"]], facts)
            self.assertEqual(observation["omitted_facts"], 0)
            self.assertEqual(compact["evidence"][0]["time_range"], original["evidence"][0]["time_range"])
            self.assertEqual({row["incident_id"] for row in compact["alerts"]}, {"earlier", "later"})
            self.assertIn("A-image-one", visible)
            self.assertEqual(original, snapshot)
            self.assertLessEqual(estimate_tokens(compact), 850)

    def test_visual_budget_keeps_complete_facts_reports_omission_and_redacts(self):
        rows = [{"fact": "long " * 100, "confidence": "high"}] + [
            {"fact": fact, "confidence": "low"} for fact in FACTS]
        result = _visual_observation(rows, budget=500)
        self.assertEqual([row["fact"] for row in result["facts"]], FACTS)
        self.assertEqual(result["omitted_facts"], 1)
        redacted = _visual_observation([{"fact": "password=do-not-send at 10.1.2.3", "confidence": "high"}])
        self.assertNotIn("do-not-send", json.dumps(redacted))
        self.assertNotIn("10.1.2.3", json.dumps(redacted))
        capped = _visual_observation([{"fact": "ok"}] * 20, budget=2000)
        self.assertEqual(len(capped["facts"]), 16)
        self.assertEqual(capped["omitted_facts"], 4)

    def test_log_time_is_structured_even_when_message_has_no_timestamp(self):
        for message in ("Request rejected", '{"level":"ERROR","message":"Request rejected"}'):
            check = _check_item({"id": "Q1", "tool": "search_logs", "status": "completed", "result": {
                "patterns": [{"count": 2, "first_seen": "2026-02-04T10:11:00Z", "last_seen": "2026-02-04T10:13:00Z",
                              "examples": [{"timestamp": "2026-02-04T10:11:00Z", "message": message, "level": "ERROR"}]}]}}, False)
            minimal = _minimal_check_observation(check)
            self.assertEqual(minimal["top_signal"]["timestamp"], "2026-02-04T10:11:00Z")
            self.assertEqual(minimal["first_seen"], "2026-02-04T10:11:00Z")
            self.assertEqual(minimal["last_seen"], "2026-02-04T10:13:00Z")
            self.assertEqual(_minimal_check_observation({**check, "observation": minimal}), minimal)

    def test_empty_log_results_and_missing_timestamps_remain_unknown(self):
        for result in ({}, {"patterns": []}, {"patterns": [{"examples": [{"message": "Request rejected"}]}]}):
            check = _check_item({"tool": "search_logs", "result": result}, False)
            minimum = _minimal_check_observation(check)
            self.assertTrue(minimum["sampled"])
            self.assertIsNone(minimum.get("first_seen"))
            self.assertIsNone(minimum.get("top_signal", {}).get("timestamp"))

    def test_alert_bound_is_explicit_and_keeps_both_ends_of_input(self):
        source = context(FACTS)
        source["alerts"] = [{"incident_id": str(index), "alert_identity": f"Alert{index}",
                             "started_at": f"2026-02-{index + 1:02}T00:00:00Z"} for index in range(20)]
        compact, _ = compact_for_model(source, [], max_prompt_tokens=2000)
        self.assertEqual(len(compact["alerts"]), 12)
        self.assertEqual(compact["omitted_alerts"], 8)
        self.assertEqual(compact["alerts"][0]["incident_id"], "0")
        self.assertEqual(compact["alerts"][-1]["incident_id"], "19")

    def run_bounded_requests(self, source):
        checks = {"workload_state": {"observations": [{"kind": "PodSpec", "ready": True}]},
                  "search_logs": {"patterns": [{"count": 2, "first_seen": "2026-02-04T10:11:00Z",
                      "last_seen": "2026-02-04T10:13:00Z", "examples": [{"timestamp": "2026-02-04T10:11:00Z",
                      "level": "ERROR", "message": "Resource allocation rejected"}]}]},
                  "historical_episode": {"observations": [{"summary": "Earlier observation " * 40}] * 80}}
        kit = SimpleNamespace(CATALOG=InvestigationTools.CATALOG, pods=["worker"],
                              execute=lambda name, arguments: copy.deepcopy(checks[name]))
        requests = []

        def chat(request):
            requests.append(request)
            return {"content": json.dumps({"action": "finish", "assessment": {
                "summary": "Two alert intervals are retained for worker in production.",
                "likely_mechanism": "Unresolved: the observations concern different times.",
                "next_action": "Compare the retained observations within each interval.",
                "expected_finding": "Contemporaneous observations could discriminate mechanisms.",
                "uncertainty": "A shared cause has not been established.",
                "evidence_ids": ["A-image-one"], "connections": [],
                "hypotheses": [{"explanation": "Separate mechanisms", "status": "unresolved",
                                "reason": "The intervals alone establish no cause.", "evidence_ids": ["A-image-one"]}]}}),
                "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200}}

        state = run_investigation(source, kit, "fake", 1200, lambda _: None, max_checks=1,
                                  max_prompt_tokens=2100, max_total_tokens=12000, client=SimpleNamespace(chat=chat))
        self.assertEqual(state["status"], "ready", state.get("message"))
        self.assertEqual(len(requests), 2)
        return requests

    def test_full_draft_and_review_keep_visual_facts_and_both_intervals_at_2100(self):
        source = context(FACTS)
        for request in self.run_bounded_requests(source):
            self.assertLessEqual(sum(estimate_tokens(message["content"]) for message in request.messages), 2100)
            self.assertIn("Later-only observations cannot establish", request.messages[0]["content"])
            bounded = json.loads(request.messages[1]["content"])["episode"]
            self.assertEqual({row["incident_id"] for row in bounded["alerts"]}, {"earlier", "later"})
            for alert in bounded["alerts"]:
                original = next(row for row in source["alerts"] if row["incident_id"] == alert["incident_id"])
                self.assertEqual(alert["startsAt"], original["started_at"])
                self.assertEqual(alert["endsAt"], original["ended_at"])
            self.assertEqual([row["fact"] for row in bounded["evidence"][0]["visual_observation"]["facts"]], FACTS)

    def test_two_images_and_large_episode_keep_bounded_temporal_and_visual_anchors(self):
        source = context(FACTS)
        second = copy.deepcopy(source["evidence"][0])
        second.update({"id": "A-image-two", "time_range": {"observed_at": "2026-02-04T10:13:00Z"}})
        second["visual_observations"] = [{"fact": "Sampled gauge reads 28 units.", "confidence": "high"}]
        source["evidence"].insert(0, second)
        source["priority_evidence_ids"].insert(0, second["id"])
        source["alerts"] = [{"incident_id": f"incident-{index}-" + "identity" * 15,
                             "alert_identity": f"Alert{index}", "started_at": f"2026-02-{index + 1:02}T00:00:00Z",
                             "ended_at": f"2026-02-{index + 1:02}T01:00:00Z"} for index in range(12)]
        for request in self.run_bounded_requests(source):
            self.assertLessEqual(sum(estimate_tokens(message["content"]) for message in request.messages), 2100)
            bounded = json.loads(request.messages[1]["content"])["episode"]
            self.assertEqual(len(bounded["alerts"]), 2)
            self.assertEqual(bounded["omitted_alerts"], 10)
            self.assertEqual(bounded["alerts"][0]["incident_id"], source["alerts"][0]["incident_id"])
            self.assertEqual(bounded["alerts"][-1]["incident_id"], source["alerts"][-1]["incident_id"])
            images = {row["id"]: row for row in bounded["evidence"]}
            self.assertEqual([row["fact"] for row in images["A-image-one"]["visual_observation"]["facts"]], FACTS)
            self.assertEqual(images["A-image-two"]["visual_observation"]["facts"][0]["fact"], "Sampled gauge reads 28 units.")

    def test_omitted_extra_image_is_counted_and_not_citable(self):
        source = context(FACTS)
        for index in range(2):
            image = copy.deepcopy(source["evidence"][0])
            image["id"] = f"A-extra-{index}"
            source["evidence"].insert(0, image)
        bounded, visible = compact_for_model(source, [], max_prompt_tokens=750)
        self.assertEqual(bounded["omitted_images"], 1)
        self.assertNotIn("A-image-one", visible)


if __name__ == "__main__":
    unittest.main()
