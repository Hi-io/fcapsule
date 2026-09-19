import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fcapsule.adapters.kubernetes_adapter import KubernetesAdapter
from fcapsule.adapters.opensearch_adapter import OpenSearchAdapter
from fcapsule.adapters.prometheus_adapter import PrometheusAdapter
from fcapsule.live_sources import LiveSourceCoordinator, _resolve_alert_pod
from fcapsule.store import FCAPSuleStore


class FakeTransport:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def request(self, path, method="GET", body=None):
        self.requests.append((path, method, body))
        for key, response in self.responses.items():
            if key in path:
                return response
        raise AssertionError(f"Unexpected request: {path}")


class LiveSourceTests(unittest.TestCase):
    def test_alert_for_disappeared_named_pod_is_not_reassigned(self):
        pods = [{"namespace": "shop", "name": "healthy-api"}]

        self.assertIsNone(_resolve_alert_pod(pods, "shop", "deleted-worker"))
        self.assertEqual(_resolve_alert_pod(pods, "shop", ""), pods[0])

    def test_prometheus_adapter_normalizes_inventory_alerts_and_ranges(self):
        adapter = PrometheusAdapter("http://prometheus")
        adapter.transport = FakeTransport(
            {
                "/api/v1/alerts": {
                    "status": "success",
                    "data": {
                        "alerts": [
                            {
                                "labels": {"alertname": "PodRestart", "severity": "warning", "namespace": "shop"},
                                "annotations": {"summary": "Restarted"},
                                "state": "firing",
                                "activeAt": "2026-09-20T00:00:00Z",
                            }
                        ]
                    },
                },
                "/api/v1/query_range": {
                    "status": "success",
                    "data": {
                        "result": [
                            {
                                "metric": {"namespace": "shop", "pod": "api-1"},
                                "values": [[1789862400, "1"], [1789862430, "2"]],
                            }
                        ]
                    },
                },
                "/api/v1/query": {
                    "status": "success",
                    "data": {
                        "result": [
                            {"metric": {"namespace": "shop", "pod": "api-1", "node": "node-1"}, "value": [1789862400, "1"]}
                        ]
                    },
                },
            }
        )
        self.assertEqual(adapter.active_alerts()[0]["alertname"], "PodRestart")
        self.assertIn(("shop", "api-1"), adapter.pod_inventory({"shop"}))
        end = datetime(2026, 9, 20, tzinfo=timezone.utc)
        metrics = adapter.collect_pod_metrics("shop", "api-1", end - timedelta(minutes=5), end)
        self.assertEqual(len(metrics), 4)
        self.assertEqual(metrics[0]["labels"]["pod"], "api-1")

    def test_opensearch_adapter_maps_filebeat_fields(self):
        adapter = OpenSearchAdapter("http://opensearch")
        adapter.transport = FakeTransport(
            {
                "/_search": {
                    "hits": {
                        "hits": [
                            {
                                "_source": {
                                    "@timestamp": "2026-09-20T00:00:00Z",
                                    "message": "level=error connection refused",
                                    "kubernetes": {
                                        "namespace": "shop",
                                        "pod": {"name": "api-1"},
                                        "container": {"name": "api"},
                                        "labels": {"app_kubernetes_io/name": "payments"},
                                    },
                                }
                            }
                        ]
                    }
                }
            }
        )
        end = datetime(2026, 9, 20, tzinfo=timezone.utc)
        logs = adapter.collect_logs("shop", "api-1", end - timedelta(minutes=5), end)
        self.assertEqual(logs[0]["level"], "ERROR")
        self.assertEqual(logs[0]["service"], "payments")

    def test_kubernetes_snapshot_masks_sensitive_configmap_keys(self):
        adapter = KubernetesAdapter("http://kubernetes")
        adapter.transport = FakeTransport(
            {
                "/configmaps/api-settings": {
                    "metadata": {"resourceVersion": "12"},
                    "data": {"MODE": "production", "API_TOKEN": "do-not-retain"},
                }
            }
        )
        pod = {
            "name": "api-1",
            "namespace": "shop",
            "workload": "payments",
            "images": ["example/api:1"],
            "containers": ["api"],
            "node": "node-1",
            "phase": "Running",
            "ready": True,
            "raw_spec": {"containers": [{"envFrom": [{"configMapRef": {"name": "api-settings"}}]}]},
        }
        snapshot = adapter.configuration_snapshot(pod)
        self.assertEqual(snapshot[1]["data"]["API_TOKEN"], "<redacted>")
        self.assertEqual(snapshot[1]["data"]["MODE"], "production")

    def test_source_configuration_is_validated_and_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            coordinator = LiveSourceCoordinator(FCAPSuleStore(state / "state.db"), state)
            config = coordinator.update_configuration(
                {
                    "prometheus_url": "http://prometheus:9090",
                    "opensearch_url": "http://opensearch:9200",
                    "opensearch_index": "k8s-logs-*",
                    "cluster_name": "cluster-a",
                    "namespaces": "shop, platform",
                    "poll_interval_seconds": 20,
                    "incident_window_minutes": 15,
                    "enabled": True,
                    "auto_build_reports": True,
                }
            )
            self.assertEqual(config["namespaces"], ["platform", "shop"])
            self.assertTrue((state / "source-settings.json").is_file())
            with self.assertRaises(ValueError):
                coordinator.update_configuration({"prometheus_url": "prometheus:9090"})

    def test_missing_kubernetes_application_is_marked_not_observed(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            store = FCAPSuleStore(state / "state.db")
            coordinator = LiveSourceCoordinator(store, state)
            store.upsert_application(
                "cluster-a:shop:checkout",
                "checkout",
                "shop",
                "cluster-a",
                source_config={
                    "metrics": {"adapter": "prometheus", "status": "observed", "pods_observed": 1},
                    "logs": {"adapter": "opensearch", "status": "observed", "recent_documents": 30},
                    "configuration": {"adapter": "kubernetes", "status": "available", "pods_visible": 1},
                    "pods": [{"name": "checkout-1"}],
                },
            )

            coordinator._mark_unobserved_applications({"cluster_name": "cluster-a"}, set())

            application = store.get_application("cluster-a:shop:checkout")
            self.assertEqual(application["status"], "not_observed")
            self.assertEqual(application["source_config"]["pods"], [])
            self.assertEqual(application["source_config"]["metrics"]["status"], "not_observed")


if __name__ == "__main__":
    unittest.main()
