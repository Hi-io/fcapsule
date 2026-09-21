import unittest

from fcapsule.related_groups import derive_related_episode_groups, episode_profile


def profile(episode_id, app_id, started_at, *, cluster="cluster-a", alert="TargetDown", node="worker-a", dependency="mysql"):
    return {
        "episode_id": episode_id, "reference": episode_id, "app_id": app_id, "cluster": cluster,
        "status": "active", "severity": "warning", "started_at": __import__("datetime").datetime.fromisoformat(started_at.replace("Z", "+00:00")),
        "last_activity_at": __import__("datetime").datetime.fromisoformat(started_at.replace("Z", "+00:00")),
        "alerts": {alert}, "nodes": {node} if node else set(), "dependencies": {dependency} if dependency else set(),
    }


class RelatedGroupTests(unittest.TestCase):
    def test_groups_require_alert_family_time_cluster_and_independent_shared_link(self):
        groups = derive_related_episode_groups([
            profile("one", "api", "2026-09-22T10:00:00Z"),
            profile("two", "worker", "2026-09-22T10:09:00Z"),
            profile("other-alert", "billing", "2026-09-22T10:04:00Z", alert="MemoryPressure"),
            profile("other-node", "inventory", "2026-09-22T10:04:00Z", node="worker-b", dependency=None),
            profile("too-late", "search", "2026-09-22T10:22:00Z"),
            profile("other-cluster", "audit", "2026-09-22T10:03:00Z", cluster="cluster-b"),
        ])

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["episode_ids"], ["one", "two"])
        self.assertIn("same_node", {item["kind"] for item in groups[0]["basis"]})
        self.assertIn("suspected", groups[0]["relationship"])

    def test_same_application_is_left_to_the_existing_episode_and_history_views(self):
        groups = derive_related_episode_groups([
            profile("one", "api", "2026-09-22T10:00:00Z"),
            profile("two", "api", "2026-09-22T10:03:00Z"),
        ])
        self.assertEqual(groups, [])

    def test_profile_uses_retained_configuration_and_topology_not_free_text(self):
        episode = {"episode_id": "one", "reference": "EP-1", "app_id": "api", "status": "active", "severity": "warning", "started_at": "2026-09-22T10:00:00Z", "last_activity_at": "2026-09-22T10:01:00Z"}
        entry = {
            "report": {"fault_alerts": [{"name": "TargetDown"}], "topology": [{"from": "api", "to": "mysql"}]},
            "capsule": {"selected_evidence": [{"type": "configuration", "configuration": {"kind": "PodSpec", "node": "worker-a"}}]},
        }
        result = episode_profile(episode, {"cluster": "cluster-a"}, [entry])

        self.assertEqual(result["nodes"], {"worker-a"})
        self.assertEqual(result["dependencies"], {"mysql"})
        self.assertEqual(result["alerts"], {"targetdown"})


if __name__ == "__main__":
    unittest.main()
