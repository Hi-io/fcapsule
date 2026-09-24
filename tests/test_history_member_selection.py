import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fcapsule.investigation_service import InvestigationService
from fcapsule.investigation_service import MAX_PRIMARY_CAPSULE_BYTES
from fcapsule.investigation_tools import historical_episode_result
from fcapsule.store import FCAPSuleStore, _recurrence_key


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
                        "recurrence_key": self.key, "signals": [{"incident_id": "current-member",
                        "started_at": "2026-09-20T01:00:00Z", "recurrence_key": self.key}],
                        "recurrence": {"candidates": [{"episode_id": "prior"}]}}
        store = Mock()
        store.get_episode.return_value = self.prior
        store.recurrence_candidates_for_incident.return_value = [{"episode_id": "prior"}]
        store.get_capsule_for_incident.side_effect = self.records.get
        store.get_incident.side_effect = self.incidents.get
        self.service = InvestigationService(SimpleNamespace(store=store, state_dir=self.root))

    def member(self, name, minute, key=None, alert=None, started_at=None):
        signal = {"incident_id": name, "started_at": started_at or f"2026-09-20T00:{minute:02d}:00Z",
                  "status": "resolved", "recurrence_key": key or self.key}
        self.prior["signals"].append(signal)
        self.incidents[name] = signal
        folder = self.root / name
        folder.mkdir()
        self.records[name] = {"output_dir": str(folder)}
        (folder / "capsule.json").write_text(json.dumps({"case": {}}), encoding="utf-8")
        report = {"incident": {}, "fault_alerts": [{"name": alert or ("QueueHigh" if signal["recurrence_key"] == self.key else "OtherAlert")}],
                  "supporting_evidence": [{"evidence_id": name + "-evidence", "type": "log", "title": name,
                                           "summary": "Retained observation " + name}]}
        (folder / "incident_report.json").write_text(json.dumps(report), encoding="utf-8")
        return signal

    def test_episode_context_loads_only_the_primary_capsule(self):
        self.member("older-member", 0)
        self.member("current-member", 1)
        self.prior["primary_incident_id"] = "current-member"

        entries = self.service.entries(self.prior, primary_incident_id="current-member")

        self.assertEqual([item["incident"]["incident_id"] for item in entries],
                         ["older-member", "current-member"])
        self.assertEqual(entries[0]["capsule"], {})
        self.assertEqual(entries[0]["capsule_load_status"], "not_loaded")
        self.assertEqual(entries[1]["capsule"], {"case": {}})
        self.assertEqual(entries[1]["capsule_load_status"], "loaded")

    def test_oversized_primary_capsule_is_preserved_but_not_loaded(self):
        self.member("current-member", 0)
        capsule = self.root / "current-member" / "capsule.json"
        capsule.write_text('{"case":{},"padding":"' + ("x" * MAX_PRIMARY_CAPSULE_BYTES) + '"}',
                           encoding="utf-8")

        entries = self.service.entries(self.prior, primary_incident_id="current-member")

        self.assertEqual(entries[0]["capsule"], {})
        self.assertEqual(entries[0]["capsule_load_status"], "size_limit")

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
                         ["matching-old"])
        self.assertEqual(selection["omitted_member_count"], 14)
        self.assertEqual(selection["retained_matching_member_count"], 1)
        self.assertEqual(self.service.plane.store.get_capsule_for_incident.call_count, 1)
        self.assertEqual(len(result["captured_evidence"]), 1)
        tool = historical_episode_result(result)
        first = tool["observations"][0]
        self.assertEqual(first["source"]["provenance"][0]["incident_id"], "matching-old")
        self.assertTrue(first["source"]["matches_current_alert_identity"])

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
        self.assertEqual([item["incident_id"] for item in result["member_selection"]["selected_members"]],
                         ["matching-old"])
        self.prior["app_id"] = "different-app"
        self.assertEqual(self.service.historical_candidates(self.current), [])

    def test_cross_pod_member_is_tentative_and_preserves_exact_pod_provenance(self):
        current_key = _recurrence_key("app", "pod", "processor-current", "QueueHigh")
        prior_key = _recurrence_key("app", "pod", "processor-old", "QueueHigh")
        self.current["recurrence_key"] = current_key
        self.current["signals"][0].update({
            "app_id": "app", "source_kind": "live", "resource_kind": "pod",
            "resource_name": "processor-current", "recurrence_key": current_key,
        })
        prior_member = self.member("prior-other-pod", 0, prior_key, "QueueHigh")
        prior_member.update({
            "app_id": "app", "source_kind": "live", "resource_kind": "pod",
            "resource_name": "processor-old",
        })
        self.service.plane.store.recurrence_candidates_for_incident.return_value = [
            {"episode_id": "prior", "match_type": "same_workload_different_pod"},
        ]

        result = self.service.historical_candidates(self.current)[0]
        selection = result["member_selection"]
        selected = selection["selected_members"][0]
        observation = historical_episode_result(result)["observations"][0]

        self.assertEqual(selection["candidate_relation"], "same_workload_different_pod")
        self.assertEqual(selected["target_relation"], "same_workload_different_pod")
        self.assertTrue(selected["matches_current_alert_identity"])
        self.assertFalse(selected["matches_current_target"])
        self.assertEqual(observation["source"]["provenance"][0]["incident_id"], "prior-other-pod")
        self.assertEqual(observation["source"]["provenance"][0]["target_relation"], "same_workload_different_pod")
        self.assertFalse(observation["source"]["provenance"][0]["matches_current_target"])
        self.assertTrue(observation["source"]["matches_current_alert_identity"])
        self.assertIn("not evidence of the same target or cause", selection["limitation"])

    def test_future_only_matching_member_in_older_episode_is_not_a_candidate(self):
        for match_type in ("same_target", "same_workload_different_pod"):
            with self.subTest(match_type=match_type):
                self.prior["signals"] = []
                self.records.clear()
                self.incidents.clear()
                self.service.plane.store.get_capsule_for_incident.reset_mock()
                if match_type == "same_target":
                    future_key = self.key
                    self.current["recurrence_key"] = self.key
                    self.current["signals"] = [{"incident_id": "current-member",
                        "started_at": "2026-09-20T01:00:00Z", "recurrence_key": self.key}]
                else:
                    current_key = _recurrence_key("app", "pod", "processor-current", "QueueHigh")
                    future_key = _recurrence_key("app", "pod", "processor-old", "QueueHigh")
                    self.current["recurrence_key"] = current_key
                    self.current["signals"] = [{"incident_id": "current-member",
                        "started_at": "2026-09-20T01:00:00Z", "app_id": "app", "source_kind": "live",
                        "resource_kind": "pod", "resource_name": "processor-current",
                        "recurrence_key": current_key}]
                self.member(f"older-unrelated-{match_type}", 0,
                            _recurrence_key("app", "pod", "processor", "OtherAlert"), "OtherAlert")
                self.member(f"future-match-{match_type}", 1, future_key, "QueueHigh",
                            started_at="2026-09-20T01:01:00Z")
                if match_type == "same_workload_different_pod":
                    self.prior["signals"][-1].update({"app_id": "app", "source_kind": "live",
                        "resource_kind": "pod", "resource_name": "processor-old"})
                self.service.plane.store.recurrence_candidates_for_incident.return_value = [
                    {"episode_id": "prior", "match_type": match_type},
                ]

                self.assertEqual(self.service.historical_candidates(self.current), [])
                self.service.plane.store.get_capsule_for_incident.assert_not_called()

    def test_older_matching_member_is_kept_but_future_matching_member_is_excluded(self):
        self.member("prior-match", 0)
        self.member("future-match", 1, started_at="2026-09-20T01:01:00Z")

        result = self.service.historical_candidates(self.current)[0]

        self.assertEqual([item["incident_id"] for item in result["member_selection"]["selected_members"]],
                         ["prior-match"])
        self.assertEqual(result["member_selection"]["matching_member_count"], 1)
        self.assertEqual([item["incident_id"] for item in result["captured_evidence"]], ["prior-match"])

    def test_cross_pod_candidate_does_not_admit_weak_or_ambiguous_members(self):
        current_key = _recurrence_key("app", "pod", "processor-current", "QueueHigh")
        self.current["recurrence_key"] = current_key
        current = self.current["signals"][0]
        current.update({"app_id": "app", "source_kind": "live", "resource_kind": "pod",
                        "resource_name": "processor-current", "recurrence_key": current_key})
        prior_key = _recurrence_key("app", "pod", "processor-old", "QueueHigh")
        signal = self.member("prior-other-pod", 0, prior_key, "QueueHigh")
        self.service.plane.store.recurrence_candidates_for_incident.return_value = [
            {"episode_id": "prior", "match_type": "same_workload_different_pod"},
        ]
        rejected = [
            {"app_id": "app", "source_kind": "external", "resource_kind": "pod", "resource_name": "processor-old",
             "recurrence_key": prior_key},
            {"app_id": "other-app", "source_kind": "live", "resource_kind": "pod", "resource_name": "processor-old",
             "recurrence_key": _recurrence_key("other-app", "pod", "processor-old", "QueueHigh")},
            {"app_id": "app", "source_kind": "live", "resource_kind": "workload", "resource_name": "processor-old",
             "recurrence_key": _recurrence_key("app", "workload", "processor-old", "QueueHigh")},
            {"app_id": "app", "source_kind": "live", "resource_kind": "pod", "resource_name": "processor-old",
             "recurrence_key": _recurrence_key("app", "pod", "processor-old", "OtherAlert")},
        ]
        for index, values in enumerate(rejected):
            with self.subTest(index=index):
                signal.update(values)
                result = self.service.historical_candidates(self.current)[0]
                self.assertEqual(result["availability"], "unavailable")
                self.assertEqual(result["observations"], [])
                self.assertEqual(result["member_selection"]["selected_members"], [])

    def test_real_store_cross_pod_candidate_reaches_retained_historical_observations(self):
        store = FCAPSuleStore(self.root / "cross-pod-state.db")
        store.upsert_application("cluster:commerce:processor", "processor", "commerce", "cluster")

        def capture(incident_id, started_at, pod):
            incident = store.record_incident({
                "incident_id": incident_id, "app_id": "cluster:commerce:processor", "case_dir": str(self.root / incident_id),
                "started_at": started_at, "ended_at": started_at, "status": "resolved", "severity": "warning",
                "source_kind": "live", "resource_kind": "pod", "resource_name": pod,
                "alert_identity": "QueueHigh",
            })
            folder = self.root / incident_id
            folder.mkdir()
            (folder / "capsule.json").write_text(json.dumps({"case": {"pod": pod}}), encoding="utf-8")
            (folder / "incident_report.json").write_text(json.dumps({
                "incident": {"service": "processor", "namespace": "commerce", "cluster": "cluster"},
                "fault_alerts": [{"name": "QueueHigh"}],
                "supporting_evidence": [{"evidence_id": "e-" + incident_id, "type": "log",
                                         "title": "Queue failure", "summary": "Retained queue failure."}],
            }), encoding="utf-8")
            store.record_capsule({"capsule_id": "capsule-" + incident_id, "incident_id": incident_id,
                                  "app_id": "cluster:commerce:processor", "output_dir": str(folder)})
            return incident

        prior = capture("prior-live-pod", "2026-09-20T01:00:00Z", "processor-7f9d-old")
        capture("current-live-pod", "2026-09-20T03:00:00Z", "processor-7f9d-new")
        current_episode = store.episode_for_incident("current-live-pod")
        service = InvestigationService(SimpleNamespace(store=store, state_dir=self.root))

        candidate = service.historical_candidates(current_episode)[0]

        self.assertEqual(candidate["episode_id"], store.episode_for_incident(prior["incident_id"])["episode_id"])
        self.assertEqual(candidate["member_selection"]["candidate_relation"], "same_workload_different_pod")
        self.assertEqual(candidate["captured_evidence"][0]["incident_id"], "prior-live-pod")
        self.assertFalse(candidate["captured_evidence"][0]["matches_current_target"])
        self.assertEqual(candidate["observations"][0]["provenance"][0]["target_relation"],
                         "same_workload_different_pod")

    def test_real_store_rejects_older_episode_with_future_matching_member(self):
        for cross_pod in (False, True):
            with self.subTest(cross_pod=cross_pod):
                store = FCAPSuleStore(self.root / f"future-member-{cross_pod}.db")
                app_id = f"app-{cross_pod}"
                store.upsert_application(app_id, "processor", "commerce", "cluster")
                old_pod = "processor-old" if cross_pod else "processor-current"
                current_pod = "processor-current"

                def record(incident_id, started_at, pod, alert):
                    return store.record_incident({
                        "incident_id": incident_id, "app_id": app_id,
                        "case_dir": str(self.root / incident_id), "started_at": started_at,
                        "ended_at": started_at, "status": "resolved", "severity": "warning",
                        "source_kind": "live", "resource_kind": "pod", "resource_name": pod,
                        "alert_identity": alert,
                    })

                old = record("old-unrelated", "2026-09-20T01:00:00Z", old_pod, "OtherAlert")
                current = record("current-target", "2026-09-20T03:00:00Z", current_pod, "QueueHigh")
                future = record("future-match", "2026-09-20T04:00:00Z", old_pod, "QueueHigh")
                old_episode_id = store.episode_for_incident(old["incident_id"])["episode_id"]
                future_episode_id = store.episode_for_incident(future["incident_id"])["episode_id"]
                self.assertNotEqual(old_episode_id, future_episode_id)
                self.assertNotEqual(old_episode_id, store.episode_for_incident(current["incident_id"])["episode_id"])

                with store._connect() as connection:
                    connection.execute("DELETE FROM episode_incidents WHERE incident_id = ?", (future["incident_id"],))
                    connection.execute("INSERT INTO episode_incidents (episode_id, incident_id) VALUES (?, ?)",
                                       (old_episode_id, future["incident_id"]))
                    connection.execute("DELETE FROM incident_episodes WHERE episode_id = ?", (future_episode_id,))
                    store._refresh_episode(connection, old_episode_id)

                current_episode = store.episode_for_incident(current["incident_id"])
                candidates = store.recurrence_candidates_for_incident(
                    current_episode["episode_id"], current["incident_id"]
                )

                self.assertEqual(candidates, [])

    def test_revision_primary_identity_overrides_episode_primary_without_relabeling(self):
        self.mixed_members()
        other_key = _recurrence_key("app", "pod", "processor", "OtherAlert")
        self.current["signals"].append({"incident_id": "revision-member",
                                         "started_at": "2026-09-20T01:00:00Z",
                                         "recurrence_key": other_key})
        self.service.plane.store.recurrence_candidates_for_incident.return_value = [{"episode_id": "prior"}]
        result = self.service.historical_candidates(self.current, "revision-member")[0]
        self.service.plane.store.recurrence_candidates_for_incident.assert_called_once_with("current", "revision-member")
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

    def test_real_store_revision_identity_selects_candidates_not_display_primary(self):
        store = FCAPSuleStore(self.root / "real-state.db")
        store.upsert_application("app", "Processor", "tasks", "test")
        store.upsert_application("outside", "Other", "tasks", "test")

        def capture(name, day, alert="QueueHigh", minute=0, severity="warning", app="app", resource="processor"):
            store.record_incident({"incident_id": name, "app_id": app, "case_dir": str(self.root / name),
                "started_at": f"2026-09-{day:02d}T00:{minute:02d}:00Z", "status": "resolved", "severity": severity,
                "resource_kind": "pod", "resource_name": resource, "alert_identity": alert})
            self.member(name, minute, _recurrence_key(app, "pod", resource, alert), alert)
            store.record_capsule({"capsule_id": "capsule-" + name, "incident_id": name, "app_id": app,
                                  "output_dir": self.records[name]["output_dir"]})

        for day in (18, 19, 20, 21):
            capture(f"prior-a-{day}", day)
            capture(f"prior-a-repeat-{day}", day, minute=1)
            capture(f"prior-b-{day}", day, "OtherAlert", minute=2, severity="critical")
        capture("other-resource", 22, resource="different")
        capture("current-a", 23)
        capture("future-a", 24)
        service = InvestigationService(SimpleNamespace(store=store, state_dir=self.root))
        episode = store.episode_for_incident("current-a")
        expected = [store.episode_for_incident(f"prior-a-{day}")["episode_id"] for day in (21, 20, 19)]
        self.assertEqual(episode["recurrence"]["candidates"], [])
        self.assertEqual([item["episode_id"] for item in service.historical_candidates(episode)], expected)
        service.plane.evidence = SimpleNamespace(model_evidence=Mock(return_value=[]))
        _, checks = service._retained_review_context(episode["episode_id"])
        self.assertEqual([item["result"]["episode"]["episode_id"] for item in checks], expected)
        self.assertEqual(checks[0]["result"]["observations"][0]["source"]["provenance"][0]["incident_id"], "prior-a-repeat-21")
        capture("current-b", 23, "OtherAlert", minute=1, severity="critical")
        capture("outside-incident", 22, app="outside")
        episode = store.episode_for_incident("current-a")
        self.assertEqual(episode["primary_incident_id"], "current-b")
        self.assertEqual(service.historical_candidates(episode)[0]["observations"][0]["provenance"][0]["incident_id"], "prior-b-21")
        candidates = service.historical_candidates(episode, "current-a")
        self.assertEqual([item["episode_id"] for item in candidates], expected)
        self.assertEqual(candidates[0]["observations"][0]["provenance"][0]["incident_id"], "prior-a-repeat-21")
        self.assertEqual(service.historical_candidates(episode, "outside-incident"), [])
        self.assertEqual(service.historical_candidates(episode, "prior-a-21"), [])
        self.assertEqual(store.recurrence_candidates_for_incident(episode["episode_id"], "outside-incident"), [])
        self.assertEqual(store.recurrence_candidates_for_incident(episode["episode_id"], "prior-a-21"), [])
        self.assertEqual(store.get_episode(episode["episode_id"])["primary_incident_id"], "current-b")


if __name__ == "__main__":
    unittest.main()
