import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

from fcapsule.adapters.prometheus_adapter import (
    MAX_PROMETHEUS_DEFAULT_RESPONSE_BYTES,
    MAX_PROMETHEUS_DISCOVERY_RESPONSE_BYTES,
    MAX_PROMETHEUS_QUERY_RESPONSE_BYTES,
    MAX_PROMETHEUS_RANGE_RESPONSE_BYTES,
    MAX_PROMETHEUS_SCOPED_TARGET_RESPONSE_BYTES,
    MAX_PROMETHEUS_STATUS_RESPONSE_BYTES,
    PrometheusAdapter,
)
from fcapsule.adapters.transport import ResponseTooLargeError


START = datetime(2026, 9, 24, tzinfo=timezone.utc)


class RecordingTransport:
    def __init__(self, responder):
        self.responder = responder
        self.requests = []

    def request(self, path, method="GET", body=None, max_response_bytes=None):
        self.requests.append((path, method, body, max_response_bytes))
        return self.responder(path, method, body, max_response_bytes)


def alert(query):
    labels = {"namespace": "shop", "pod": "orders-1", "service": "orders"}
    return {
        "alertname": "CheckoutLatencyHigh",
        "startsAt": "2026-09-24T00:00:00Z",
        "labels": labels,
        "rule": {
            "name": "CheckoutLatencyHigh",
            "query": query,
            "duration": 20,
            "labels": {"service": "orders"},
        },
    }


def matrix_result(metric, first=0.5, second=0.6):
    return {
        "metric": metric,
        "values": [[START.timestamp(), str(first)], [START.timestamp() + 15, str(second)]],
    }


