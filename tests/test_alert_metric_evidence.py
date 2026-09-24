import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import promql_parser as promql

from fcapsule.adapters.alert_expression import UnavailableExpression, plan_alert_expression
from fcapsule.adapters.prometheus_adapter import (
    MAX_ALERT_POINTS, MAX_ALERT_SERIES, MAX_ALERT_SOURCE_POINTS, MAX_ALERT_SOURCE_SERIES,
    PrometheusAdapter,
)
from fcapsule.attention.evidence_scorer import score_evidence
from fcapsule.attention.evidence_selector import select_evidence
from fcapsule.incident_report import build_incident_report
from fcapsule.io.case_loader import _validate_metrics, load_case
from fcapsule.live_sources import LiveSourceCoordinator
from fcapsule.models.schemas import CaseValidationError
from fcapsule.pipeline import investigate_case
from fcapsule.processing.metrics_analyzer import analyze_metrics
from fcapsule.store import FCAPSuleStore
from tests.common import REFERENCE_CASE


START = datetime(2026, 9, 20, tzinfo=timezone.utc)
LABELS = {"namespace": "shop", "service": "mysql-exporter"}


def alert(query='up{namespace="shop",service="mysql-exporter"} == 0', labels=None):
    return {
        "alertname": "TargetDown", "status": "firing", "severity": "warning",
        "startsAt": "2026-09-20T00:00:15Z", "labels": labels or LABELS,
        "rule": {"name": "TargetDown", "query": query, "duration": 60, "labels": {"severity": "warning"}},
    }


