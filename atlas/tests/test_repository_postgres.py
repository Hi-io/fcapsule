from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from atlas.normalize import normalize_case, observation_pattern_id
from atlas.repository import IdempotencyConflict, PostgresAtlasRepository


DSN = os.environ.get("ATLAS_TEST_DATABASE_URL")


def payload(instance: str, episode: str, revision: int, at: datetime, observation: tuple[str, str, int], fingerprint: str | None = None) -> dict:
    kind, key, value = observation
    return {
        "instance_id": instance,
        "episode_id": episode,
        "revision": revision,
        "observed_at": at.isoformat(),
        "scope": {"environment": "test", "cluster": "atlas-test-cluster"},
        "summary": f"Recorded {key} observation",
        "observations": [{"kind": kind, "key": key, "value": value, "unit": "count", "source": "test"}],
        "hypotheses": [{"statement": "Unverified test hypothesis", "confidence": None, "supporting_refs": []}],
        "fingerprint": fingerprint,
    }


@unittest.skipUnless(DSN, "set ATLAS_TEST_DATABASE_URL to a disposable PostgreSQL database")
class PostgresRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = PostgresAtlasRepository(DSN)
        self.repo.migrate()
        self.prefix = f"atlas-test-{uuid.uuid4().hex}"
        self.cluster = f"cluster-{uuid.uuid4().hex}"

    def tearDown(self) -> None:
        with self.repo._connect() as conn:
            conn.execute("DELETE FROM atlas_cases WHERE instance_id LIKE %s", (f"{self.prefix}%",))

    def add_case(self, episode: str, revision: int, at: datetime, observation: tuple[str, str, int], fingerprint: str | None = None, instance_suffix: str = "a") -> dict:
        case = normalize_case(payload(f"{self.prefix}-{instance_suffix}", episode, revision, at, observation, fingerprint))
        case["scope"]["cluster"] = self.cluster
        return self.repo.create_case(case)["case"]

    def test_idempotency_is_immutable_and_revisions_are_preserved(self) -> None:
        now = datetime.now(timezone.utc)
        original = normalize_case(payload(f"{self.prefix}-a", "ep", 1, now, ("metric", "restarts", 2)))
        original["scope"]["cluster"] = self.cluster
        first = self.repo.create_case(original)
        replay = self.repo.create_case(original)
        self.assertTrue(first["created"])
        self.assertFalse(replay["created"])
        self.assertEqual(first["case"]["id"], replay["case"]["id"])
        changed = {**original, "summary": "A different payload"}
        with self.assertRaises(IdempotencyConflict):
            self.repo.create_case(changed)
        second = self.add_case("ep", 2, now + timedelta(seconds=1), ("metric", "memory", 5))
        self.assertNotEqual(first["case"]["id"], second["id"])
        self.assertEqual(self.repo.get_case(first["case"]["id"])["revision"], 1)
        self.assertEqual(self.repo.get_case(second["id"])["revision"], 2)

    def test_patterns_count_latest_revision_once_and_expose_cooccurrence_only(self) -> None:
        now = datetime.now(timezone.utc)
        self.add_case("episode-1", 1, now, ("metric", "restarts", 3), instance_suffix="1")
        self.add_case("episode-1", 2, now + timedelta(seconds=1), ("metric", "memory", 7), instance_suffix="1")
        self.add_case("episode-2", 1, now + timedelta(seconds=2), ("metric", "memory", 7), instance_suffix="2")
        self.add_case("episode-3", 1, now + timedelta(seconds=3), ("metric", "memory", 7), instance_suffix="3")

        patterns = self.repo.list_patterns(scope={"cluster": self.cluster}, limit=10)["patterns"]
        pattern = next(item for item in patterns if item["key"] == "memory" and item["value"] == 7)
        self.assertEqual(pattern["case_count"], 3)
        self.assertEqual(pattern["instance_count"], 3)
        self.assertEqual(pattern["similarity"], "same_observation")
        self.assertIn("not evidence of a shared cause", pattern["interpretation"])
        detail = self.repo.get_pattern(pattern["id"])
        self.assertEqual(detail["pattern"]["case_count"], len(detail["cases"]))
        self.assertFalse(detail["has_more"])
        self.assertNotIn("episode-1", {case["episode_id"] for case in detail["cases"] if case["revision"] == 1})

        old_pattern_id = observation_pattern_id({"kind": "metric", "key": "restarts", "value": 3, "unit": "count"})
        self.assertIsNone(self.repo.get_pattern(old_pattern_id))

    def test_exact_fingerprint_is_ranked_before_newer_lexical_candidates(self) -> None:
        now = datetime.now(timezone.utc)
        old = self.add_case("old-exact", 1, now - timedelta(hours=2), ("metric", "restarts", 3), "rare-fingerprint", "old")
        self.add_case("new-a", 1, now - timedelta(minutes=1), ("metric", "restarts", 3), "other-a", "new-a")
        self.add_case("new-b", 1, now, ("metric", "restarts", 3), "other-b", "new-b")

        import atlas.repository as repository_module
        original_cap = repository_module.MAX_SEARCH_CANDIDATES
        repository_module.MAX_SEARCH_CANDIDATES = 1
        try:
            result = self.repo.search(
                scope={"cluster": self.cluster}, query="restarts", fingerprint="rare-fingerprint", limit=1
            )
        finally:
            repository_module.MAX_SEARCH_CANDIDATES = original_cap
        self.assertEqual(result["cases"][0]["id"], old["id"])
        self.assertEqual(result["cases"][0]["relation"], "fingerprint_match")


if __name__ == "__main__":
    unittest.main()
