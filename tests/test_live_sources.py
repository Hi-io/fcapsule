import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from fcapsule.adapters.kubernetes_adapter import KubernetesAdapter, KubernetesInventory
from fcapsule.adapters.opensearch_adapter import OpenSearchAdapter
from fcapsule.adapters.prometheus_adapter import PrometheusAdapter
from fcapsule.adapters.transport import ResponseTooLargeError
from fcapsule.live_sources import LiveSourceCoordinator, _incident_id, _resolve_alert_pod, _resolve_alert_scope
from fcapsule.adapters.grafana_webhook_adapter import normalize_notification
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


class PagedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, path, method="GET", body=None, max_response_bytes=None):
        self.requests.append((path, method, body, max_response_bytes))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


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
    def test_existing_prometheus_pod_incident_id_is_stable(self):
        alert = {"alertname": "PodRestart", "startsAt": "2026-09-25T05:00:00Z"}
        self.assertEqual(_incident_id(alert, "core", "pod:gw-1"), _incident_id(alert, "core", "gw-1"))
        self.assertNotEqual(_incident_id({**alert, "source": "grafana_webhook"}, "core", "pod:gw-1"),
                            _incident_id(alert, "core", "gw-1"))

    def test_group_identity_resolves_all_replicas_without_guessing_a_pod(self):
        mappings = [
            {"name": "CNFC", "alert_label": "cnfc", "pod_label": "telecom.example.com/cnfc"},
            {"name": "VNFC", "alert_label": "vnfc", "pod_label": "telecom.example.com/vnfc"},
        ]
        pods = [
            {"namespace": "core", "name": name, "workload": "gateway", "labels": {
                "telecom.example.com/cnfc": "edge-a", "telecom.example.com/vnfc": vnfc,
            }} for name, vnfc in (("gw-1", "blue"), ("gw-2", "blue"), ("gw-3", "green"))
        ]
        scope = _resolve_alert_scope(pods, "core", {"cnfc": "edge-a"}, mappings)
        self.assertEqual(scope["kind"], "cnfc")
        self.assertEqual([item["name"] for item in scope["pods"]], ["gw-1", "gw-2", "gw-3"])
        narrower = _resolve_alert_scope(pods, "core", {"cnfc": "edge-a", "vnfc": "blue"}, mappings)
        self.assertEqual([item["name"] for item in narrower["pods"]], ["gw-1", "gw-2"])
        exact = _resolve_alert_scope(pods, "core", {"pod": "gw-3", "cnfc": "edge-a"}, mappings)
        self.assertEqual(exact["kind"], "pod")
        self.assertEqual([item["name"] for item in exact["pods"]], ["gw-3"])
        pods[1]["uid"] = "uid-gw-2"
        by_uid = _resolve_alert_scope(pods, "core", {"pod_uid": "uid-gw-2", "cnfc": "edge-a"}, mappings)
        self.assertEqual([item["name"] for item in by_uid["pods"]], ["gw-2"])
        self.assertIsNone(_resolve_alert_scope(pods, "core", {"cnfc": "missing"}, mappings))
        other = {**pods[0], "namespace": "other", "name": "other-gw"}
        self.assertIsNone(_resolve_alert_scope(pods + [other], "", {"cnfc": "edge-a"}, mappings))

    def test_grafana_webhook_retains_firing_then_resolves(self):
        payload = {"status": "firing", "commonLabels": {"namespace": "core"}, "alerts": [{
            "status": "firing", "fingerprint": "fingerprint-1", "startsAt": "2026-09-25T05:00:00Z",
            "labels": {"alertname": "GatewayErrors", "cnfc": "edge-a"},
            "annotations": {"summary": "Gateway error rate increased"},
        }]}
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"FCAPSULE_GRAFANA_WEBHOOK_TOKEN": "test-token"}):
            state = Path(directory)
            coordinator = LiveSourceCoordinator(FCAPSuleStore(state / "state.db"), state)
            coordinator.update_configuration({"grafana_webhook_enabled": True})
            self.assertEqual(coordinator.receive_grafana_alerts(payload)["firing"], 1)
            self.assertEqual(next(iter(coordinator._grafana_alerts().values()))["labels"]["namespace"], "core")
            payload["alerts"][0]["status"] = "resolved"
            self.assertEqual(coordinator.receive_grafana_alerts(payload)["firing"], 0)
            payload["alerts"][0]["status"] = "firing"
            self.assertEqual(coordinator.receive_grafana_alerts(payload)["firing"], 1)
            coordinator.update_configuration({"grafana_webhook_enabled": False})
            self.assertEqual(coordinator._grafana_alerts(), {})
        payload["alerts"][0]["status"] = "resolved"
        self.assertEqual(normalize_notification(payload)[0][1], None)

    def test_group_capture_includes_metrics_logs_and_config_for_each_member(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            coordinator = LiveSourceCoordinator(FCAPSuleStore(state / "state.db"), state)
            config = {"cluster_name": "cluster-a", "incident_window_minutes": 10, "opensearch_index": "logs-*"}
            pods = [
                {"name": f"gw-{number}", "namespace": "core", "workload": "gateway",
                 "node": f"worker-{number}", "ready": number != 2}
                for number in range(1, 4)
            ]
            scope = {"kind": "cnfc", "name": "edge-a", "pods": pods, "identifiers": [
                {"name": "CNFC", "alert_label": "cnfc", "pod_label": "cnfc", "value": "edge-a"}
            ]}
            prometheus, opensearch, kubernetes = Mock(), Mock(), Mock()
            prometheus.collect_alert_metrics.return_value = {"alert_evidence": {}, "series": []}
            prometheus.collect_pod_metrics.return_value = []
            opensearch.collect_logs.return_value = []
            kubernetes.configuration_snapshot.return_value = []
            alert = {"alertname": "GatewayErrors", "startsAt": "2026-09-25T05:00:00Z",
                     "status": "firing", "severity": "warning", "labels": {"cnfc": "edge-a"}, "annotations": {}}
            path = coordinator._capture_case(config, prometheus, opensearch, kubernetes, alert, pods[0], "incident-group", scope=scope)
            metadata = __import__("yaml").safe_load((path / "metadata.yaml").read_text())
            self.assertIsNone(metadata["pod"])
            self.assertEqual(metadata["resource_scope"]["captured_pods"], 3)
            self.assertEqual({item["node"] for item in metadata["topology"]}, {"worker-1", "worker-2", "worker-3"})
            self.assertEqual(prometheus.collect_pod_metrics.call_count, 3)
            self.assertEqual(opensearch.collect_logs.call_count, 3)
            self.assertEqual(kubernetes.configuration_snapshot.call_count, 3)
            crowded = pods + [
                {"name": f"gw-{number}", "namespace": "core", "workload": "gateway",
                 "node": f"worker-{number}", "ready": number != 5}
                for number in (4, 5)
            ]
            crowded_scope = {**scope, "pods": crowded}
            crowded_path = coordinator._capture_case(config, prometheus, opensearch, kubernetes, alert,
                                                      pods[0], "incident-crowded", scope=crowded_scope)
            crowded_meta = __import__("yaml").safe_load((crowded_path / "metadata.yaml").read_text())
            self.assertEqual(crowded_meta["resource_scope"]["omitted_pods"], 1)
            self.assertEqual(crowded_meta["topology"][0]["name"], "gw-2")
            self.assertEqual(crowded_meta["topology"][1]["name"], "gw-5")

    def test_synchronize_routes_cnfc_only_alert_to_group_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            coordinator = LiveSourceCoordinator(FCAPSuleStore(state / "state.db"), state)
            coordinator.update_configuration({"namespaces": ["core"]})
            pods = [{"namespace": "core", "name": f"gw-{number}", "workload": "gateway",
                     "ready": number != 2, "phase": "Running", "labels": {"cnfc": "edge-a"}}
                    for number in range(1, 4)]
            prometheus, opensearch, kubernetes = Mock(), Mock(), Mock()
            coordinator.adapters = Mock(return_value=(prometheus, opensearch, kubernetes))
            coordinator.test_connections = Mock(return_value={"targets": {
                "prometheus": {"ok": True}, "opensearch": {"ok": True}, "kubernetes": {"ok": True}}})
            kubernetes.list_pods.return_value = pods
            prometheus.pod_inventory.return_value = {}
            opensearch.pod_log_counts.return_value = {}
            prometheus.alert_rules.return_value = {}
            prometheus.active_alerts.return_value = [{"alertname": "GatewayErrors", "status": "firing",
                "startsAt": "2026-09-25T05:00:00Z", "labels": {"namespace": "core", "cnfc": "edge-a"}}]
            with patch.object(coordinator, "_capture_case", return_value=state / "case") as capture:
                result = coordinator.synchronize()
            self.assertEqual(len(result["captured"]), 1)
            self.assertEqual(capture.call_args.kwargs["scope"]["kind"], "cnfc")
            self.assertEqual(len(capture.call_args.kwargs["scope"]["pods"]), 3)
            self.assertEqual(result["unmapped_alerts"], [])

    def test_cross_workload_identity_is_not_presented_as_a_discovered_application(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            store = FCAPSuleStore(state / "state.db")
            coordinator = LiveSourceCoordinator(store, state)
            coordinator.update_configuration({"namespaces": ["core"]})
            pods = [{"namespace": "core", "name": "gw-1", "workload": "gateway", "ready": True,
                     "labels": {"cnfc": "edge-a"}},
                    {"namespace": "core", "name": "worker-1", "workload": "worker", "ready": True,
                     "labels": {"cnfc": "edge-a"}}]
            prometheus, opensearch, kubernetes = Mock(), Mock(), Mock()
            coordinator.adapters = Mock(return_value=(prometheus, opensearch, kubernetes))
            coordinator.test_connections = Mock(return_value={"targets": {
                "prometheus": {"ok": True}, "opensearch": {"ok": True}, "kubernetes": {"ok": True}}})
            kubernetes.list_pods.return_value = pods
            prometheus.pod_inventory.return_value = {}
            opensearch.pod_log_counts.return_value = {}
            prometheus.alert_rules.return_value = {}
            prometheus.active_alerts.return_value = [{"alertname": "CNFCDegraded", "status": "firing",
                "startsAt": "2026-09-25T05:00:00Z", "labels": {"namespace": "core", "cnfc": "edge-a"}}]
            with patch.object(coordinator, "_capture_case", return_value=state / "case"):
                result = coordinator.synchronize()
            captured = result["captured"][0]
            self.assertEqual(captured["app_name"], "CNFC edge-a")
            self.assertEqual(store.get_application(captured["app_id"])["status"], "not_observed")

    def test_synchronize_does_not_treat_unavailable_pod_inventory_as_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            coordinator = LiveSourceCoordinator(Mock(), state)
            kubernetes = Mock()
            kubernetes.list_pods.return_value = KubernetesInventory(
                status="unavailable", complete=False, has_more=None, reason="request_failed",
            )
            coordinator.configuration = Mock(return_value={"namespaces": ["shop"]})
            coordinator.adapters = Mock(return_value=(Mock(), Mock(), kubernetes))
            coordinator.test_connections = Mock(
                return_value={"targets": {"kubernetes": {"ok": True}}},
            )

            with self.assertRaisesRegex(RuntimeError, "pod inventory is unavailable"):
                coordinator.synchronize()

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

    def test_aggregate_budget_retains_incident_signal_before_recent_baseline(self):
        class CrowdedBaselineTransport:
            def request(self, path, method="GET", body=None, max_response_bytes=None):
                order = body["sort"][0]["@timestamp"]
                if order == "desc":
                    return {"hits": {"hits": [{
                        "_id": f"baseline-{index}",
                        "_source": {
                            "@timestamp": f"2026-09-20T00:04:{index:02d}Z",
                            "message": "baseline evidence " + "x" * 500,
                            "kubernetes": {"namespace": "shop", "pod": {"name": "api-1"}},
                        },
                    } for index in range(20)]}}
                return {"hits": {"hits": [{
                    "_id": "incident-failure",
                    "_source": {
                        "@timestamp": "2026-09-20T00:05:01Z",
                        "message": "incident failure ECONNREFUSED",
                        "error": {"code": "ECONNREFUSED"},
                        "kubernetes": {"namespace": "shop", "pod": {"name": "api-1"}},
                    },
                }]}}

        adapter = OpenSearchAdapter("http://opensearch", max_collection_bytes=4096)
        adapter.transport = CrowdedBaselineTransport()
        start = datetime(2026, 9, 20, tzinfo=timezone.utc)

        logs, capture = adapter.collect_logs_with_info(
            "shop", "api-1", start, start + timedelta(minutes=10), limit=200, focus=start + timedelta(minutes=5)
        )

        self.assertIn("incident failure ECONNREFUSED", [item["message"] for item in logs])
        self.assertTrue(any(item["message"].startswith("baseline evidence") for item in logs))
        self.assertEqual(logs, sorted(logs, key=lambda item: item["@timestamp"]))
        self.assertLessEqual(capture["retained_compact_bytes"], adapter.max_collection_bytes)

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
                            "podMetricsEndpoints": [{"port": "metrics", "portNumber": 9104}],
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
        self.assertEqual(monitors[1]["endpoints"], [{"port": "metrics", "portNumber": 9104}])
        self.assertEqual(monitors[0]["effective_namespaces"], ["shop"])
        self.assertEqual(monitors[0]["namespace_selector"]["status"], "resolved")
        self.assertTrue(monitors[0]["observed_at"].endswith("Z"))

    def test_endpoint_slice_inventory_is_namespace_and_service_scoped_without_addresses(self):
        adapter = KubernetesAdapter("http://kubernetes")
        adapter.transport = FakeTransport({
            "/apis/discovery.k8s.io/v1/namespaces/shop/endpointslices": {"metadata": {}, "items": [
                {"metadata": {"name": "mysql-a", "labels": {"kubernetes.io/service-name": "mysql-exporter"}},
                 "addressType": "IPv4", "ports": [{"name": "metrics", "port": 9104, "protocol": "TCP"}],
                 "endpoints": [{"addresses": ["10.10.0.7"], "conditions": {"ready": False, "serving": False},
                                "targetRef": {"kind": "Pod", "name": "mysql-exporter-0", "namespace": "shop"}}]},
                {"metadata": {"name": "unrelated", "labels": {"kubernetes.io/service-name": "other"}},
                 "endpoints": []},
            ]}
        })

        inventory = adapter.list_endpoint_slices("shop", {"mysql-exporter"})

        self.assertEqual(len(inventory["slices"]), 1)
        endpoint_slice = inventory["slices"][0]
        self.assertEqual(endpoint_slice["service"], "mysql-exporter")
        self.assertEqual(endpoint_slice["ports"], [{"name": "metrics", "port": 9104, "protocol": "TCP"}])
        self.assertEqual(endpoint_slice["endpoints"][0]["target_ref"], {
            "kind": "Pod", "name": "mysql-exporter-0", "namespace": "shop"})
        self.assertFalse(endpoint_slice["endpoints"][0]["ready"])
        self.assertNotIn("addresses", endpoint_slice["endpoints"][0])
        self.assertTrue(inventory["observed_at"].endswith("Z"))
        self.assertIn("/namespaces/shop/endpointslices", adapter.transport.requests[0][0])

    def test_endpoint_slice_list_paginates_with_service_scope_and_byte_budget(self):
        adapter = KubernetesAdapter("http://kubernetes")
        adapter.transport = PagedTransport([
            {"metadata": {"continue": "opaque cursor"}, "items": [
                {"metadata": {"name": "mysql-a", "labels": {"kubernetes.io/service-name": "mysql-exporter"}},
                 "endpoints": [], "ports": []},
            ]},
            {"metadata": {}, "items": [
                {"metadata": {"name": "mysql-b", "labels": {"kubernetes.io/service-name": "mysql-exporter"}},
                 "endpoints": [], "ports": []},
            ]},
        ])

        inventory = adapter.list_endpoint_slices("shop", {"mysql-exporter"})

        self.assertEqual(inventory["status"], "observed")
        self.assertTrue(inventory["complete"])
        self.assertEqual([item["name"] for item in inventory["slices"]], ["mysql-a", "mysql-b"])
        self.assertEqual(len(adapter.transport.requests), 2)
        first_query = parse_qs(urlsplit(adapter.transport.requests[0][0]).query)
        second_query = parse_qs(urlsplit(adapter.transport.requests[1][0]).query)
        self.assertEqual(first_query["limit"], ["50"])
        self.assertEqual(first_query["labelSelector"], ["kubernetes.io/service-name in (mysql-exporter)"])
        self.assertEqual(second_query["continue"], ["opaque cursor"])
        self.assertEqual([request[3] for request in adapter.transport.requests], [1024 * 1024] * 2)

    def test_endpoint_slice_page_cap_returns_useful_partial_discovery(self):
        adapter = KubernetesAdapter("http://kubernetes")
        adapter.transport = PagedTransport([
            {"metadata": {"continue": "next"}, "items": [
                {"metadata": {"name": "mysql-a", "labels": {"kubernetes.io/service-name": "mysql-exporter"}},
                 "endpoints": [], "ports": []},
            ]},
            {"metadata": {}, "items": []},
        ])

        with patch("fcapsule.adapters.kubernetes_adapter.MAX_ENDPOINT_SLICE_PAGES", 1):
            inventory = adapter.list_endpoint_slices("shop", {"mysql-exporter"})

        self.assertEqual(inventory["status"], "partial")
        self.assertFalse(inventory["complete"])
        self.assertTrue(inventory["has_more"])
        self.assertEqual([item["name"] for item in inventory["slices"]], ["mysql-a"])
        self.assertEqual(len(adapter.transport.requests), 1)

    def test_oversized_endpoint_slice_page_is_explicitly_unavailable(self):
        adapter = KubernetesAdapter("http://kubernetes")
        adapter.transport = PagedTransport([ResponseTooLargeError("too large")])

        inventory = adapter.list_endpoint_slices("shop", {"mysql-exporter"})

        self.assertEqual(inventory["status"], "unavailable")
        self.assertFalse(inventory["complete"])
        self.assertEqual(inventory["reason"], "response_too_large")
        self.assertEqual(inventory["slices"], [])
        self.assertEqual(adapter.transport.requests[0][3], 1024 * 1024)

    def test_later_endpoint_slice_page_failure_preserves_partial_results(self):
        adapter = KubernetesAdapter("http://kubernetes")
        adapter.transport = PagedTransport([
            {"metadata": {"continue": "next"}, "items": [
                {"metadata": {"name": "mysql-a", "labels": {"kubernetes.io/service-name": "mysql-exporter"}},
                 "endpoints": [], "ports": []},
            ]},
            ResponseTooLargeError("too large"),
        ])

        inventory = adapter.list_endpoint_slices("shop", {"mysql-exporter"})

        self.assertEqual(inventory["status"], "partial")
        self.assertFalse(inventory["complete"])
        self.assertTrue(inventory["has_more"])
        self.assertEqual(inventory["reason"], "page_unavailable")
        self.assertEqual([item["name"] for item in inventory["slices"]], ["mysql-a"])

    def test_pod_inventory_distinguishes_not_ready_from_unreported_readiness(self):
        adapter = KubernetesAdapter("http://kubernetes")
        adapter.transport = FakeTransport({"/api/v1/namespaces/shop/pods": {"metadata": {}, "items": [
            {"metadata": {"name": "starting", "namespace": "shop"}, "status": {"phase": "Running", "conditions": [
                {"type": "Ready", "status": "False"}]}},
            {"metadata": {"name": "missing-condition", "namespace": "shop"}, "status": {"phase": "Pending", "conditions": []}},
        ]}})

        pods = {item["name"]: item for item in adapter.list_pods({"shop"})}

        self.assertFalse(pods["starting"]["ready"])
        self.assertEqual(pods["starting"]["ready_status"], "false")
        self.assertFalse(pods["missing-condition"]["ready"])
        self.assertEqual(pods["missing-condition"]["ready_status"], "unknown")

    def test_pod_inventory_is_namespaced_paginated_and_bounded(self):
        adapter = KubernetesAdapter("http://kubernetes")
        adapter.transport = PagedTransport([
            {"metadata": {"continue": "opaque cursor"}, "items": [
                {"metadata": {"name": "starting", "namespace": "shop"}, "status": {"phase": "Running"}},
            ]},
            {"metadata": {}, "items": [
                {"metadata": {"name": "other", "namespace": "shop"}, "status": {"phase": "Pending"}},
            ]},
        ])

        pods = adapter.list_pods({"shop"})

        self.assertIsInstance(pods, list)
        self.assertEqual(pods.status, "observed")
        self.assertTrue(pods.complete)
        self.assertEqual([item["name"] for item in pods], ["starting", "other"])
        self.assertEqual(len(adapter.transport.requests), 2)
        first_path, second_path = (request[0] for request in adapter.transport.requests)
        self.assertTrue(first_path.startswith("/api/v1/namespaces/shop/pods?"))
        self.assertTrue(second_path.startswith("/api/v1/namespaces/shop/pods?"))
        self.assertEqual(parse_qs(urlsplit(first_path).query)["limit"], ["50"])
        self.assertEqual(parse_qs(urlsplit(second_path).query)["continue"], ["opaque cursor"])
        self.assertEqual([request[3] for request in adapter.transport.requests], [1024 * 1024] * 2)

    def test_pod_inventory_exposes_unavailable_and_partial_states_without_breaking_list_callers(self):
        adapter = KubernetesAdapter("http://kubernetes")
        adapter.transport = PagedTransport([ResponseTooLargeError("too large")])

        unavailable = adapter.list_pods({"shop"})

        self.assertIsInstance(unavailable, list)
        self.assertEqual(unavailable.status, "unavailable")
        self.assertFalse(unavailable.complete)
        self.assertEqual(unavailable.reason, "response_too_large")
        self.assertEqual(unavailable, [])

        adapter.transport = PagedTransport([
            {"metadata": {"continue": "next"}, "items": [
                {"metadata": {"name": "starting", "namespace": "shop"}, "status": {"phase": "Running"}},
            ]},
            RuntimeError("page unavailable"),
        ])
        partial = adapter.list_pods({"shop"})

        self.assertEqual(partial.status, "partial")
        self.assertFalse(partial.complete)
        self.assertTrue(partial.has_more)
        self.assertEqual(partial.reason, "page_unavailable")
        self.assertEqual([item["name"] for item in partial], ["starting"])

    def test_pod_inventory_page_cap_is_explicitly_partial(self):
        adapter = KubernetesAdapter("http://kubernetes")
        adapter.transport = PagedTransport([
            {"metadata": {"continue": "next"}, "items": [
                {"metadata": {"name": "starting", "namespace": "shop"}, "status": {"phase": "Running"}},
            ]},
            {"metadata": {}, "items": []},
        ])

        with patch("fcapsule.adapters.kubernetes_adapter.MAX_POD_PAGES", 1):
            pods = adapter.list_pods({"shop"})

        self.assertEqual(pods.status, "partial")
        self.assertFalse(pods.complete)
        self.assertTrue(pods.has_more)
        self.assertEqual([item["name"] for item in pods], ["starting"])
        self.assertEqual(len(adapter.transport.requests), 1)

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
            prometheus.last_pod_metric_capture_info = {
                "status": "partial", "available": True, "truncated": True,
                "response_byte_limit_count": 1, "response_limited_metrics": ["pod_cpu_cores"],
                "response_limit_bytes": 1024 * 1024, "retained_series": 0,
            }
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
            metric_capture = json.loads((case_dir / "prometheus_metrics.json").read_text(encoding="utf-8"))["pod_metric_capture"]

            self.assertEqual(capture["status"], "partial")
            self.assertTrue(capture["truncated"])
            self.assertEqual(metric_capture["status"], "partial")
            self.assertEqual(metric_capture["response_limited_metrics"], ["pod_cpu_cores"])

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
