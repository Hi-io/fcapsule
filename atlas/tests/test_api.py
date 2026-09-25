from __future__ import annotations

import json
import uuid
import unittest
from copy import deepcopy

from fastapi.testclient import TestClient

from atlas.app import create_app
from atlas.repository import IdempotencyConflict


TOKEN = "atlas-test-token-with-at-least-32-bytes"


class MemoryRepository:
    def __init__(self) -> None:
        self.cases: dict[tuple[str, str, int], dict] = {}
        self.search_call: dict | None = None
        self.pattern_call: dict | None = None

    def migrate(self) -> None:
        pass

    def healthcheck(self) -> bool:
        return True

    def create_case(self, case: dict) -> dict:
        key = (case["instance_id"], case["episode_id"], case["revision"])
        existing = self.cases.get(key)
        if existing:
            if any(existing[name] != value for name, value in case.items()):
                raise IdempotencyConflict("This idempotency key already has a different case payload")
            return {"case": deepcopy(existing), "created": False}
        stored = {**deepcopy(case), "id": str(uuid.uuid4())}
        self.cases[key] = stored
        return {"case": deepcopy(stored), "created": True}

    def get_case(self, case_id: str) -> dict | None:
        return next((deepcopy(case) for case in self.cases.values() if case["id"] == case_id), None)

    def search(self, **kwargs) -> dict:
        self.search_call = kwargs
        return {"cases": [], "limit": kwargs["limit"], "has_more": False}

    def list_patterns(self, **kwargs) -> dict:
        self.pattern_call = kwargs
        return {"patterns": [], "limit": kwargs["limit"], "has_more": False}

    def get_pattern(self, pattern_id: str) -> dict | None:
        return None


def case_payload(**changes) -> dict:
    payload = {
        "schema_version": 1,
        "normalization_version": "fp-v1",
        "instance_id": "site-alpha",
        "episode_id": "episode-17",
        "observed_at": "2026-08-02T09:15:00+09:00",
        "scope": {
            "environment": "prod",
            "cluster": "west-1",
            "namespace": "checkout",
            "service": "cart",
            "workload": "cart-api",
            "cnfc_id": "cnfc-3",
            "vnfc_id": "vnfc-8",
        },
        "summary": "Container restart count increased",
        "observations": [
            {
                "kind": "Metric",
                "key": "Restart Count",
                "value": 3,
                "unit": "count",
                "source": "prometheus",
                "observed_at": "2026-08-02T00:10:00Z",
                "reference": "metric:pod-restarts",
            }
        ],
        "hypotheses": [{
            "statement": "Memory pressure may have contributed",
            "confidence": None,
            "supporting_refs": ["metric:pod-restarts"],
        }],
        "fingerprint": "podrestart-v1:abc123",
    }
    payload.update(changes)
    return payload


class AtlasAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = MemoryRepository()
        self.context = TestClient(create_app(repository=self.repository, token=TOKEN))
        self.client = self.context.__enter__()
        self.headers = {"Authorization": f"Bearer {TOKEN}"}

    def tearDown(self) -> None:
        self.context.__exit__(None, None, None)

    def test_health_is_unauthenticated_and_checks_repository(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_data_endpoints_require_bearer_token(self) -> None:
        response = self.client.post("/v1/search", json={})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")

    def test_case_create_is_idempotent_and_returns_versioned_envelope(self) -> None:
        first = self.client.post("/v1/cases", json=case_payload(), headers=self.headers)
        self.assertEqual(first.status_code, 201)
        result = first.json()
        case = result["case"]
        self.assertTrue(result["created"])
        self.assertEqual(case["schema_version"], 1)
        self.assertEqual(case["normalization_version"], "fp-v1")
        self.assertEqual(case["revision"], 1)
        self.assertEqual(case["observed_at"], "2026-08-02T00:15:00Z")
        self.assertEqual(case["scope"]["environment"], "prod")
        self.assertEqual(case["observations"][0]["kind"], "metric")
        self.assertEqual(case["observations"][0]["key"], "restart_count")
        self.assertIsNone(case["hypotheses"][0]["confidence"])
        self.assertNotIn("statement", case["observations"][0])

        replay = self.client.post("/v1/cases", json=case_payload(), headers=self.headers)
        self.assertEqual(replay.status_code, 200)
        self.assertFalse(replay.json()["created"])
        self.assertEqual(replay.json()["case"]["id"], case["id"])

        detail = self.client.get(f"/v1/cases/{case['id']}", headers=self.headers)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["case"], case)

    def test_different_payload_under_same_idempotency_key_conflicts(self) -> None:
        self.client.post("/v1/cases", json=case_payload(), headers=self.headers)
        changed = case_payload(summary="Different evidence summary")
        response = self.client.post("/v1/cases", json=changed, headers=self.headers)
        self.assertEqual(response.status_code, 409)

    def test_rejects_nested_telemetry_and_secret_values_without_echoing_them(self) -> None:
        nested = case_payload(observations=[{"kind": "log", "key": "line", "value": {"raw": "too much"}}])
        response = self.client.post("/v1/cases", json=nested, headers=self.headers)
        self.assertEqual(response.status_code, 422)

        secret = "token=supersecretvalue123"
        response = self.client.post(
            "/v1/cases", json=case_payload(summary=secret), headers=self.headers
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(secret, response.text)

    def test_body_and_search_result_are_bounded(self) -> None:
        response = self.client.post("/v1/cases", content=b" " * (40 * 1024 + 1), headers=self.headers)
        self.assertEqual(response.status_code, 413)

        response = self.client.post("/v1/search", json={"limit": 11}, headers=self.headers)
        self.assertEqual(response.status_code, 422)

    def test_search_supports_time_alias_and_exact_scope(self) -> None:
        response = self.client.post(
            "/v1/search",
            json={
                "query": "restart count",
                "scope": {"environment": "prod", "cluster": "west-1"},
                "before": "2026-08-03T00:00:00Z",
                "limit": 7,
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"cases": [], "limit": 7, "has_more": False})
        self.assertEqual(self.repository.search_call["scope"], {"environment": "prod", "cluster": "west-1"})
        self.assertEqual(self.repository.search_call["limit"], 7)
        self.assertEqual(self.repository.search_call["before"].isoformat(), "2026-08-03T00:00:00+00:00")

    def test_search_rejects_naive_time_and_unsafe_scope(self) -> None:
        naive = self.client.post("/v1/search", json={"before": "2026-08-03T00:00:00"}, headers=self.headers)
        self.assertEqual(naive.status_code, 422)
        secret_scope = self.client.post("/v1/search", json={"scope": {"cluster": "token=unsafevalue123"}}, headers=self.headers)
        self.assertEqual(secret_scope.status_code, 422)

    def test_pattern_list_accepts_json_scope_and_time(self) -> None:
        response = self.client.get(
            "/v1/patterns",
            params={"scope": json.dumps({"environment": "prod", "cluster": "west-1"}), "query": "restart", "observed_before": "2026-08-03T00:00:00Z", "limit": 4},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.repository.pattern_call["scope"], {"environment": "prod", "cluster": "west-1"})
        self.assertEqual(self.repository.pattern_call["limit"], 4)

    def test_pattern_detail_not_found(self) -> None:
        response = self.client.get("/v1/patterns/not-a-pattern", headers=self.headers)
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
