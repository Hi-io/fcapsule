import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from fcapsule.adapters.kubernetes_adapter import KubernetesAdapter
from fcapsule.adapters.opensearch_adapter import OpenSearchAdapter
from fcapsule.adapters.prometheus_adapter import PrometheusAdapter
from fcapsule.adapters.transport import ResponseTooLargeError
from fcapsule.live_sources import LiveSourceCoordinator, _resolve_alert_pod
from fcapsule.store import FCAPSuleStore


class FakeTransport:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    def request(self, path, method="GET", body=None, max_response_bytes=None):
        self.requests.append((path, method, body))
        for key, response in self.responses.items():
            if key in path:
                return response
        raise AssertionError(f"Unexpected request: {path}")


class FocusedLogTransport:
    def __init__(self):
        self.requests = []

    def request(self, path, method="GET", body=None, max_response_bytes=None):
        self.requests.append((path, method, body))
        order = body["sort"][0]["@timestamp"]
        if order == "desc":
            hit = {
                "_id": "baseline",
                "_source": {
                    "@timestamp": "2026-09-20T00:04:59Z",
                    "message": "inventory request completed",
                    "kubernetes": {"namespace": "shop", "pod": {"name": "api-1"}},
                },
            }
        else:
            hit = {
                "_id": "incident",
                "_source": {
                    "@timestamp": "2026-09-20T00:05:01Z",
                    "message": "max_connections reached; checkout rejected",
                    "kubernetes": {"namespace": "shop", "pod": {"name": "api-1"}},
                },
            }
        return {"hits": {"hits": [hit]}}