class PrometheusResponseBoundTests(unittest.TestCase):
    def test_api_passes_endpoint_specific_response_limits(self):
        def responder(path, method, body, max_bytes):
            endpoint = path.split("?", 1)[0]
            if endpoint == "/api/v1/status/buildinfo":
                return {"status": "success", "data": {"version": "test"}}
            if endpoint == "/api/v1/targets":
                return {"status": "success", "data": {"activeTargets": []}}
            return {"status": "success", "data": {}}

        adapter = PrometheusAdapter("http://prometheus")
        transport = RecordingTransport(responder)
        adapter.transport = transport
        for path, expected in (
            ("/api/v1/query", MAX_PROMETHEUS_QUERY_RESPONSE_BYTES),
            ("/api/v1/query_range", MAX_PROMETHEUS_RANGE_RESPONSE_BYTES),
            ("/api/v1/alerts", MAX_PROMETHEUS_DISCOVERY_RESPONSE_BYTES),
            ("/api/v1/targets", MAX_PROMETHEUS_DISCOVERY_RESPONSE_BYTES),
            ("/api/v1/rules", MAX_PROMETHEUS_DISCOVERY_RESPONSE_BYTES),
            ("/api/v1/status/buildinfo", MAX_PROMETHEUS_STATUS_RESPONSE_BYTES),
            ("/api/v1/unknown", MAX_PROMETHEUS_DEFAULT_RESPONSE_BYTES),
        ):
            with self.subTest(path=path):
                adapter._api(path)
                self.assertEqual(transport.requests[-1][3], expected)

        adapter.test_connection()
        self.assertEqual(transport.requests[-2][3], MAX_PROMETHEUS_STATUS_RESPONSE_BYTES)
        self.assertEqual(transport.requests[-1][3], MAX_PROMETHEUS_DISCOVERY_RESPONSE_BYTES)

    def test_oversized_live_alert_discovery_is_not_returned_as_empty(self):
        adapter = PrometheusAdapter("http://prometheus")
        adapter.transport = RecordingTransport(
            lambda *args: (_ for _ in ()).throw(ResponseTooLargeError("over byte limit"))
        )

        with self.assertRaises(ResponseTooLargeError):
            adapter.active_alerts()
        self.assertEqual(adapter.transport.requests[0][3], MAX_PROMETHEUS_DISCOVERY_RESPONSE_BYTES)

    def test_scoped_target_discovery_uses_server_pool_filter_and_keeps_partial_error_detail(self):
        first_pool = "serviceMonitor/fcapsule-lab/fcapsule-lab-mysql/0"
        oversized_pool = "serviceMonitor/fcapsule-lab/other/0"

        def responder(path, method, body, max_bytes):
            params = {key: values[0] for key, values in
                      parse_qs(path.split("?", 1)[1]).items()}
            if params["scrapePool"] == oversized_pool:
                raise ResponseTooLargeError("over byte limit")
            return {"status": "success", "data": {"activeTargets": [{
                "health": "down",
                "lastError": "server returned HTTP status 404 Not Found",
                "scrapePool": first_pool,
                "scrapeUrl": "http://user:password@mysql-exporter:9104/custom/metrics?token=private",
                "discoveredLabels": {"__metrics_path__": "/metrics"},
                "labels": {"namespace": "fcapsule-lab", "pod": "mysql-exporter-0",
                           "service": "mysql-exporter", "job": "mysql-exporter"},
            }], "droppedTargets": []}}

        adapter = PrometheusAdapter("http://prometheus")
        transport = RecordingTransport(responder)
        adapter.transport = transport

        targets = adapter.scrape_targets(
            "fcapsule-lab", scrape_pools=[first_pool, oversized_pool],
        )

        self.assertEqual(len(transport.requests), 2)
        self.assertTrue(all("scrapePool=" in request[0] for request in transport.requests))
        self.assertTrue(all("state=any" in request[0] for request in transport.requests))
        self.assertTrue(all(request[3] == MAX_PROMETHEUS_SCOPED_TARGET_RESPONSE_BYTES
                            for request in transport.requests))
        target = targets["active"][0]
        self.assertEqual(target["health"], "down")
        self.assertEqual(target["last_error"], "server returned HTTP status 404 Not Found")
        self.assertEqual(target["scrape_pool"], first_pool)
        self.assertEqual(target["scrape_endpoint"], "mysql-exporter:9104")
        self.assertEqual(target["scrape_path"], "/custom/metrics")
        self.assertNotIn("password", repr(target))
        self.assertNotIn("token", repr(target))
        self.assertEqual(targets["inventory"]["status"], "partial")
        self.assertFalse(targets["inventory"]["complete"])
        self.assertEqual(targets["inventory"]["response_limited_pools"], 1)

    def test_missing_scoped_target_pools_are_unavailable_without_a_global_fetch(self):
        adapter = PrometheusAdapter("http://prometheus")
        transport = RecordingTransport(lambda *args: self.fail("must not request the global target list"))
        adapter.transport = transport

        targets = adapter.scrape_targets("shop", scrape_pools=[])

        self.assertEqual(transport.requests, [])
        self.assertEqual(targets["inventory"]["status"], "unavailable")
        self.assertEqual(targets["inventory"]["reason"], "no_relevant_scrape_pools")

    def test_oversized_primary_range_is_explicitly_unavailable(self):
        adapter = PrometheusAdapter("http://prometheus")
        adapter.transport = RecordingTransport(
            lambda *args: (_ for _ in ()).throw(ResponseTooLargeError("over byte limit"))
        )
        query = 'orders_checkout_latency_p95_seconds{namespace="shop",service="orders"} > 0.25'

        captured = adapter.collect_alert_metrics(
            alert(query), "shop", "orders-1", START, START + timedelta(minutes=1),
        )

        self.assertEqual(captured["alert_evidence"]["status"], "unavailable")
        self.assertEqual(captured["alert_evidence"]["reason"], "response_byte_limit")
        self.assertEqual(captured["alert_evidence"]["source"]["response_limit_bytes"],
                         MAX_PROMETHEUS_RANGE_RESPONSE_BYTES)
        self.assertEqual(captured["series"], [])
        source = captured["alert_evidence"]["source_metric_capture"]
        self.assertEqual(source["status"], "unavailable")
        self.assertEqual(source["reason"], "response_byte_limit")
        self.assertEqual(source["response_byte_limit_count"], 1)

    def test_primary_overflow_can_still_retain_bounded_rule_operands(self):
        calls = []

        def responder(path, method, body, max_bytes):
            calls.append(path)
            if len(calls) == 1:
                raise ResponseTooLargeError("over byte limit")
            return {"status": "success", "data": {"resultType": "matrix", "result": [
                matrix_result({
                    "__name__": "orders_checkout_sample_count",
                    "namespace": "shop", "pod": "orders-1", "service": "orders",
                }, 10, 12),
            ]}}

        adapter = PrometheusAdapter("http://prometheus")
        adapter.transport = RecordingTransport(responder)
        query = (
            '(orders_checkout_latency_p95_seconds{namespace="shop",service="orders"} > 0.25) '
            'and on (namespace,pod,service) '
            '(orders_checkout_sample_count{namespace="shop",service="orders"} >= 10) '
            'and on (namespace,pod,service) (up{namespace="shop",service="orders"} == 1)'
        )

        captured = adapter.collect_alert_metrics(
            alert(query), "shop", "orders-1", START, START + timedelta(minutes=1),
        )

        self.assertEqual(captured["alert_evidence"]["status"], "unavailable")
        self.assertEqual(captured["alert_evidence"]["reason"], "response_byte_limit")
        self.assertEqual(captured["alert_evidence"]["source_metric_capture"]["status"], "available")
        self.assertEqual([item["metric"] for item in captured["series"]], ["orders_checkout_sample_count"])

    def test_oversized_source_query_is_explicitly_unavailable(self):
        calls = []

        def responder(path, method, body, max_bytes):
            calls.append(path)
            if len(calls) == 1:
                return {"status": "success", "data": {"resultType": "matrix", "result": [
                    matrix_result({"namespace": "shop", "pod": "orders-1", "service": "orders"}),
                ]}}
            raise ResponseTooLargeError("over byte limit")

        adapter = PrometheusAdapter("http://prometheus")
        adapter.transport = RecordingTransport(responder)
        query = (
            '(orders_checkout_latency_p95_seconds{namespace="shop",service="orders"} > 0.25) '
            'and on (namespace,pod,service) '
            '(orders_checkout_sample_count{namespace="shop",service="orders"} >= 10) '
            'and on (namespace,pod,service) '
            '(time() - orders_checkout_latest_sample_timestamp_seconds{namespace="shop",service="orders"} < 30) '
            'and on (namespace,pod,service) (up{namespace="shop",service="orders"} == 1)'
        )

        captured = adapter.collect_alert_metrics(
            alert(query), "shop", "orders-1", START, START + timedelta(minutes=1),
        )

        source = captured["alert_evidence"]["source_metric_capture"]
        self.assertEqual(captured["alert_evidence"]["status"], "available")
        self.assertEqual(source["status"], "unavailable")
        self.assertEqual(source["reason"], "response_byte_limit")
        self.assertEqual(source["response_byte_limit_count"], 1)
        self.assertEqual(source["response_limit_bytes"], MAX_PROMETHEUS_RANGE_RESPONSE_BYTES)
        self.assertEqual(len(captured["series"]), 1)

    def test_source_capture_is_partial_when_one_group_exceeds_the_limit(self):
        requests = []

        def responder(path, method, body, max_bytes):
            requests.append(path)
            if len(requests) == 1:
                return {"status": "success", "data": {"resultType": "matrix", "result": [
                    matrix_result({"namespace": "shop", "pod": "orders-1", "service": "orders"}),
                ]}}
            if len(requests) == 2:
                return {"status": "success", "data": {"resultType": "matrix", "result": [
                    matrix_result({"__name__": "orders_checkout_sample_count", "namespace": "shop", "pod": "orders-1", "service": "orders"}, 10, 12),
                ]}}
            raise ResponseTooLargeError("over byte limit")

        adapter = PrometheusAdapter("http://prometheus")
        adapter.transport = RecordingTransport(responder)
        query = (
            '(orders_checkout_latency_p95_seconds{namespace="shop",service="orders"} > 0.25) '
            'and on (namespace,pod,service) '
            '(orders_checkout_sample_count{namespace="shop",service="orders"} >= 10) '
            'and on (namespace,pod,service) '
            '(orders_inventory_timeout_total{namespace="shop",service="orders",kind="slow"} > 0) '
            'and on (namespace,pod,service) (up{namespace="shop",service="orders"} == 1)'
        )

        captured = adapter.collect_alert_metrics(
            alert(query), "shop", "orders-1", START, START + timedelta(minutes=1),
        )

        source = captured["alert_evidence"]["source_metric_capture"]
        retained = [item for item in captured["series"] if item["signal_origin"] == "alert_rule_source"]
        self.assertEqual(source["status"], "partial")
        self.assertEqual(source["reason"], "response_byte_limit")
        self.assertEqual(source["response_byte_limit_count"], 1)
        self.assertEqual([item["metric"] for item in retained], ["orders_checkout_sample_count"])

    def test_pod_metric_range_overflow_records_partial_capture_without_zero_series(self):
        adapter = PrometheusAdapter("http://prometheus")
        calls = []

        def query_range(*args, **kwargs):
            metric = len(calls)
            calls.append(metric)
            if metric == 0:
                raise ResponseTooLargeError("over byte limit")
            return []

        adapter.query_range = query_range
        series = adapter.collect_pod_metrics(
            "shop", "orders-1", START, START + timedelta(minutes=5),
        )

        info = adapter.last_pod_metric_capture_info
        self.assertEqual(series, [])
        self.assertEqual(info["status"], "partial")
        self.assertTrue(info["available"])
        self.assertTrue(info["truncated"])
        self.assertEqual(info["response_limited_metrics"], ["pod_cpu_cores"])
        self.assertEqual(info["reason"], "response_byte_limit")
        self.assertEqual(info["response_limit_bytes"], MAX_PROMETHEUS_RANGE_RESPONSE_BYTES)


if __name__ == "__main__":
    unittest.main()