class AlertExpressionTests(unittest.TestCase):
    def plan(self, query, labels=None, rule_labels=None):
        rule = {"query": query, "labels": rule_labels or {}}
        return plan_alert_expression(rule, labels or LABELS, "shop", "mysql-exporter-abc")

    def test_root_comparison_keeps_underlying_values_and_threshold(self):
        result = self.plan('up{namespace="shop",service="mysql-exporter"} == 0')
        tree = promql.parse(result["expression"])
        self.assertIsInstance(tree, promql.VectorSelector)
        self.assertEqual(tree.name, "up")
        self.assertEqual((result["threshold"], result["operator"], result["unit"]), (0, "==", "state"))

    def test_bare_latency_selector_is_scoped_and_seconds_are_not_converted(self):
        result = self.plan("order_checkout_latency_p90_seconds > 0.25")
        self.assertEqual(result["unit"], "seconds")
        self.assertEqual(result["threshold"], 0.25)
        self.assertIn('namespace="shop"', result["expression"])
        self.assertIn('service="mysql-exporter"', result["expression"])
        self.assertNotIn("namespace", result["underlying_expression"])

    def test_reversed_and_parenthesized_comparisons(self):
        for query, expected in (("(0.25 < (latency_seconds))", ">"), ("-1 >= temperature", "<="), ("0 != up", "!=")):
            with self.subTest(query=query):
                self.assertEqual(self.plan(query)["operator"], expected)

    def test_comparison_like_text_in_label_is_not_stripped(self):
        result = self.plan('up{note="a > b == 0"} == 0')
        tree = promql.parse(result["expression"])
        self.assertEqual(next(m.value for m in tree.matchers.matchers if m.name == "note"), "a > b == 0")

    def test_label_escaping_cannot_inject_promql(self):
        service = 'x"} or up{namespace="other"}\n\\'
        result = self.plan("up == 0", {"namespace": "shop", "service": service})
        tree = promql.parse(result["expression"])
        self.assertIsInstance(tree, promql.VectorSelector)
        self.assertEqual(next(m.value for m in tree.matchers.matchers if m.name == "service"), service)

    def test_scoped_derived_expression_is_preserved(self):
        expression = 'sum(rate(requests_total{namespace="shop",service="mysql-exporter"}[2m]))'
        result = self.plan(f"{expression} > 5")
        self.assertEqual(result["expression"], str(promql.parse(expression)))
        self.assertIsNone(result["unit"])
        self.assertNotIn('pod=', result["expression"])

    def test_per_pod_derived_rule_is_narrowed_only_when_every_aggregation_keeps_pod(self):
        expression = 'sum by (namespace,pod) (rate(order_errors_total{namespace="shop"}[1m])) > 3'
        result = self.plan(expression, labels={"namespace": "shop", "pod": "orders-7"})
        parsed = promql.parse(result["expression"])
        vector = parsed.expr.args[0].vector_selector
        self.assertEqual(
            {matcher.name: matcher.value for matcher in vector.matchers.matchers},
            {"namespace": "shop", "pod": "orders-7"},
        )
        self.assertIn("by (namespace, pod)", result["expression"])
        self.assertEqual(result["scope"], {"namespace": "shop", "pod": "orders-7"})

    def test_per_pod_derived_rule_fails_closed_when_scope_is_lost_or_ambiguous(self):
        cases = (
            ('sum by (namespace) (rate(order_errors_total{namespace="shop"}[1m])) > 3',
             {"namespace": "shop", "pod": "orders-7"}, "drops_incident_identity"),
            ('sum by (namespace,pod) (sum by (namespace) (rate(order_errors_total{namespace="shop"}[1m]))) > 3',
             {"namespace": "shop", "pod": "orders-7"}, "drops_incident_identity"),
            ('sum by (namespace,pod) (rate(order_errors_total{namespace="shop",pod=~"orders-.*"}[1m])) > 3',
             {"namespace": "shop", "pod": "orders-7"}, "not_exact"),
            ('sum by (namespace,pod) (rate(order_errors_total{namespace="elsewhere"}[1m])) > 3',
             {"namespace": "shop", "pod": "orders-7"}, "outside_incident_namespace"),
        )
        for query, labels, reason in cases:
            with self.subTest(query=query), self.assertRaisesRegex(UnavailableExpression, reason):
                self.plan(query, labels=labels)

    def test_compound_latency_alert_captures_primary_value_not_sample_guards(self):
        expression = (
            '(order_checkout_latency_p95_seconds{namespace="shop",service="orders-api"} > 0.25) '
            'and on(namespace,pod,service) '
            '(order_checkout_latency_sample_count{namespace="shop",service="orders-api"} >= 10) '
            'and on(namespace,pod,service) (up{namespace="shop",service="orders-api"} == 1)'
        )
        result = self.plan(expression, labels={"namespace": "shop", "service": "orders-api", "pod": "orders-7"})
        self.assertEqual(result["capture_mode"], "primary_threshold_series")
        self.assertEqual(result["rule_qualifier_count"], 2)
        self.assertEqual(result["threshold"], 0.25)
        self.assertEqual(result["operator"], ">")
        self.assertEqual(result["unit"], "seconds")
        self.assertEqual(result["expression"],
                         'order_checkout_latency_p95_seconds{namespace="shop",service="orders-api",pod="orders-7"}')

    def test_compound_connection_alert_selects_ratio_and_scopes_every_source(self):
        expression = (
            '(inventory_mysql_client_sessions_active{namespace="shop",service="inventory-api"} '
            '/ clamp_min(inventory_mysql_server_max_connections{namespace="shop",service="inventory-api"}, 1)) > 0.8 '
            'and on(namespace,pod,service) '
            '(inventory_mysql_server_max_connections{namespace="shop",service="inventory-api"} > 0) '
            'and on(namespace,pod,service) (up{namespace="shop",service="inventory-api"} == 1)'
        )
        result = self.plan(expression, labels={"namespace": "shop", "service": "inventory-api", "pod": "inventory-3"})
        parsed = promql.parse(result["expression"])
        selectors = []
        promql.walk(parsed, pre_visit=lambda node: selectors.append(node) if isinstance(node, promql.VectorSelector) else None)
        self.assertEqual(result["capture_mode"], "primary_threshold_series")
        self.assertEqual(result["threshold"], 0.8)
        self.assertEqual(result["unit"], "ratio")
        self.assertEqual(result["scope"], {"namespace": "shop", "pod": "inventory-3"})
        self.assertEqual(len(selectors), 2)
        self.assertTrue(all({m.name: m.value for m in item.matchers.matchers}.get("pod") == "inventory-3"
                            for item in selectors))

    def test_compound_alert_with_two_equally_plausible_primary_metrics_fails_closed(self):
        expression = (
            '(request_latency_seconds{namespace="shop"} > 1) '
            'and on(namespace,pod) (request_duration_seconds{namespace="shop"} > 1)'
        )
        with self.assertRaisesRegex(UnavailableExpression, "ambiguous_primary_threshold_series"):
            self.plan(expression, labels={"namespace": "shop", "pod": "orders-7"})

    def test_unsupported_or_unsafe_expressions_are_not_rewritten(self):
        expressions = (
            "up == 0 or latency_seconds > 0.25", "(up == 0) and (ready == 0)",
            "(up == 0) + 1 > 0", "up > other_metric", "up == bool 0",
            "absent(up) == 1", "sum(up) == 0", "up offset 5m == 0", "up @ 10 == 0",
            'up{namespace="other"} == 0', 'up{service="another-app"} == 0',
            'rate(up{namespace="shop",service="mysql-exporter"}[1d]) > 1',
            "up > Inf", "up > (1 + 2)", "max_over_time(up[5m:1s]) > 0", "not valid promql !",
        )
        for expression in expressions:
            with self.subTest(expression=expression), self.assertRaises(UnavailableExpression):
                self.plan(expression)

    def test_static_rule_label_is_not_assumed_to_exist_on_metric(self):
        result = self.plan("up == 0", rule_labels={"service": "mysql-exporter"})
        self.assertNotIn('service=', result["expression"])
        self.assertIn('pod="mysql-exporter-abc"', result["expression"])