class LiveSourceTests(unittest.TestCase):
    def test_alert_for_disappeared_named_pod_is_not_reassigned(self):
        pods = [{"namespace": "shop", "name": "healthy-api", "workload": "api"}]

        self.assertIsNone(_resolve_alert_pod(pods, "shop", {"pod": "deleted-worker"}))
        self.assertIsNone(_resolve_alert_pod(pods, "shop", {"deployment": "deleted-worker"}))
        self.assertEqual(_resolve_alert_pod(pods, "shop", {"deployment": "api"}), pods[0])
        self.assertEqual(_resolve_alert_pod(pods, "shop", {}), pods[0])

    def test_service_scoped_alert_uses_one_selector_backed_workload(self):
        orders = [
            {"namespace": "shop", "name": "orders-1", "workload": "orders", "ready": False},
            {"namespace": "shop", "name": "orders-2", "workload": "orders", "ready": True},
        ]
        unrelated = {"namespace": "shop", "name": "inventory-1", "workload": "inventory", "ready": True}

        self.assertEqual(
            _resolve_alert_pod(orders + [unrelated], "shop", {"service": "orders"}, orders),
            orders[1],
        )
        self.assertEqual(
            _resolve_alert_pod(orders + [unrelated], "shop", {"target_workload": "orders"}),
            orders[1],
        )
        self.assertIsNone(
            _resolve_alert_pod(orders + [unrelated], "shop", {"service": "shared"}, orders + [unrelated])
        )

    def test_live_capture_uses_explicit_target_workload_for_a_shared_monitoring_service(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            coordinator = LiveSourceCoordinator(FCAPSuleStore(state / "state.db"), state)
            coordinator.update_configuration(
                {
                    "prometheus_url": "http://prometheus:9090",
                    "opensearch_url": "http://opensearch:9200",
                    "opensearch_index": "k8s-logs-*",
                    "cluster_name": "cluster-a",
                    "namespaces": ["shop"],
                }
            )
            pod = {
                "name": "orders-1",
                "namespace": "shop",
                "workload": "orders-api",
                "labels": {"app.kubernetes.io/name": "orders-api"},
                "ready": True,
                "phase": "Running",
            }
            inventory = {
                "name": "inventory-1",
                "namespace": "shop",
                "workload": "inventory-api",
                "labels": {"app.kubernetes.io/name": "inventory-api"},
                "ready": True,
                "phase": "Running",
            }
            prometheus, opensearch, kubernetes = Mock(), Mock(), Mock()
            coordinator.adapters = Mock(return_value=(prometheus, opensearch, kubernetes))
            coordinator.test_connections = Mock(
                return_value={"targets": {name: {"ok": True} for name in ("prometheus", "opensearch", "kubernetes")}}
            )
            kubernetes.list_pods.return_value = [pod, inventory]
            prometheus.pod_inventory.return_value = {}
            opensearch.pod_log_counts.return_value = {}
            prometheus.active_alerts.return_value = [
                {
                    "alertname": "MetricsDiscoveryMissing",
                    "status": "firing",
                    "startsAt": "2026-09-22T00:00:00Z",
                    "labels": {
                        "namespace": "shop",
                        "service": "orders-api",
                        "target_service": "lab-app-metrics",
                        "target_workload": "orders-api",
                    },
                }
            ]
            prometheus.alert_rules.return_value = {}

            with patch.object(coordinator, "_capture_case", return_value=state / "case"):
                result = coordinator.synchronize()

            kubernetes.service_pods.assert_not_called()
            self.assertEqual(len(result["captured"]), 1)

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
                            ,
                            {
                                "labels": {"alertname": "PendingThreshold", "severity": "warning", "namespace": "shop"},
                                "annotations": {"summary": "Not firing yet"},
                                "state": "pending",
                                "activeAt": "2026-09-20T00:00:10Z",
                            },
                        ]
                    },
                },
                "/api/v1/rules": {
                    "status": "success",
                    "data": {
                        "groups": [
                            {
                                "name": "workloads",
                                "file": "/etc/prometheus/rules.yaml",
                                "rules": [
                                    {
                                        "type": "alerting",
                                        "name": "PodRestart",
                                        "query": "increase(kube_pod_container_status_restarts_total[5m]) > 0",
                                        "duration": 60,
                                        "health": "ok",
                                    }
                                ],
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
        alerts = adapter.active_alerts()
        self.assertEqual([item["alertname"] for item in alerts], ["PodRestart"])
        self.assertIn("increase(", adapter.alert_rules()["PodRestart"]["query"])
        self.assertIn(("shop", "api-1"), adapter.pod_inventory({"shop"}))
        end = datetime(2026, 9, 20, tzinfo=timezone.utc)
        metrics = adapter.collect_pod_metrics("shop", "api-1", end - timedelta(minutes=5), end)
        self.assertEqual(len(metrics), 8)
        self.assertEqual(metrics[0]["labels"]["pod"], "api-1")

    def test_prometheus_target_discovery_keeps_dropped_and_down_distinct(self):
        adapter = PrometheusAdapter("http://prometheus")
        adapter.transport = FakeTransport(
            {
                "/api/v1/targets": {
                    "status": "success",
                    "data": {
                        "activeTargets": [
                            {
                                "health": "down",
                                "lastError": "dial tcp: connect: connection refused",
                                "scrapePool": "serviceMonitor/shop/api/0",
                                "labels": {"namespace": "shop", "pod": "api-1", "service": "api", "job": "api"},
                            },
                            {"health": "up", "labels": {"namespace": "other", "pod": "other-1"}},
                        ],
                        "droppedTargets": [
                            {
                                "discoveredLabels": {
                                    "__meta_kubernetes_namespace": "shop",
                                    "__meta_kubernetes_pod_name": "api-2",
                                    "__meta_kubernetes_service_name": "api",
                                }
                            }
                        ],
                    },
                }
            }
        )

        targets = adapter.scrape_targets("shop", {"api-1", "api-2"})

        self.assertEqual(targets["active"][0]["health"], "down")
        self.assertIn("connection refused", targets["active"][0]["last_error"])
        self.assertEqual(targets["dropped"][0]["state"], "dropped")
        self.assertEqual(targets["dropped"][0]["pod"], "api-2")

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

    def test_opensearch_adapter_preserves_bounded_structured_diagnostics(self):
        adapter = OpenSearchAdapter("http://opensearch")
        adapter.transport = FakeTransport(
            {
                "/_search": {
                    "hits": {
                        "hits": [
                            {
                                "_source": {
                                    "@timestamp": "2026-09-20T00:00:00Z",
                                    "message": "dependency request failed",
                                    "level": "error",
                                    "error": {"code": "ECONNREFUSED", "message": "password=do-not-keep"},
                                    "http": {"response": {"status_code": 503}},
                                    "request": {"id": "request-4821"},
                                    "authorization": "Bearer do-not-keep-this",
                                    "unrelated_payload": "do-not-retain-arbitrary-body",
                                    "kubernetes": {
                                        "namespace": "shop",
                                        "pod": {"name": "api-1"},
                                        "container": {"name": "api"},
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

        self.assertEqual(logs[0]["message"], "dependency request failed")
        fields = logs[0]["diagnostic_fields"]
        self.assertEqual(fields["error_code"], "ECONNREFUSED")
        self.assertEqual(fields["status_code"], "503")
        self.assertRegex(fields["request_id"], r"^<REF:[A-Z2-7]{10}>$")
        self.assertNotIn("request-4821", repr(logs))
        self.assertNotIn("do-not-keep", repr(logs))
        self.assertNotIn("do-not-retain-arbitrary-body", repr(logs))

    def test_opensearch_adapter_bounds_message_bytes_and_keeps_structured_diagnostics(self):
        adapter = OpenSearchAdapter("http://opensearch", max_message_bytes=512)
        adapter.transport = FakeTransport({
            "/_search": {
                "hits": {"hits": [{"_source": {
                    "@timestamp": "2026-09-20T00:00:00Z",
                    "message": "🙂" * 1000,
                    "error": {"code": "ECONNREFUSED"},
                    "kubernetes": {"namespace": "shop", "pod": {"name": "api-1"}},
                }}]}
            }
        })
        end = datetime(2026, 9, 20, tzinfo=timezone.utc)

        logs = adapter.collect_logs("shop", "api-1", end - timedelta(minutes=5), end)

        self.assertEqual(len(logs[0]["message"].encode("utf-8")), 512)
        self.assertTrue(logs[0]["message_truncated"])
        self.assertEqual(logs[0]["message_truncation_reasons"], ["message_byte_limit"])
        self.assertEqual(logs[0]["diagnostic_fields"]["error_code"], "ECONNREFUSED")
        self.assertTrue(adapter.last_collection_info["truncated"])

    def test_opensearch_adapter_bounds_aggregate_hits_and_reports_omissions(self):
        hits = [{"_id": str(index), "_source": {
            "@timestamp": f"2026-09-20T00:00:{index:02d}Z",
            "message": "x" * 512,
            "kubernetes": {"namespace": "shop", "pod": {"name": "api-1"}},
        }} for index in range(10)]
        adapter = OpenSearchAdapter("http://opensearch", max_message_bytes=512, max_collection_bytes=4096)
        adapter.transport = FakeTransport({"/_search": {"hits": {"hits": hits}}})
        end = datetime(2026, 9, 20, tzinfo=timezone.utc)

        logs, capture = adapter.collect_logs_with_info("shop", "api-1", end - timedelta(minutes=5), end, limit=10)

        self.assertLessEqual(capture["retained_compact_bytes"], 4096)
        self.assertEqual(capture["retained_hits"], len(logs))
        self.assertTrue(capture["truncated"])
        self.assertGreater(capture["collection_omitted_hits"], 0)
        self.assertTrue(any(item.get("message_truncated") for item in logs))

    def test_oversized_opensearch_segment_is_marked_unavailable_without_losing_other_segment(self):
        class PartiallyOversizedTransport:
            def __init__(self):
                self.requests = []

            def request(self, path, method="GET", body=None, max_response_bytes=None):
                self.requests.append((path, method, body, max_response_bytes))
                if body["sort"][0]["@timestamp"] == "desc":
                    raise ResponseTooLargeError("over byte limit")
                return {"hits": {"hits": [{"_source": {
                    "@timestamp": "2026-09-20T00:05:01Z", "message": "incident failure",
                    "kubernetes": {"namespace": "shop", "pod": {"name": "api-1"}},
                }}]}}

        adapter = OpenSearchAdapter("http://opensearch")
        adapter.transport = PartiallyOversizedTransport()
        start = datetime(2026, 9, 20, tzinfo=timezone.utc)

        logs, capture = adapter.collect_logs_with_info(
            "shop", "api-1", start, start + timedelta(minutes=10), limit=8, focus=start + timedelta(minutes=5)
        )

        self.assertEqual([item["message"] for item in logs], ["incident failure"])
        self.assertEqual(capture["status"], "partial")
        self.assertEqual(capture["unavailable_segments"], [{
            "segment": "baseline", "reason": "response_byte_limit", "limit_bytes": adapter.max_response_bytes,
        }])
        self.assertEqual(adapter.transport.requests[0][3], adapter.max_response_bytes)

    def test_opensearch_adapter_reserves_capacity_for_post_alert_logs(self):
        adapter = OpenSearchAdapter("http://opensearch")
        adapter.transport = FocusedLogTransport()
        start = datetime(2026, 9, 20, tzinfo=timezone.utc)
        focus = start + timedelta(minutes=5)

        logs = adapter.collect_logs("shop", "api-1", start, start + timedelta(minutes=10), limit=8, focus=focus)

        self.assertEqual([item["message"] for item in logs], [
            "inventory request completed",
            "max_connections reached; checkout rejected",
        ])
        self.assertEqual([request[2]["size"] for request in adapter.transport.requests], [2, 6])
        baseline_range = adapter.transport.requests[0][2]["query"]["bool"]["filter"][0]["range"]["@timestamp"]
        incident_range = adapter.transport.requests[1][2]["query"]["bool"]["filter"][0]["range"]["@timestamp"]
        self.assertEqual(baseline_range["lte"], "2026-09-20T00:05:00Z")
        self.assertEqual(incident_range["gte"], "2026-09-20T00:05:00Z")

    def test_literal_search_reserves_post_alert_capacity_too(self):
        adapter = OpenSearchAdapter("http://opensearch")
        adapter.transport = FocusedLogTransport()
        start = datetime(2026, 9, 20, tzinfo=timezone.utc)
        adapter.collect_logs("shop", "api-1", start, start + timedelta(minutes=10),
                             limit=8, focus=start + timedelta(minutes=5), terms=["connection"])
        self.assertEqual([item[2]["size"] for item in adapter.transport.requests], [2, 6])
        for _, _, body in adapter.transport.requests:
            self.assertEqual(body["query"]["bool"]["should"], [{"match_phrase": {"message": "connection"}}])

    def test_single_hit_focus_budget_keeps_the_incident_window(self):
        adapter = OpenSearchAdapter("http://opensearch")
        adapter.transport = FocusedLogTransport()
        start = datetime(2026, 9, 20, tzinfo=timezone.utc)

        logs = adapter.collect_logs(
            "shop", "api-1", start, start + timedelta(minutes=10), limit=1, focus=start + timedelta(minutes=5)
        )

        self.assertEqual([item["message"] for item in logs], ["max_connections reached; checkout rejected"])
        self.assertEqual(len(adapter.transport.requests), 1)
        self.assertEqual(adapter.transport.requests[0][2]["size"], 1)

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

    def test_kubernetes_monitoring_resources_keep_service_and_pod_selectors_separate(self):
        adapter = KubernetesAdapter("http://kubernetes")
        adapter.transport = FakeTransport(
            {
                "/apis/monitoring.coreos.com/v1/servicemonitors": {
                    "items": [{
                        "metadata": {"name": "api", "namespace": "monitoring", "resourceVersion": "12"},
                        "spec": {
                        "namespaceSelector": {"matchNames": ["shop"]},
                            "selector": {"matchLabels": {"metrics": "enabled"}, "matchExpressions": [
                                {"key": "tier", "operator": "In", "values": ["api", "worker"]},
                            ]},
                            "endpoints": [{"port": "metrics", "path": "/metrics", "interval": "30s"}],
                        },
                    }]
                },
                "/apis/monitoring.coreos.com/v1/podmonitors": {
                    "items": [{
                        "metadata": {"name": "worker", "namespace": "monitoring"},
                        "spec": {
                            "namespaceSelector": {"matchNames": ["shop"]},
                            "selector": {"matchLabels": {"metrics": "pod-enabled"}, "matchExpressions": [
                                {"key": "deprecated", "operator": "DoesNotExist", "values": []},
                            ]},
                            "podMetricsEndpoints": [{"port": "metrics"}],
                        },
                    }]
                },
            }
        )

        monitors = adapter.monitoring_resources({"shop"})

        self.assertEqual([item["kind"] for item in monitors], ["ServiceMonitor", "PodMonitor"])
        self.assertEqual(monitors[0]["match_labels"], {"metrics": "enabled"})
        self.assertEqual(monitors[1]["match_labels"], {"metrics": "pod-enabled"})
        self.assertEqual(monitors[0]["match_expressions"], [
            {"key": "tier", "operator": "In", "values": ["api", "worker"]},
        ])
        self.assertEqual(monitors[1]["match_expressions"], [
            {"key": "deprecated", "operator": "DoesNotExist", "values": []},
        ])
        self.assertEqual(monitors[0]["effective_namespaces"], ["shop"])
        self.assertEqual(monitors[0]["namespace_selector"]["status"], "resolved")
        self.assertTrue(monitors[0]["observed_at"].endswith("Z"))

    def test_monitor_namespace_any_is_scoped_to_requested_namespaces(self):
        adapter = KubernetesAdapter("http://kubernetes")
        adapter.transport = FakeTransport({
            "/apis/monitoring.coreos.com/v1/servicemonitors": {"items": [
                {"metadata": {"name": "global", "namespace": "monitoring"},
                 "spec": {"namespaceSelector": {"any": True}, "selector": {}}},
                {"metadata": {"name": "local-default", "namespace": "shop"},
                 "spec": {"selector": {"matchLabels": {"app": "api"}}}},
                {"metadata": {"name": "other-only", "namespace": "monitoring"},
                 "spec": {"namespaceSelector": {"matchNames": ["other"]}, "selector": {}}},
                {"metadata": {"name": "malformed-cross-ns", "namespace": "monitoring"},
                 "spec": {"namespaceSelector": {"any": "true"}}},
            ]},
            "/apis/monitoring.coreos.com/v1/podmonitors": {"items": []},
        })

        monitors = adapter.monitoring_resources({"shop"})

        self.assertEqual([item["name"] for item in monitors], ["global", "local-default", "malformed-cross-ns"])
        self.assertEqual(monitors[0]["effective_namespaces"], ["shop"])
        self.assertTrue(monitors[0]["namespace_selector"]["any"])
        self.assertEqual(monitors[1]["effective_namespaces"], ["shop"])
        self.assertTrue(monitors[1]["namespace_selector"]["defaults_to_monitor_namespace"])
        self.assertEqual(monitors[2]["effective_namespaces"], ["shop"])
        self.assertEqual(monitors[2]["namespace_selector"]["status"], "unknown")
        self.assertFalse(monitors[2]["selector_complete"])

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
                    "opensearch_max_message_bytes": 512,
                    "opensearch_max_collection_bytes": 5000,
                    "opensearch_max_response_bytes": 8192,
                    "enabled": True,
                    "auto_build_reports": True,
                }
            )
            self.assertEqual(config["namespaces"], ["platform", "shop"])
            self.assertEqual(config["opensearch_max_message_bytes"], 512)
            self.assertEqual(config["opensearch_max_collection_bytes"], 5000)
            self.assertEqual(config["opensearch_max_response_bytes"], 8192)
            _, logs, _ = coordinator.adapters(config)
            self.assertEqual(logs.max_message_bytes, 512)
            self.assertEqual(logs.max_collection_bytes, 5000)
            self.assertEqual(logs.max_response_bytes, 8192)
            self.assertTrue((state / "source-settings.json").is_file())
            with self.assertRaises(ValueError):
                coordinator.update_configuration({"prometheus_url": "prometheus:9090"})

    def test_live_case_persists_opensearch_coverage_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            coordinator = LiveSourceCoordinator(FCAPSuleStore(state / "state.db"), state)
            config = {
                "cluster_name": "cluster-a",
                "incident_window_minutes": 10,
                "opensearch_index": "logs-*",
                "prometheus_url": "http://prometheus:9090",
                "opensearch_url": "http://opensearch:9200",
            }
            prometheus, opensearch, kubernetes = Mock(), Mock(), Mock()
            prometheus.collect_alert_metrics.return_value = {"alert_evidence": [], "series": []}
            prometheus.collect_pod_metrics.return_value = []
            opensearch.collect_logs.return_value = []
            opensearch.last_collection_info = {
                "status": "partial", "available": True, "truncated": True,
                "message_truncated_hits": 1, "collection_omitted_hits": 0,
                "unavailable_segments": [],
            }
            kubernetes.configuration_snapshot.return_value = []
            alert = {"alertname": "PodRestart", "startsAt": "2026-09-20T00:05:00Z", "annotations": {}}
            pod = {"name": "api-1", "namespace": "shop", "workload": "payments"}

            case_dir = coordinator._capture_case(config, prometheus, opensearch, kubernetes, alert, pod, "incident-1")
            capture = json.loads((case_dir / "opensearch_logs.json").read_text(encoding="utf-8"))["capture"]

            self.assertEqual(capture["status"], "partial")
            self.assertTrue(capture["truncated"])

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
