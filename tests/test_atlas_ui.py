import json
import threading
import unittest
from http.client import HTTPConnection
from types import SimpleNamespace

from fcapsule.ui.app import FCAPSuleHTTPServer, HTML, JS


class FakeAtlasClient:
    def __init__(self):
        self.calls = []

    def list_patterns(self, **kwargs):
        self.calls.append(("list_patterns", kwargs))
        return {"patterns": [{"id": "p-1"}], "limit": kwargs["limit"]}

    def stats(self):
        self.calls.append(("stats", None))
        return {"episodes": 3, "revisions": 5, "patterns": 2}

    def list_cases(self, **kwargs):
        self.calls.append(("list_cases", kwargs))
        return {"cases": [{"id": "c-1", "episode_id": "ep-1"}], "next_cursor": "next-page"}

    def get_pattern(self, pattern_id):
        self.calls.append(("get_pattern", pattern_id))
        return {"pattern": {"id": pattern_id}, "cases": []}

    def get_case(self, case_id):
        self.calls.append(("get_case", case_id))
        return {"case": {"id": case_id}}

    def search(self, **kwargs):
        self.calls.append(("search", kwargs))
        return {"cases": [{"id": "c-1"}], "limit": kwargs["limit"]}


class FakeControlPlane:
    def __init__(self, client=None):
        self.client = client or FakeAtlasClient()
        self.config = {
            "url": "https://atlas.example.test",
            "instance_id": "fcapsule-test",
            "token_configured": True,
            "read_enabled": True,
            "publish_enabled": False,
            "pending_count": 0,
            "failed_count": 0,
            "last_error": "",
        }
        self.updated = None
        self.withdrawal_episode = None
        self.investigator = SimpleNamespace(stopping=False)
        self.briefing_executor = SimpleNamespace(shutdown=lambda **_: None)
        self.evidence = SimpleNamespace(shutdown=lambda **_: None)

    def stop_live_monitoring(self):
        pass

    def atlas_client(self, operation):
        return self.client if operation == "read" and self.config["read_enabled"] else None

    def atlas_configuration(self):
        return dict(self.config)

    def update_atlas_configuration(self, payload):
        self.updated = payload
        self.config.update({key: value for key, value in payload.items() if key != "token"})
        self.config["token_configured"] = self.config["token_configured"] or bool(payload.get("token"))
        return self.atlas_configuration()

    def retry_atlas_publications(self, limit=100):
        self.retry_limit = limit
        return {"status": "queued", "limit": limit}

    def request_collective_withdrawal(self, episode_id):
        self.withdrawal_episode = episode_id
        return {"status": "pending", "attempts": 0}


class AtlasUIRouteTests(unittest.TestCase):
    def setUp(self):
        self.plane = FakeControlPlane()
        self.server = FCAPSuleHTTPServer(("127.0.0.1", 0), self.plane)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, payload=None):
        connection = HTTPConnection("127.0.0.1", self.port)
        body = json.dumps(payload).encode() if payload is not None else None
        connection.request(method, path, body, {"Content-Type": "application/json"} if body else {})
        response = connection.getresponse()
        result = json.loads(response.read())
        connection.close()
        return response.status, result

    def test_collective_page_is_the_public_route_and_legacy_pages_still_resolve(self):
        for path in ("/collective", "/estima", "/atlas"):
            connection = HTTPConnection("127.0.0.1", self.port)
            connection.request("GET", path)
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertIn('href="/collective"', body)
            self.assertIn(">Collective</a>", body)
        self.assertIn("no LLM API keys are sent to Collective", JS)
        self.assertIn("/api/settings/collective", JS)
        self.assertNotIn("/api/settings/atlas", JS)

    def test_legacy_local_api_aliases_continue_to_work(self):
        self.assertEqual(self.request("GET", "/api/atlas/patterns")[0], 200)
        self.assertEqual(self.request("GET", "/api/estima/stats")[1]["episodes"], 3)
        status, result = self.request("POST", "/api/settings/atlas", {
            "url": "https://atlas.example.test", "instance_id": "fcapsule-test",
            "read_enabled": True, "publish_enabled": False,
        })
        self.assertEqual(status, 200)
        self.assertEqual(result["url"], "https://atlas.example.test")

    def test_collective_stats_and_case_pages_reach_the_service(self):
        self.assertEqual(self.request("GET", "/api/collective/stats")[1]["episodes"], 3)
        status, result = self.request("GET", "/api/collective/cases?cluster=west&query=timeout&limit=7&cursor=next")
        self.assertEqual(status, 200)
        self.assertEqual(result["next_cursor"], "next-page")
        self.assertEqual(self.plane.client.calls[-1], ("list_cases", {
            "scope": {"cluster": "west"}, "query": "timeout", "limit": 7, "cursor": "next",
        }))

    def test_list_pattern_passes_structured_cluster_scope(self):
        status, result = self.request("GET", "/api/estima/patterns?cluster=west&query=timeout&limit=5")
        self.assertEqual(status, 200)
        self.assertEqual(result["patterns"][0]["id"], "p-1")
        self.assertEqual(self.plane.client.calls[-1], ("list_patterns", {
            "scope": {"cluster": "west"}, "query": "timeout", "limit": 5, "before": None,
        }))

    def test_pattern_and_case_ids_are_decoded_and_search_passes_supported_scope(self):
        self.assertEqual(self.request("GET", "/api/estima/patterns/latency%2Fdb")[1]["pattern"]["id"], "latency/db")
        self.assertEqual(self.request("GET", "/api/estima/cases/case%2Fone")[1]["case"]["id"], "case/one")
        status, result = self.request("POST", "/api/estima/search", {"query": "timeout", "scope": {"cluster": "west"}})
        self.assertEqual(status, 200)
        self.assertEqual(result["cases"][0]["id"], "c-1")
        self.assertEqual(self.plane.client.calls[-1], ("search", {
            "scope": {"cluster": "west"}, "query": "timeout", "limit": 10, "before": None,
        }))

    def test_unavailable_read_is_not_an_empty_success(self):
        self.plane.config["read_enabled"] = False
        status, result = self.request("GET", "/api/estima/patterns")
        self.assertEqual(status, 503)
        self.assertEqual(result["status"], "disabled")
        self.assertNotIn("patterns", result)

    def test_search_limit_matches_atlas_contract(self):
        status, _ = self.request("POST", "/api/estima/search", {"query": "timeout", "limit": 50})
        self.assertEqual(status, 200)
        self.assertEqual(self.plane.client.calls[-1][1]["limit"], 10)

    def test_settings_response_never_echoes_bearer_token(self):
        status, result = self.request("POST", "/api/settings/estima", {
            "url": "https://atlas.example.test", "instance_id": "fcapsule-test",
            "token": "do-not-return-this", "read_enabled": True,
            "publish_enabled": False, "clear_token": False,
        })
        self.assertEqual(status, 200)
        self.assertNotIn("do-not-return-this", json.dumps(result))
        self.assertTrue(result["token_configured"])
        self.assertEqual(self.plane.updated["token"], "do-not-return-this")

    def test_manual_retry_is_bounded(self):
        status, result = self.request("POST", "/api/estima/retry-failed")
        self.assertEqual(status, 202)
        self.assertEqual(result["status"], "queued")
        self.assertEqual(self.plane.retry_limit, 100)

    def test_explicit_collective_withdrawal_route_uses_local_episode_id(self):
        status, result = self.request("POST", "/api/episodes/local%2Fepisode/collective-withdrawal")
        self.assertEqual(status, 202)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(self.plane.withdrawal_episode, "local/episode")


if __name__ == "__main__":
    unittest.main()
