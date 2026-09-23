import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fcapsule.investigation_service import InvestigationService
from fcapsule.investigation_tools import historical_episode_result
from fcapsule.store import _recurrence_key


class HistoricalMemberSelectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.key = _recurrence_key("app", "pod", "processor", "QueueHigh")
        self.records = {}
        self.incidents = {}
        self.prior = {"episode_id": "prior", "app_id": "app", "title": "Mixed episode",
                      "started_at": "2026-09-20T00:00:00Z", "status": "resolved", "signals": []}
        self.current = {"episode_id": "current", "app_id": "app", "primary_incident_id": "current-member",
                        "recurrence_key": self.key, "signals": [{"incident_id": "current-member", "recurrence_key": self.key}],
                        "recurrence": {"candidates": [{"episode_id": "prior"}]}}
        store = Mock()
        store.get_episode.return_value = self.prior
        store.get_capsule_for_incident.side_effect = self.records.get
        store.get_incident.side_effect = self.incidents.get
        self.service = InvestigationService(SimpleNamespace(store=store, state_dir=self.root))

    def member(self, name, minute, key=None):
        signal = {"incident_id": name, "started_at": f"2026-09-20T00:{minute:02d}:00Z",
                  "status": "resolved", "recurrence_key": key or self.key}
        self.prior["signals"].append(signal)
        self.incidents[name] = signal
        folder = self.root / name
        folder.mkdir()
        self.records[name] = {"output_dir": str(folder)}
        (folder / "capsule.json").write_text(json.dumps({"case": {}}), encoding="utf-8")
        report = {"incident": {}, "fault_alerts": [{"name": "QueueHigh" if signal["recurrence_key"] == self.key else "OtherAlert"}],
                  "supporting_evidence": [{"evidence_id": name + "-evidence", "type": "log", "title": name,
                                           "summary": "Retained observation " + name}]}
        (folder / "incident_report.json").write_text(json.dumps(report), encoding="utf-8")
        return signal

    def mixed_members(self):
        self.member("matching-old", 0)
        for minute in range(1, 15):
            self.member(f"other-{minute}", minute, _recurrence_key("app", "pod", "processor", "OtherAlert"))
        self.prior["primary_incident_id"] = "other-14"

    def test_older_match_beyond_latest_twelve_precedes_recent_other_members(self):
        self.mixed_members()
        result = self.service.historical_candidates(self.current)[0]
        selection = result["member_selection"]
        self.assertEqual([item["incident_id"] for item in selection["selected_members"]],
                         ["matching-old", "other-14", "other-13", "other-12"])
        self.assertEqual(selection["omitted_member_count"], 11)
        self.assertEqual(selection["retained_matching_member_count"], 1)
        self.assertEqual(self.service.plane.store.get_capsule_for_incident.call_count, 4)
        self.assertEqual(len(result["captured_evidence"]), 4)
        tool = historical_episode_result(result)
        first = tool["observations"][0]
        self.assertEqual(first["source"]["provenance"][0]["incident_id"], "matching-old")
        self.assertTrue(first["source"]["matches_current_alert_identity"])
        self.assertFalse(tool["observations"][-1]["source"]["matches_current_alert_identity"])

    def test_multiple_matches_use_recency_and_keep_four_member_bound(self):
        for minute in range(6): self.member(f"match-{minute}", minute)
        result = self.service.historical_candidates(self.current)[0]
        self.assertEqual([item["incident_id"] for item in result["captured_evidence"]],
                         ["match-5", "match-4", "match-3", "match-2"])
        self.assertEqual(result["member_selection"]["matching_member_count"], 6)

    def test_missing_matching_capture_does_not_substitute_other_members_or_checks(self):
        self.mixed_members()
        for corruption in (None, "not JSON", "null"):
            with self.subTest(corruption=corruption):
                path = self.root / "matching-old" / "incident_report.json"
                if corruption is None: path.unlink()
                else: path.write_text(corruption, encoding="utf-8")
                with patch.object(self.service, "read", return_value={
                    "assessment": {"summary": "Unrelated prior diagnosis"},
                    "checks": [{"status": "completed", "tool": "search_logs", "result": {"summary": "Other member"}}],
                }):
                    result = self.service.historical_candidates(self.current)[0]
                self.assertEqual(result["availability"], "unavailable")
                for key in ("observations", "captured_evidence", "retained_checks"):
                    self.assertEqual(result[key], [])
                self.assertEqual(result["prior_hypothesis"], {})
                missing = result["member_selection"]["selected_members"][0]
                self.assertEqual(missing["incident_id"], "matching-old")
                self.assertTrue(missing["matches_current_alert_identity"])
                self.assertFalse(missing["capture_available"])

    def test_same_alert_at_another_resource_is_not_a_matching_member(self):
        self.member("matching-old", 0)
        self.member("other-resource", 1, _recurrence_key("app", "pod", "different", "QueueHigh"))
        result = self.service.historical_candidates(self.current)[0]
        self.assertEqual(result["member_selection"]["matching_member_count"], 1)
        self.assertFalse(result["member_selection"]["selected_members"][1]["matches_current_alert_identity"])
        self.prior["app_id"] = "different-app"
        self.assertEqual(self.service.historical_candidates(self.current), [])

    def test_revision_primary_identity_overrides_episode_primary_without_relabeling(self):
        self.mixed_members()
        other_key = _recurrence_key("app", "pod", "processor", "OtherAlert")
        self.current["signals"].append({"incident_id": "revision-member", "recurrence_key": other_key})
        result = self.service.historical_candidates(self.current, "revision-member")[0]
        self.assertEqual(result["member_selection"]["current_incident_id"], "revision-member")
        self.assertEqual(result["captured_evidence"][0]["incident_id"], "other-14")
        self.assertEqual(self.current["primary_incident_id"], "current-member")

    def test_unknown_identity_does_not_infer_a_match_from_prose(self):
        self.member("matching-old", 0)
        self.current["recurrence_key"] = ""
        self.current["signals"][0]["recurrence_key"] = ""
        self.current["title"] = "QueueHigh"
        result = self.service.historical_candidates(self.current)[0]
        self.assertEqual(result["availability"], "unavailable")
        self.assertFalse(result["member_selection"]["identity_available"])
        self.assertEqual(result["observations"], [])

    def test_retained_only_review_rehydrates_matching_member_without_prior_check(self):
        self.mixed_members()
        current_signal = self.member("current-member", 20)
        self.prior["signals"].remove(current_signal)
        self.current["signals"] = [current_signal]
        self.service.plane.store.get_episode.side_effect = lambda key: {"prior": self.prior, "current": self.current}.get(key)
        self.service.plane.evidence = SimpleNamespace(model_evidence=Mock(return_value=[]))
        _, checks = self.service._retained_review_context("current")
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["tool"], "historical_episode")
        first = checks[0]["result"]["observations"][0]
        self.assertEqual(first["source"]["provenance"][0]["incident_id"], "matching-old")
        self.assertTrue(first["source"]["matches_current_alert_identity"])


if __name__ == "__main__":
    unittest.main()