class AlertMetricCaptureTests(unittest.TestCase):
    def setUp(self):
        self.adapter = PrometheusAdapter("http://configured-prometheus")
        self.adapter.transport = Mock()

    def collect(self, values=None, query=None, results=None, start=START, end=None):
        if results is None:
            results = [{"metric": LABELS, "values": values if values is not None else [
                [START.timestamp(), "1"], [START.timestamp() + 15, "0"], [START.timestamp() + 30, "0"],
            ]}]
        self.adapter.transport.request.return_value = {"status": "success", "data": {"resultType": "matrix", "result": results}}
        trigger = alert(query) if query else alert()
        return self.adapter.collect_alert_metrics(trigger, "shop", "mysql-exporter-abc", start, end or start + timedelta(minutes=1))

    def test_query_is_bounded_and_only_uses_configured_endpoint(self):
        captured = self.collect()
        path = self.adapter.transport.request.call_args.args[0]
        params = parse_qs(urlparse(path).query)
        self.assertTrue(path.startswith("/api/v1/query_range?"))
        self.assertEqual(params["timeout"], ["4s"])
        self.assertEqual(params["limit"], [str(MAX_ALERT_SERIES + 1)])
        self.assertIsInstance(promql.parse(params["query"][0]), promql.VectorSelector)
        self.assertEqual(captured["alert_evidence"]["status"], "available")
        self.assertEqual(captured["series"][0]["source"]["capture_mode"], "incident_capture")
        self.assertEqual(captured["series"][0]["labels"], LABELS)
        self.assertEqual(captured["series"][0]["rule"]["query"], alert()["rule"]["query"])

    def test_compound_rule_captures_primary_signal_and_discloses_omitted_qualifiers(self):
        expression = (
            '(order_checkout_latency_p95_seconds{namespace="shop",service="mysql-exporter"} > 0.25) '
            'and on(namespace,pod,service) '
            '(order_checkout_latency_sample_count{namespace="shop",service="mysql-exporter"} >= 10) '
            'and on(namespace,pod,service) (up{namespace="shop",service="mysql-exporter"} == 1)'
        )
        captured = self.collect(query=expression)
        first_path = self.adapter.transport.request.call_args_list[0].args[0]
        sent = parse_qs(urlparse(first_path).query)["query"][0]
        parsed = promql.parse(sent)
        self.assertIsInstance(parsed, promql.VectorSelector)
        self.assertEqual(parsed.name, "order_checkout_latency_p95_seconds")
        self.assertEqual(captured["alert_evidence"]["capture_mode"], "primary_threshold_series")
        self.assertEqual(captured["alert_evidence"]["rule_qualifier_count"], 2)
        self.assertEqual(captured["series"][0]["source"]["capture_mode"], "primary_threshold_series")
        self.assertIn("not graphed", captured["series"][0]["source"]["capture_note"])
        self.assertIn("sample_count", captured["alert_evidence"]["rule"]["query"])

    def test_retained_lab_pm_rules_capture_their_threshold_series(self):
        cases = (
            (
                "mysql-connections",
                '(inventory_mysql_client_sessions_active{namespace="fcapsule-lab",service="inventory-api"} '
                '/ clamp_min(inventory_mysql_server_max_connections{namespace="fcapsule-lab",service="inventory-api"}, 1)) > 0.8 '
                'and on (namespace, pod, service) (inventory_mysql_server_max_connections{namespace="fcapsule-lab",service="inventory-api"} > 0) '
                'and on (namespace, pod, service) (time() - inventory_mysql_sample_timestamp_seconds{namespace="fcapsule-lab",service="inventory-api"} < 45) '
                'and on (namespace, pod, service) (time() - timestamp(inventory_mysql_client_sessions_active{namespace="fcapsule-lab",service="inventory-api"}) < 30) '
                'and on (namespace, pod, service) (up{namespace="fcapsule-lab",service="inventory-api"} == 1)',
                "inventory-api-69c954747-djq6x", "inventory-api", 0.8, "primary_threshold_series",
                "inventory_mysql_client_sessions_active",
            ),
            (
                "memory-leak",
                'max by (namespace, pod, service) (lab_worker_allocated_bytes{namespace="fcapsule-lab",service="lab-worker"}) > 83886080',
                "lab-worker-568d998598-px4xg", "lab-worker", 83886080, "alert_condition",
                "lab_worker_allocated_bytes",
            ),
            (
                "downstream-latency",
                '(orders_checkout_latency_p95_seconds{namespace="fcapsule-lab",service="orders-api"} > 0.25) '
                'and on (namespace, pod, service) (orders_checkout_latency_sample_count{namespace="fcapsule-lab",service="orders-api"} >= 10) '
                'and on (namespace, pod, service) (time() - orders_checkout_latency_latest_sample_timestamp_seconds{namespace="fcapsule-lab",service="orders-api"} < 30) '
                'and on (namespace, pod, service) (up{namespace="fcapsule-lab",service="orders-api"} == 1)',
                "orders-api-8675f7c799-r67km", "orders-api", 0.25, "primary_threshold_series",
                "orders_checkout_latency_p95_seconds",
            ),
        )
        start = datetime(2026, 9, 24, tzinfo=timezone.utc)
        for name, query, pod, service, threshold, capture_mode, metric_name in cases:
            with self.subTest(rule=name):
                self.adapter.transport.request.reset_mock()
                self.adapter.transport.request.return_value = {
                    "status": "success",
                    "data": {"resultType": "matrix", "result": [{
                        "metric": {"namespace": "fcapsule-lab", "pod": pod, "service": service},
                        "values": [[start.timestamp(), "0.85"], [start.timestamp() + 15, "0.9"]],
                    }]},
                }
                trigger = alert(query, labels={"namespace": "fcapsule-lab", "pod": pod, "service": service})
                trigger["rule"]["labels"] = {"service": service, "severity": "warning", "signal_class": "PM"}
                captured = self.adapter.collect_alert_metrics(
                    trigger, "fcapsule-lab", pod, start, start + timedelta(minutes=1),
                )
                self.assertEqual(captured["alert_evidence"]["status"], "available")
                self.assertEqual(captured["alert_evidence"]["capture_mode"], capture_mode)
                self.assertEqual(captured["alert_evidence"]["threshold"], threshold)
                self.assertEqual(len(captured["series"]), 1)
                sent = parse_qs(urlparse(self.adapter.transport.request.call_args_list[0].args[0]).query)["query"][0]
                parsed = promql.parse(sent)
                names = []
                promql.walk(parsed, pre_visit=lambda node: names.append(node.name) if isinstance(node, promql.VectorSelector) else None)
                self.assertIn(metric_name, names)
                self.assertNotIn("time", names)
                self.assertNotIn("up", names)

    def test_retained_pm_rules_capture_raw_application_sources(self):
        cases = (
            (
                "mysql-connections",
                '(inventory_mysql_client_sessions_active{namespace="fcapsule-lab",service="inventory-api"} '
                '/ clamp_min(inventory_mysql_server_max_connections{namespace="fcapsule-lab",service="inventory-api"}, 1)) > 0.8 '
                'and on (namespace, pod, service) (inventory_mysql_server_max_connections{namespace="fcapsule-lab",service="inventory-api"} > 0) '
                'and on (namespace, pod, service) (time() - inventory_mysql_sample_timestamp_seconds{namespace="fcapsule-lab",service="inventory-api"} < 45) '
                'and on (namespace, pod, service) (time() - timestamp(inventory_mysql_client_sessions_active{namespace="fcapsule-lab",service="inventory-api"}) < 30) '
                'and on (namespace, pod, service) (up{namespace="fcapsule-lab",service="inventory-api"} == 1)',
                "inventory-api-1", "inventory-api", {"service": "inventory-api", "severity": "critical"},
                {"inventory_mysql_client_sessions_active", "inventory_mysql_server_max_connections", "inventory_mysql_sample_timestamp_seconds"},
            ),
            (
                "cpu-saturation",
                'sum by (namespace, pod, container) (rate(container_cpu_usage_seconds_total{container="worker",image!="",namespace="fcapsule-lab"}[1m])) '
                '/ max by (namespace, pod, container) (kube_pod_container_resource_limits{container="worker",namespace="fcapsule-lab",resource="cpu",unit="core"}) > 0.75',
                "lab-worker-1", "lab-worker", {"service": "lab-worker", "severity": "critical"},
                {"container_cpu_usage_seconds_total", "kube_pod_container_resource_limits"},
            ),
            (
                "downstream-latency",
                '(orders_checkout_latency_p95_seconds{namespace="fcapsule-lab",service="orders-api"} > 0.25) '
                'and on (namespace, pod, service) (orders_checkout_latency_sample_count{namespace="fcapsule-lab",service="orders-api"} >= 10) '
                'and on (namespace, pod, service) (time() - orders_checkout_latency_latest_sample_timestamp_seconds{namespace="fcapsule-lab",service="orders-api"} < 30) '
                'and on (namespace, pod, service) (up{namespace="fcapsule-lab",service="orders-api"} == 1)',
                "orders-api-1", "orders-api", {"service": "orders-api", "severity": "warning"},
                {"orders_checkout_latency_sample_count", "orders_checkout_latency_latest_sample_timestamp_seconds"},
            ),
        )
        start = datetime(2026, 9, 24, tzinfo=timezone.utc)
        for name, query, pod, service, rule_labels, source_names in cases:
            with self.subTest(rule=name):
                self.adapter.transport.reset_mock()
                scope_labels = {"namespace": "fcapsule-lab", "pod": pod, "service": service}
                source_results = {metric: {
                    "metric": {"__name__": metric, **scope_labels},
                    "values": [[start.timestamp(), "1"], [start.timestamp() + 15, "2"]],
                } for metric in sorted(source_names)}
                main_response = {"status": "success", "data": {"resultType": "matrix", "result": [{
                        "metric": scope_labels,
                        "values": [[start.timestamp(), "0.85"], [start.timestamp() + 15, "0.9"]],
                    }]}}

                request_index = [0]

                def query_response(path, **kwargs):
                    if request_index[0] == 0:
                        request_index[0] += 1
                        return main_response
                    request_index[0] += 1
                    query_text = parse_qs(urlparse(path).query)["query"][0]
                    matched = [metric for metric in source_names if metric in query_text]
                    return {"status": "success", "data": {"resultType": "matrix", "result": [
                        source_results[metric] for metric in matched
                    ]}}

                self.adapter.transport.request.side_effect = query_response
                trigger = alert(query, labels=scope_labels)
                trigger["rule"]["labels"] = rule_labels
                captured = self.adapter.collect_alert_metrics(
                    trigger, "fcapsule-lab", pod, start, start + timedelta(minutes=1),
                )
                source_capture = captured["alert_evidence"]["source_metric_capture"]
                self.assertEqual(source_capture["status"], "available")
                self.assertEqual(set(source_capture["metric_names"]), source_names)
                sources = [series for series in captured["series"] if series["signal_origin"] == "alert_rule_source"]
                self.assertEqual({series["metric"] for series in sources}, source_names)
                self.assertTrue(all(series["scope"] == {"namespace": "fcapsule-lab", "pod": pod} for series in sources))
                self.assertTrue(all("threshold" not in series and "operator" not in series for series in sources))
                self.assertTrue(all(all(point[1] is not None for point in series["values"]) for series in sources))
                _validate_metrics({"series": sources})
                self.assertGreater(source_capture["omitted_missing_or_non_finite_points"], 0)
                self.assertGreaterEqual(len(self.adapter.transport.request.call_args_list), 2)
                for request in self.adapter.transport.request.call_args_list[1:]:
                    source_params = parse_qs(urlparse(request.args[0]).query)
                    source_query = source_params["query"][0]
                    selectors = []
                    promql.walk(promql.parse(source_query), pre_visit=lambda node: selectors.append(node)
                                if isinstance(node, promql.VectorSelector) else None)
                    self.assertTrue(all(
                        {matcher.name: matcher.value for matcher in node.matchers.matchers}.get("pod") == pod
                        for node in selectors
                    ))
                    self.assertTrue(all(
                        not any(matcher.name == "__name__" and "up" in matcher.value
                                for matcher in node.matchers.matchers)
                        for node in selectors
                    ))
                    self.assertLessEqual(int(source_params["limit"][0]), MAX_ALERT_SOURCE_SERIES + 1)
                    self.assertEqual(source_params["timeout"], ["4s"])

    def test_unscoped_rule_source_is_skipped_and_source_series_caps_are_disclosed(self):
        primary = 'orders_checkout_latency_p95_seconds{namespace="shop",service="orders-api"} > 0.25'
        unsafe = 'secret_internal_metric{namespace="elsewhere",service="orders-api"} > 1'
        trigger = alert(f"({primary}) and ({unsafe}) and (up{{namespace=\"shop\",service=\"orders-api\"}} == 1)",
                        labels={"namespace": "shop", "pod": "orders-1", "service": "orders-api"})
        trigger["rule"]["labels"] = {"service": "orders-api"}
        self.adapter.transport.request.return_value = {"status": "success", "data": {"resultType": "matrix", "result": [{
            "metric": {"namespace": "shop", "pod": "orders-1", "service": "orders-api"},
            "values": [[START.timestamp(), "0.5"]],
        }]}}
        captured = self.adapter.collect_alert_metrics(trigger, "shop", "orders-1", START, START + timedelta(minutes=1))
        self.assertEqual(self.adapter.transport.request.call_count, 1)
        self.assertEqual(captured["alert_evidence"]["source_metric_capture"]["status"], "not_applicable")
        self.assertEqual(captured["alert_evidence"]["source_metric_capture"]["rejected_selector_count"], 1)

        guards = " and ".join(f'(related_signal_{index}{{namespace="shop",service="orders-api"}} > 0)' for index in range(9))
        trigger = alert(f"({primary}) and ({guards})", labels={"namespace": "shop", "pod": "orders-1", "service": "orders-api"})
        trigger["rule"]["labels"] = {"service": "orders-api"}
        source_results = [{
            "metric": {"__name__": f"related_signal_{index}", "namespace": "shop", "pod": "orders-1", "service": "orders-api"},
            "values": [[START.timestamp(), "1"], [START.timestamp() + 15, "1"]],
        } for index in range(MAX_ALERT_SOURCE_SERIES + 1)]
        self.adapter.transport.request.side_effect = [
            {"status": "success", "data": {"resultType": "matrix", "result": [{
                "metric": {"namespace": "shop", "pod": "orders-1", "service": "orders-api"},
                "values": [[START.timestamp(), "0.5"]],
            }]}},
            {"status": "success", "data": {"resultType": "matrix", "result": source_results}},
        ]
        captured = self.adapter.collect_alert_metrics(trigger, "shop", "orders-1", START, START + timedelta(minutes=1))
        summary = captured["alert_evidence"]["source_metric_capture"]
        self.assertEqual(len([series for series in captured["series"] if series["signal_origin"] == "alert_rule_source"]),
                         MAX_ALERT_SOURCE_SERIES)
        self.assertEqual(summary["status"], "partial")
        self.assertTrue(summary["truncated"])
        self.assertEqual(summary["omitted_selector_count"], 1)
        self.assertEqual(summary["omitted_series_at_least"], 1)

    def test_alert_source_rejects_oversized_result_labels(self):
        query = ('orders_checkout_latency_p95_seconds{namespace="shop"} > 0.25 and '
                 'orders_checkout_sample_count{namespace="shop"} > 0')
        scope = {"namespace": "shop", "pod": "orders-1"}
        self.adapter.transport.request.side_effect = [
            {"status": "success", "data": {"resultType": "matrix", "result": [{
                "metric": scope,
                "values": [[START.timestamp(), "0.5"], [START.timestamp() + 15, "0.6"]],
            }]}},
            {"status": "success", "data": {"resultType": "matrix", "result": [{
                "metric": {"__name__": "orders_checkout_sample_count", **scope, "extra": "x" * 2100},
                "values": [[START.timestamp(), "10"], [START.timestamp() + 15, "12"]],
            }]}},
        ]
        captured = self.adapter.collect_alert_metrics(
            alert(query, labels=scope), "shop", "orders-1", START, START + timedelta(minutes=1),
        )
        summary = captured["alert_evidence"]["source_metric_capture"]
        self.assertEqual(summary["rejected_label_series_count"], 1)
        self.assertEqual(summary["status"], "unavailable")
        self.assertEqual(summary["reason"], "invalid_query_samples")
        self.assertFalse(any(series["signal_origin"] == "alert_rule_source" for series in captured["series"]))

    def test_alert_source_metric_points_have_a_coarser_hard_cap(self):
        query = 'sum by (namespace, pod) (rate(worker_failures_total{namespace="shop"}[2m])) > 1'
        labels = {"namespace": "shop", "pod": "worker-1"}
        trigger = alert(query, labels=labels)
        trigger["rule"]["labels"] = {}
        end = START + timedelta(hours=4)
        threshold_values = [[START.timestamp() + offset * 60, "2"] for offset in range(MAX_ALERT_POINTS)]
        source_values = [[START.timestamp() + offset * 120, "2"] for offset in range(MAX_ALERT_SOURCE_POINTS)]
        self.adapter.transport.request.side_effect = [
            {"status": "success", "data": {"resultType": "matrix", "result": [{
                "metric": labels, "values": threshold_values,
            }]}},
            {"status": "success", "data": {"resultType": "matrix", "result": [{
                "metric": {"__name__": "worker_failures_total", **labels}, "values": source_values,
            }]}},
        ]
        captured = self.adapter.collect_alert_metrics(trigger, "shop", "worker-1", START, end)
        source = next(series for series in captured["series"] if series["signal_origin"] == "alert_rule_source")
        self.assertEqual(source["step_seconds"], 120)
        self.assertEqual(len(source["values"]), MAX_ALERT_SOURCE_POINTS)
        self.assertEqual(captured["alert_evidence"]["source_metric_capture"]["point_limit"], MAX_ALERT_SOURCE_POINTS)


    def test_non_finite_and_missing_samples_remain_null_not_zero(self):
        captured = self.collect([[START.timestamp(), "1"], [START.timestamp() + 15, "NaN"], [START.timestamp() + 45, "+Inf"]])
        self.assertEqual([point[1] for point in captured["series"][0]["values"]], [1, None, None, None, None])
        json.dumps(captured, allow_nan=False)

    def test_prometheus_millisecond_timestamp_precision_is_preserved(self):
        start = START + timedelta(microseconds=123456)
        captured = self.collect([[START.timestamp() + .123, "1"]], start=start)
        self.assertEqual(captured["series"][0]["values"][0], ["2026-09-20T00:00:00.123000Z", 1])

    def test_multiple_series_same_metric_keep_labels_and_stable_identity(self):
        results = [{"metric": {**LABELS, "instance": str(index)}, "values": [[START.timestamp(), "0"]]} for index in range(2)]
        first, second = self.collect(results=results), self.collect(results=list(reversed(results)))
        self.assertEqual(len(first["series"]), 2)
        self.assertNotEqual(first["series"][0]["series_id"], first["series"][1]["series_id"])
        self.assertEqual(first["series"][0]["series_id"], second["series"][1]["series_id"])

    def test_no_query_for_unsupported_rules_or_model_annotations(self):
        captured = self.collect(query="up == 0 or vector(1)")
        self.assertEqual(captured["alert_evidence"]["status"], "unavailable")
        self.adapter.transport.request.assert_not_called()
        trigger = alert()
        trigger.pop("rule")
        trigger["annotations"] = {"query": "up", "generatorURL": "http://untrusted/api/v1/query?query=up"}
        captured = self.adapter.collect_alert_metrics(trigger, "shop", "pod", START, START + timedelta(minutes=1))
        self.assertEqual(captured["alert_evidence"]["reason"], "rule_definition_unavailable")
        self.adapter.transport.request.assert_not_called()

    def test_large_time_window_is_rejected_without_query(self):
        captured = self.collect(end=START + timedelta(days=1))
        self.assertEqual(captured["alert_evidence"]["reason"], "capture_window_out_of_bounds")
        self.adapter.transport.request.assert_not_called()

    def test_point_count_is_bounded_for_long_valid_window(self):
        captured = self.collect([[START.timestamp(), "1"]], end=START + timedelta(hours=4))
        self.assertLessEqual(len(captured["series"][0]["values"]), MAX_ALERT_POINTS)

    def test_many_series_are_unavailable_not_silently_truncated(self):
        captured = self.collect(results=[{"metric": LABELS, "values": []}] * (MAX_ALERT_SERIES + 1))
        self.assertEqual(captured["alert_evidence"]["reason"], "series_limit_exceeded")
        self.assertEqual(captured["series"], [])

    def test_query_error_does_not_abort_capture(self):
        self.adapter.transport.request.side_effect = RuntimeError("secret URL")
        captured = self.collect()
        self.assertEqual(captured["alert_evidence"]["reason"], "query_failed")
        self.assertNotIn("secret", json.dumps(captured))

    def test_no_samples_histograms_and_wrong_scope_are_explicitly_unavailable(self):
        for results, reason in (([], "no_finite_samples"),
                                ([{"histograms": [[1, {}]]}], "native_histogram_values_unsupported"),
                                ([{"metric": {"namespace": "other"}}], "result_outside_incident_scope")):
            with self.subTest(reason=reason):
                captured = self.collect(results=results)
                self.assertEqual(captured["alert_evidence"]["reason"], reason)
                self.assertEqual(captured["series"], [])

    def test_duplicate_rule_names_are_marked_ambiguous(self):
        self.adapter.transport.request.return_value = {"status": "success", "data": {"groups": [{"rules": [
            {"name": "SameName", "query": "up == 0"}, {"name": "SameName", "query": "latency > 1"},
        ]}]}}
        rule = self.adapter.alert_rules()["SameName"]
        self.assertTrue(rule["ambiguous"])
        with self.assertRaisesRegex(UnavailableExpression, "ambiguous_rule_name"):
            plan_alert_expression(rule, LABELS, "shop", "pod")


class AlertMetricPipelineTests(unittest.TestCase):
    def setUp(self):
        self.adapter = PrometheusAdapter("http://configured-prometheus")
        self.adapter.transport = Mock(return_value=None)
        self.adapter.transport.request.return_value = {"status": "success", "data": {"resultType": "matrix", "result": [
            {"metric": LABELS, "values": [[START.timestamp(), "1"], [START.timestamp() + 15, "0"], [START.timestamp() + 30, "0"]]},
        ]}}
        self.captured = self.adapter.collect_alert_metrics(alert(), "shop", "pod", START, START + timedelta(minutes=1))
        self.bundle = replace(load_case(REFERENCE_CASE), metrics=self.captured["series"], alerts=[{**alert(), "metric_evidence": self.captured["alert_evidence"]}])

    def test_zero_is_observed_failure_and_not_missing(self):
        anomaly = analyze_metrics(self.bundle)[0]
        self.assertEqual(anomaly["condition"]["matching_samples"], 2)
        self.assertEqual(anomaly["condition"]["missing_samples"], 2)
        self.assertEqual(anomaly["incident_peak"], 0)
        self.assertEqual(anomaly["condition"]["latest"]["value"], 0)

    def test_selected_evidence_has_structured_condition_and_real_values(self):
        selected, _ = select_evidence(score_evidence(self.bundle, [], analyze_metrics(self.bundle)))
        item = next(item for item in selected if item["type"] == "metric_anomaly")
        observation = item["metric_observation"]
        self.assertEqual(observation["condition"]["min"], 0)
        self.assertEqual(observation["threshold"], 0)
        self.assertEqual(observation["operator"], "==")
        self.assertNotIn("values", observation)
        self.assertIn("do not prove", item["summary"])

    def test_counter_threshold_is_not_changed_to_delta(self):
        series = {**self.bundle.metrics[0], "metric": "failures_total", "threshold": 10,
                  "operator": ">", "values": [["2026-09-20T00:00:00Z", 50], ["2026-09-20T00:00:15Z", 50]]}
        anomaly = analyze_metrics(replace(self.bundle, metrics=[series]))[0]
        self.assertEqual(anomaly["analysis_mode"], "alert_condition")
        self.assertEqual(anomaly["condition"]["matching_samples"], 2)
        self.assertEqual(anomaly["incident_peak"], 50)

    def test_no_pre_alert_values_do_not_invent_baseline(self):
        series = {**self.bundle.metrics[0], "values": [["2026-09-20T00:00:15Z", 0]]}
        anomaly = analyze_metrics(replace(self.bundle, metrics=[series]))[0]
        self.assertIsNone(anomaly["baseline_median"])
        self.assertEqual(anomaly["baseline_basis"], "unavailable")

    def test_latency_report_preserves_seconds_and_threshold(self):
        series = {**self.bundle.metrics[0], "metric": "order_checkout_latency_p90_seconds", "unit": "seconds",
                  "threshold": .25, "operator": ">", "values": [
                      ["2026-09-20T00:00:00Z", .1], ["2026-09-20T00:00:15Z", .750001],
                  ]}
        bundle = replace(self.bundle, metrics=[series])
        report = build_incident_report({"metric_anomalies": analyze_metrics(bundle)}, {}, bundle.metrics)
        signal = report["pm_signals"][0]
        self.assertEqual(signal["threshold"], .25)
        self.assertEqual(signal["values"][1]["value"], .750001)
        self.assertEqual(signal["peak_value"], .750001)
        self.assertEqual(signal["unit"], "seconds")
        self.assertEqual(signal["condition"]["incident_matching_samples"], 1)

    def test_historical_capture_gets_explicit_note_without_synthesized_data(self):
        historical = load_case(REFERENCE_CASE)
        report = build_incident_report({"alerts": historical.alerts}, {}, historical.metrics)
        self.assertEqual(report["report_version"], "1.3")
        self.assertEqual(report["alert_metric_evidence"][0]["reason"], "not_captured")
        self.assertIn("not backfilled", report["alert_metric_evidence"][0]["note"])
        self.assertEqual(report["pm_signals"], [])

    def test_report_contract_retains_multiple_series_and_gaps(self):
        series = self.bundle.metrics[0]
        bundle = replace(self.bundle, metrics=[series, {**series, "series_id": "another", "labels": {**LABELS, "instance": "two"}}])
        anomalies = analyze_metrics(bundle)
        selected, _ = select_evidence(score_evidence(bundle, [], anomalies))
        report = build_incident_report({"alerts": bundle.alerts, "metric_anomalies": anomalies, "selected_evidence": selected}, {}, bundle.metrics)
        self.assertEqual(len(report["pm_signals"]), 2)
        signal = report["pm_signals"][0]
        for field in ("series_id", "expression", "rule", "threshold", "operator", "unit", "labels", "source", "time_range", "step_seconds", "condition"):
            self.assertIn(field, signal)
        self.assertEqual(signal["alert_timestamp"], alert()["startsAt"])
        self.assertIsNone(signal["values"][-1]["value"])
        self.assertEqual(report["alert_metric_evidence"][0]["status"], "available")
        self.assertIn("metric_observation", next(item for item in report["supporting_evidence"] if item["type"] == "metric_anomaly"))

    def test_loader_permits_alert_gaps_but_rejects_nonfinite_values(self):
        _validate_metrics({"series": self.bundle.metrics})
        for value in (float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(CaseValidationError):
                _validate_metrics({"series": [{**self.bundle.metrics[0], "values": [["2026-09-20T00:00:15Z", value]]}]})

    def test_complete_pipeline_retains_condition_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory) / "case"
            shutil.copytree(REFERENCE_CASE, case)
            (case / "alert.json").write_text(json.dumps(self.bundle.alerts))
            (case / "prometheus_metrics.json").write_text(json.dumps({"series": self.bundle.metrics, "alert_evidence": self.captured["alert_evidence"]}))
            output = Path(directory) / "output"
            investigate_case(case, output)
            capsule = json.loads((output / "capsule.json").read_text())
            selected = next(item for item in capsule["selected_evidence"] if item.get("signal_origin") == "alert_rule")
            self.assertEqual(selected["metric_observation"]["condition"]["matching_samples"], 2)

    def test_new_capture_includes_alert_metrics_and_existing_capture_is_immutable(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            coordinator = LiveSourceCoordinator(FCAPSuleStore(state / "store.sqlite3"), state)
            prometheus, logs, kubernetes = Mock(), Mock(), Mock()
            prometheus.collect_alert_metrics.return_value = self.captured
            prometheus.collect_pod_metrics.return_value = []
            logs.collect_logs.return_value = []
            kubernetes.configuration_snapshot.return_value = []
            config = {"incident_window_minutes": 10, "cluster_name": "test", "opensearch_index": "logs"}
            pod = {"name": "pod", "namespace": "shop", "workload": "mysql-exporter"}
            case = coordinator._capture_case(config, prometheus, logs, kubernetes, alert(), pod, "fresh-case")
            original = {path.name: path.read_bytes() for path in case.iterdir()}
            bundle = load_case(case)
            self.assertEqual(bundle.metrics[0]["signal_origin"], "alert_rule")
            self.assertEqual(bundle.alerts[0]["metric_evidence"]["status"], "available")
            self.assertLessEqual((bundle.window_end - bundle.window_start).total_seconds(), 1200)
            coordinator._capture_case(config, prometheus, logs, kubernetes, alert("up > 1"), pod, "fresh-case")
            prometheus.collect_alert_metrics.assert_called_once()
            self.assertEqual(original, {path.name: path.read_bytes() for path in case.iterdir()})


if __name__ == "__main__":
    unittest.main()
