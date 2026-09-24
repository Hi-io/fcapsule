"""Read-only Prometheus HTTP API adapter."""

from __future__ import annotations

import time
import math
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlsplit

import promql_parser as promql

from fcapsule.adapters.transport import JsonTransport, ResponseTooLargeError
from fcapsule.adapters.alert_expression import UnavailableExpression, plan_alert_expression

MAX_PROMETHEUS_DEFAULT_RESPONSE_BYTES = 1024 * 1024
MAX_PROMETHEUS_QUERY_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PROMETHEUS_RANGE_RESPONSE_BYTES = 1024 * 1024
MAX_PROMETHEUS_DISCOVERY_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PROMETHEUS_SCOPED_TARGET_RESPONSE_BYTES = 256 * 1024
MAX_PROMETHEUS_STATUS_RESPONSE_BYTES = 64 * 1024
MAX_PROMETHEUS_SCRAPE_POOLS = 4
MAX_PROMETHEUS_TARGETS_PER_STATE = 40

MAX_ALERT_SERIES = 12
MAX_ALERT_POINTS = 241
MAX_ALERT_WINDOW_SECONDS = 4 * 60 * 60
MAX_ALERT_SOURCE_SELECTORS = 8
MAX_ALERT_SOURCE_QUERY_GROUPS = 3
MAX_ALERT_SOURCE_SERIES = 6
MAX_ALERT_SOURCE_POINTS = 121
MAX_ALERT_SOURCE_SELECTOR_CHARS = 1024
MAX_ALERT_SOURCE_EXPRESSION_CHARS = 4096
MAX_ALERT_SOURCE_LABELS = 32
MAX_ALERT_SOURCE_LABEL_CHARS = 2048

_MATCHER_SYMBOLS = {
    "MatchOp.Equal": "=",
    "MatchOp.NotEqual": "!=",
    "MatchOp.Re": "=~",
    "MatchOp.NotRe": "!~",
}


class PrometheusAdapter:
    """Query alerts, targets, pod identity, and bounded metric ranges."""

    def __init__(self, base_url: str, timeout: float = 8) -> None:
        self.base_url = base_url.rstrip("/")
        self.transport = JsonTransport(self.base_url, timeout=timeout)
        self.last_pod_metric_capture_info: dict[str, Any] = {
            "status": "unreported", "available": None, "truncated": None,
            "retained_series": 0,
        }

    def _api(
        self, path: str, params: dict[str, Any] | None = None,
        *, max_response_bytes: int | None = None,
    ) -> Any:
        suffix = path
        if params:
            suffix += "?" + urlencode(params)
        payload = self.transport.request(
            suffix, max_response_bytes=(max_response_bytes if max_response_bytes is not None
                                       else _response_limit(path)),
        )
        if payload.get("status") != "success":
            raise RuntimeError(str(payload.get("error") or "Prometheus API request failed"))
        return payload.get("data")

    def query(self, expression: str) -> list[dict[str, Any]]:
        data = self._api("/api/v1/query", {"query": expression}) or {}
        return list(data.get("result", []))

    def query_range(
        self,
        expression: str,
        start: datetime,
        end: datetime,
        step_seconds: int = 30,
    ) -> list[dict[str, Any]]:
        data = self._api(
            "/api/v1/query_range",
            {
                "query": expression,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": max(5, step_seconds),
            },
        ) or {}
        return list(data.get("result", []))

    def test_connection(self) -> dict[str, Any]:
        started = time.perf_counter()
        build = self.transport.request(
            "/api/v1/status/buildinfo", max_response_bytes=MAX_PROMETHEUS_STATUS_RESPONSE_BYTES,
        )
        targets = self._api("/api/v1/targets", {"state": "active"}) or {}
        active = list(targets.get("activeTargets", []))
        return {
            "ok": True,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "version": build.get("data", {}).get("version"),
            "active_targets": len(active),
            "healthy_targets": sum(1 for item in active if item.get("health") == "up"),
        }

    def active_alerts(self) -> list[dict[str, Any]]:
        data = self._api("/api/v1/alerts") or {}
        alerts = []
        for item in data.get("alerts", []):
            if str(item.get("state", "firing")).lower() != "firing":
                continue
            labels = dict(item.get("labels", {}))
            annotations = dict(item.get("annotations", {}))
            alerts.append(
                {
                    "alertname": labels.get("alertname", "PrometheusAlert"),
                    "status": item.get("state", "firing"),
                    "severity": labels.get("severity", "warning"),
                    "startsAt": item.get("activeAt"),
                    "endsAt": None,
                    "labels": labels,
                    "annotations": annotations,
                    "value": item.get("value"),
                }
            )
        return alerts

    def scrape_targets(
        self, namespace: str, pods: set[str] | None = None,
        scrape_pools: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return a bounded, scope-aware view of Prometheus target discovery.

        `up == 0` describes a contacted target. A dropped target or the absence of
        a discovered target describes a different failure mode, so both remain
        explicit rather than being collapsed into one health flag. Supplying
        scrape pools uses Prometheus' server-side filter, with a tighter byte
        budget per pool; it never fetches the federated target list first.
        """
        expected = pods or set()
        pool_names = list(dict.fromkeys(
            pool.strip() for pool in (scrape_pools or [])
            if isinstance(pool, str) and pool.strip()
        ))
        scoped = scrape_pools is not None
        selected_pools = pool_names[:MAX_PROMETHEUS_SCRAPE_POOLS]
        omitted_pools = max(0, len(pool_names) - len(selected_pools))
        active_items: list[dict[str, Any]] = []
        dropped_items: list[dict[str, Any]] = []
        failed_pools = 0
        successful_pools = 0
        failure_reasons: set[str] = set()

        if scoped:
            if not selected_pools:
                return {
                    "active": [], "dropped": [],
                    "inventory": {
                        "status": "unavailable", "complete": False, "scope_complete": False,
                        "scope": "scrape_pools", "reason": "no_relevant_scrape_pools",
                        "requested_scrape_pools": [], "omitted_scrape_pools": omitted_pools,
                        "response_limited_pools": 0,
                    },
                }
            for pool in selected_pools:
                try:
                    data = self._api(
                        "/api/v1/targets", {"state": "any", "scrapePool": pool},
                        max_response_bytes=MAX_PROMETHEUS_SCOPED_TARGET_RESPONSE_BYTES,
                    ) or {}
                except ResponseTooLargeError:
                    failed_pools += 1
                    failure_reasons.add("response_byte_limit")
                    continue
                except (RuntimeError, OSError, ValueError):
                    failed_pools += 1
                    failure_reasons.add("query_failed")
                    continue
                if not isinstance(data, dict) or not isinstance(data.get("activeTargets", []), list) \
                        or not isinstance(data.get("droppedTargets", []), list):
                    failed_pools += 1
                    failure_reasons.add("invalid_response")
                    continue
                successful_pools += 1
                active_items.extend(item for item in data.get("activeTargets", []) if isinstance(item, dict))
                dropped_items.extend(item for item in data.get("droppedTargets", []) if isinstance(item, dict))
        else:
            try:
                data = self._api("/api/v1/targets") or {}
            except ResponseTooLargeError:
                return {
                    "active": [], "dropped": [],
                    "inventory": {
                        "status": "unavailable", "complete": False, "scope_complete": False,
                        "scope": "namespace", "reason": "response_byte_limit",
                        "requested_scrape_pools": [], "omitted_scrape_pools": 0,
                        "response_limited_pools": 1,
                    },
                }
            if not isinstance(data, dict) or not isinstance(data.get("activeTargets", []), list) \
                    or not isinstance(data.get("droppedTargets", []), list):
                return {
                    "active": [], "dropped": [],
                    "inventory": {
                        "status": "unavailable", "complete": False, "scope_complete": False,
                        "scope": "namespace", "reason": "invalid_response",
                        "requested_scrape_pools": [], "omitted_scrape_pools": 0,
                        "response_limited_pools": 0,
                    },
                }
            active_items = [item for item in data.get("activeTargets", []) if isinstance(item, dict)]
            dropped_items = [item for item in data.get("droppedTargets", []) if isinstance(item, dict)]

        def normalized(item: dict[str, Any], state: str) -> dict[str, Any] | None:
            labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
            discovered = item.get("discoveredLabels") if isinstance(item.get("discoveredLabels"), dict) else {}
            combined = {str(key): str(value) for key, value in {**discovered, **labels}.items()}
            target_namespace = combined.get("namespace") or combined.get("__meta_kubernetes_namespace") or ""
            pod = combined.get("pod") or combined.get("__meta_kubernetes_pod_name") or ""
            service = combined.get("service") or combined.get("__meta_kubernetes_service_name") or ""
            if target_namespace != namespace and pod not in expected:
                return None
            scrape_url = item.get("scrapeUrl")
            scrape_endpoint = str(discovered.get("__address__") or "")[:200]
            scrape_path = discovered.get("__metrics_path__")
            if isinstance(scrape_url, str):
                try:
                    parsed_url = urlsplit(scrape_url)
                    scrape_endpoint = parsed_url.netloc.rsplit("@", 1)[-1][:200]
                    scrape_path = parsed_url.path or scrape_path
                except ValueError:
                    pass
            if isinstance(scrape_path, str):
                scrape_path = scrape_path.split("?", 1)[0].split("#", 1)[0][:240]
            else:
                scrape_path = ""
            return {
                "state": state,
                "health": str(item.get("health") or "unknown"),
                "last_error": str(item.get("lastError") or "")[:400],
                "scrape_pool": str(item.get("scrapePool") or ""),
                "scrape_endpoint": scrape_endpoint,
                "scrape_path": scrape_path,
                "job": str(labels.get("job") or combined.get("job") or ""),
                "namespace": target_namespace,
                "pod": pod,
                "service": service,
                "labels": {key: value for key, value in combined.items() if key in {
                    "namespace", "pod", "service", "job", "instance", "__meta_kubernetes_namespace",
                    "__meta_kubernetes_pod_name", "__meta_kubernetes_service_name", "__meta_kubernetes_service_label_fcapsule_io_app_metrics",
                }},
            }

        active_all = [value for item in active_items
                      if (value := normalized(item, "active")) is not None]
        dropped_all = [value for item in dropped_items
                       if (value := normalized(item, "dropped")) is not None]
        truncated = (len(active_all) > MAX_PROMETHEUS_TARGETS_PER_STATE
                     or len(dropped_all) > MAX_PROMETHEUS_TARGETS_PER_STATE)
        active = active_all[:MAX_PROMETHEUS_TARGETS_PER_STATE]
        dropped = dropped_all[:MAX_PROMETHEUS_TARGETS_PER_STATE]
        scope_complete = not failed_pools and not omitted_pools and not truncated
        if scoped:
            status = "partial" if successful_pools else "unavailable"
            reason = ("response_byte_limit" if "response_byte_limit" in failure_reasons else
                      next(iter(sorted(failure_reasons)), None) or
                      ("scrape_pool_scope" if successful_pools else "query_failed"))
            if not scope_complete:
                status = "partial" if successful_pools else "unavailable"
        else:
            status = "partial" if truncated else "observed"
            reason = "target_limit_exceeded" if truncated else None
        return {
            "active": active,
            "dropped": dropped,
            "inventory": {
                "status": status,
                "complete": not scoped and scope_complete,
                "scope_complete": scope_complete,
                "scope": "scrape_pools" if scoped else "namespace",
                "reason": reason,
                "requested_scrape_pools": selected_pools,
                "omitted_scrape_pools": omitted_pools,
                "response_limited_pools": failed_pools,
                "omitted_active_targets": max(0, len(active_all) - len(active)),
                "omitted_dropped_targets": max(0, len(dropped_all) - len(dropped)),
            },
        }

    def alert_rules(self) -> dict[str, dict[str, Any]]:
        """Return Prometheus alert definitions keyed by alert name."""

        data = self._api("/api/v1/rules", {"type": "alert"}) or {}
        definitions: dict[str, dict[str, Any]] = {}
        for group in data.get("groups", []):
            for rule in group.get("rules", []):
                name = str(rule.get("name", "")).strip()
                if not name or str(rule.get("type", "alerting")) != "alerting":
                    continue
                definition = {
                    "name": name,
                    "query": str(rule.get("query", "")),
                    "duration": float(rule.get("duration", 0) or 0),
                    "keep_firing_for": float(rule.get("keepFiringFor", 0) or 0),
                    "labels": dict(rule.get("labels", {})),
                    "annotations": dict(rule.get("annotations", {})),
                    "health": str(rule.get("health", "unknown")),
                    "last_error": str(rule.get("lastError", "")),
                    "group": str(group.get("name", "")),
                    "file": str(group.get("file", "")),
                }
                if name in definitions:
                    # Alertmanager labels alone cannot disambiguate same-name rules.
                    definitions[name]["ambiguous"] = True
                else:
                    definitions[name] = definition
        return definitions

    def collect_alert_metrics(
        self, alert: dict[str, Any], namespace: str, pod: str, start: datetime, end: datetime,
    ) -> dict[str, Any]:
        """Capture bounded underlying values from a source-discovered rule, never a model query.

        Failure is evidence of unavailable coverage, not an empty healthy graph.
        This method only reads the configured Prometheus; it never follows alert URLs.
        """
        rule = alert.get("rule") if isinstance(alert.get("rule"), dict) else {}
        evidence: dict[str, Any] = {
            "status": "unavailable", "reason": None, "signal_origin": "alert_rule",
            "alertname": alert.get("alertname"), "alert_timestamp": alert.get("startsAt"),
            "rule": rule, "labels": dict(alert.get("labels", {})),
            "source": {"adapter": "prometheus", "endpoint": "/api/v1/query_range",
                       "captured_at": _timestamp(time.time()), "capture_mode": "incident_capture"},
            "time_range": {"start": _timestamp(start.timestamp()), "end": _timestamp(end.timestamp())},
        }
        result: dict[str, Any] = {"series": [], "alert_evidence": evidence}
        duration = (end - start).total_seconds()
        if not 0 < duration <= MAX_ALERT_WINDOW_SECONDS:
            evidence["reason"] = "capture_window_out_of_bounds"
            return result
        try:
            evidence.update(plan_alert_expression(rule, alert.get("labels", {}), namespace, pod))
        except UnavailableExpression as exc:
            evidence["reason"] = str(exc)
            return result
        if evidence.get("capture_mode") == "primary_threshold_series":
            evidence["source"]["capture_mode"] = "primary_threshold_series"
            evidence["source"]["rule_qualifier_count"] = evidence.get("rule_qualifier_count", 0)
            evidence["source"]["capture_note"] = (
                "Primary threshold series only; the full alert rule includes additional AND conditions "
                "that are retained as rule metadata but not graphed."
            )
        step = max(15, math.ceil(duration / (MAX_ALERT_POINTS - 1)))
        evidence["step_seconds"] = step
        try:
            data = self._api("/api/v1/query_range", {
                "query": evidence["expression"], "start": start.timestamp(), "end": end.timestamp(),
                "step": step, "timeout": "4s", "limit": MAX_ALERT_SERIES + 1,
            }) or {}
        except ResponseTooLargeError:
            evidence["reason"] = "response_byte_limit"
            evidence["source"]["response_limit_bytes"] = MAX_PROMETHEUS_RANGE_RESPONSE_BYTES
            source_evidence = {
                key: value for key, value in evidence.items() if key != "underlying_expression"
            }
            source_capture = self._collect_alert_source_metrics(
                alert, namespace, pod, start, end, step, source_evidence,
            )
            evidence["source_metric_capture"] = source_capture["evidence"]
            result["series"].extend(source_capture["series"])
            return result
        except (RuntimeError, OSError, ValueError):
            evidence["reason"] = "query_failed"
            return result
        if not isinstance(data, dict):
            evidence["reason"] = "unsupported_query_result"
            return result
        results = data.get("result", [])
        if data.get("resultType") != "matrix" or not isinstance(results, list):
            evidence["reason"] = "unsupported_query_result"
            return result
        if len(results) > MAX_ALERT_SERIES:
            evidence["reason"] = "series_limit_exceeded"
            return result
        try:
            for item in results:
                if item.get("histograms"):
                    evidence["reason"] = "native_histogram_values_unsupported"
                    result["series"] = []
                    return result
                labels = {str(key): str(value) for key, value in item.get("metric", {}).items()}
                if any(key in labels and labels[key] != expected for key, expected in evidence["scope"].items()):
                    evidence["reason"] = "result_outside_incident_scope"
                    result["series"] = []
                    return result
                samples = item.get("values", [])
                if len(samples) > MAX_ALERT_POINTS:
                    raise ValueError("too many samples")
                grid = [start.timestamp() + offset * step for offset in range(math.floor(duration / step) + 1)]
                by_position = {}
                for stamp, raw_value in samples:
                    stamp, value = float(stamp), float(raw_value)
                    if not math.isfinite(stamp) or not start.timestamp() - 0.001 <= stamp <= end.timestamp() + 0.001:
                        raise ValueError("sample outside window")
                    position = round((stamp - start.timestamp()) / step)
                    if abs(grid[position] - stamp) > 0.001 or position in by_position:
                        raise ValueError("invalid sample timestamp")
                    by_position[position] = [_timestamp(stamp), value if math.isfinite(value) else None]
                values = [by_position.get(position, [_timestamp(stamp), None]) for position, stamp in enumerate(grid)]
                if not any(point[1] is not None for point in values):
                    continue
                rule_identity = {key: rule.get(key) for key in ("name", "query", "duration", "keep_firing_for", "group", "file", "labels")}
                identity = json.dumps([rule_identity, labels], sort_keys=True, separators=(",", ":"))
                series_id = "alert_" + hashlib.sha256(identity.encode()).hexdigest()[:16]
                result["series"].append({
                    **{key: value for key, value in evidence.items() if key not in {"status", "reason", "labels"}},
                    "series_id": series_id, "labels": labels, "values": values,
                })
        except (TypeError, ValueError, KeyError, IndexError, AttributeError):
            evidence["reason"] = "invalid_query_samples"
            result["series"] = []
            return result
        evidence["status"] = "available" if result["series"] else "unavailable"
        evidence["reason"] = None if result["series"] else "no_finite_samples"
        evidence["series_ids"] = [item["series_id"] for item in result["series"]]
        source_capture = self._collect_alert_source_metrics(alert, namespace, pod, start, end, step, evidence)
        evidence["source_metric_capture"] = source_capture["evidence"]
        result["series"].extend(source_capture["series"])
        return result

    def _collect_alert_source_metrics(
        self, alert: dict[str, Any], namespace: str, pod: str, start: datetime, end: datetime,
        alert_step_seconds: int, alert_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """Capture bounded raw selector inputs referenced by the configured alert rule.

        This is deliberately not metric discovery: the rule is the source catalog,
        every query is reduced to one exact namespace/pod selector, and guard values
        are retained without reapplying the alert's comparison or threshold.
        """
        alert_labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
        service = str(alert_labels.get("service") or "")
        summary: dict[str, Any] = {
            "status": "unavailable", "reason": None,
            "source": {"adapter": "prometheus", "endpoint": "/api/v1/query_range",
                       "capture_mode": "alert_rule_source_metrics", "captured_at": _timestamp(time.time())},
            "scope": {"namespace": namespace, **({"pod": pod} if pod else ({"service": service} if service else {}))},
            "selector_limit": MAX_ALERT_SOURCE_SELECTORS,
            "query_group_limit": MAX_ALERT_SOURCE_QUERY_GROUPS,
            "series_limit": MAX_ALERT_SOURCE_SERIES,
            "point_limit": MAX_ALERT_SOURCE_POINTS,
            "candidate_selector_count": 0, "selected_selector_count": 0,
            "candidate_group_count": 0, "selected_group_count": 0, "processed_group_count": 0,
            "captured_series_count": 0, "omitted_selector_count": 0,
            "omitted_group_count": 0, "omitted_series_at_least": 0,
            "omitted_missing_or_non_finite_points": 0,
            "rejected_selector_count": 0, "rejected_label_series_count": 0, "query_failure_count": 0,
            "response_byte_limit_count": 0,
            "response_limit_bytes": MAX_PROMETHEUS_RANGE_RESPONSE_BYTES,
            "truncated": False,
            "note": "Raw source selector samples; alert aggregation, comparison, and threshold are not reapplied.",
        }
        empty = {"series": [], "evidence": summary}
        rule = alert.get("rule") if isinstance(alert.get("rule"), dict) else {}
        query = rule.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 4096:
            summary["reason"] = "rule_definition_unavailable"
            return empty
        try:
            root = promql.parse(query)
            nodes: list[Any] = []
            promql.walk(root, pre_visit=lambda node: nodes.append(node))
        except ValueError:
            summary["reason"] = "invalid_or_unsupported_promql"
            return empty

        direct_threshold_signature = None
        underlying = alert_evidence.get("underlying_expression")
        if isinstance(underlying, str):
            try:
                threshold_node = promql.parse(underlying)
                while isinstance(threshold_node, promql.ParenExpr):
                    threshold_node = threshold_node.expr
                if isinstance(threshold_node, promql.VectorSelector):
                    direct_threshold_signature = _selector_signature(threshold_node)
            except ValueError:
                pass

        candidates: list[str] = []
        seen: set[str] = set()
        rejected = 0
        for node in nodes:
            if not isinstance(node, promql.VectorSelector):
                continue
            if not node.name or node.name == "up":
                continue
            if direct_threshold_signature is not None and _selector_signature(node) == direct_threshold_signature:
                continue
            selector = _scoped_source_selector(node, namespace, pod, service)
            if selector is None or len(selector) > MAX_ALERT_SOURCE_SELECTOR_CHARS:
                rejected += 1
                continue
            if selector not in seen:
                seen.add(selector)
                candidates.append(selector)

        summary["candidate_selector_count"] = len(candidates)
        summary["rejected_selector_count"] = rejected
        if not candidates:
            summary["status"] = "not_applicable"
            summary["reason"] = "no_additional_safe_source_selectors"
            return empty

        candidate_count = len(candidates)
        candidates = candidates[:MAX_ALERT_SOURCE_SELECTORS]
        groups_by_matchers: dict[tuple[Any, ...], dict[str, Any]] = {}
        groups: list[dict[str, Any]] = []
        for selector in candidates:
            parsed_selector = promql.parse(selector)
            matcher_key = tuple(sorted(
                (matcher.name, str(matcher.op), matcher.value)
                for matcher in parsed_selector.matchers.matchers
            ))
            group = groups_by_matchers.get(matcher_key)
            if group is None:
                group = {"matchers": parsed_selector.matchers.matchers, "selectors": {}}
                groups_by_matchers[matcher_key] = group
                groups.append(group)
            group["selectors"][parsed_selector.name] = selector

        summary["candidate_group_count"] = len(groups)
        selected_groups: list[dict[str, Any]] = []
        query_char_budget = MAX_ALERT_SOURCE_EXPRESSION_CHARS
        for group in groups:
            if len(selected_groups) >= MAX_ALERT_SOURCE_QUERY_GROUPS:
                continue
            expression = _source_metric_family_expression(list(group["selectors"]), group["matchers"])
            if expression is None or len(expression) > query_char_budget:
                continue
            group["expression"] = expression
            selected_groups.append(group)
            query_char_budget -= len(expression)
        selected_selector_count = sum(len(group["selectors"]) for group in selected_groups)
        summary["selected_group_count"] = len(selected_groups)
        summary["selected_selector_count"] = selected_selector_count
        summary["omitted_selector_count"] = candidate_count - selected_selector_count
        summary["omitted_group_count"] = len(groups) - len(selected_groups)
        summary["truncated"] = summary["omitted_selector_count"] > 0
        summary["metric_names"] = sorted({
            metric_name for group in selected_groups for metric_name in group["selectors"]
        })
        summary["query_groups"] = [{
            "index": index, "metric_names": sorted(group["selectors"]),
            "selector_count": len(group["selectors"]),
        } for index, group in enumerate(selected_groups)]
        if not selected_groups:
            summary["status"] = "unavailable"
            summary["reason"] = "source_expression_limit_exceeded"
            return empty

        duration = (end - start).total_seconds()
        step = max(alert_step_seconds, math.ceil(duration / (MAX_ALERT_SOURCE_POINTS - 1)))
        summary["step_seconds"] = step
        scope = summary["scope"]
        grid = [start.timestamp() + offset * step for offset in range(math.floor(duration / step) + 1)]
        captured: list[dict[str, Any]] = []
        invalid_series = 0
        seen_series: set[tuple[str, str]] = set()
        for group_index, group in enumerate(selected_groups):
            remaining = MAX_ALERT_SOURCE_SERIES - len(captured)
            if remaining <= 0:
                summary["truncated"] = True
                summary["omitted_group_count"] += len(selected_groups) - group_index
                break
            try:
                data = self._api("/api/v1/query_range", {
                    "query": group["expression"], "start": start.timestamp(), "end": end.timestamp(),
                    "step": step, "timeout": "4s", "limit": remaining + 1,
                }) or {}
            except ResponseTooLargeError:
                summary["response_byte_limit_count"] += 1
                continue
            except (RuntimeError, OSError, ValueError):
                summary["query_failure_count"] += 1
                continue
            if not isinstance(data, dict) or data.get("resultType") != "matrix" or not isinstance(data.get("result"), list):
                summary["query_failure_count"] += 1
                continue
            summary["processed_group_count"] += 1
            results = data["result"]
            over_limit = len(results) > remaining
            if over_limit:
                summary["truncated"] = True
                summary["omitted_series_at_least"] += len(results) - remaining
            for item in results[:remaining]:
                if not isinstance(item, dict) or item.get("histograms"):
                    invalid_series += 1
                    continue
                raw_labels = item.get("metric")
                if not isinstance(raw_labels, dict):
                    invalid_series += 1
                    continue
                labels = {str(key): str(value) for key, value in raw_labels.items()}
                if (len(labels) > MAX_ALERT_SOURCE_LABELS
                        or sum(len(key) + len(value) for key, value in labels.items()) > MAX_ALERT_SOURCE_LABEL_CHARS):
                    invalid_series += 1
                    summary["rejected_label_series_count"] += 1
                    continue
                if (any(labels.get(key) != expected for key, expected in scope.items())
                        or (service and labels.get("service") and labels["service"] != service)):
                    summary["reason"] = "result_outside_incident_scope"
                    summary["status"] = "unavailable"
                    return empty
                metric_name = labels.get("__name__")
                selector = group["selectors"].get(metric_name)
                if not metric_name or not selector:
                    invalid_series += 1
                    continue
                result_identity = (metric_name, json.dumps(labels, sort_keys=True, separators=(",", ":")))
                if result_identity in seen_series:
                    continue
                seen_series.add(result_identity)
                samples = item.get("values", [])
                if not isinstance(samples, list) or len(samples) > MAX_ALERT_SOURCE_POINTS:
                    invalid_series += 1
                    continue
                by_position: dict[int, list[Any]] = {}
                try:
                    for stamp, raw_value in samples:
                        stamp = float(stamp)
                        value = float(raw_value)
                        if not math.isfinite(stamp) or not start.timestamp() - 0.001 <= stamp <= end.timestamp() + 0.001:
                            raise ValueError("sample outside source window")
                        position = round((stamp - start.timestamp()) / step)
                        if position >= len(grid) or abs(grid[position] - stamp) > 0.001 or position in by_position:
                            raise ValueError("invalid source sample timestamp")
                        if math.isfinite(value):
                            by_position[position] = [_timestamp(stamp), value]
                except (TypeError, ValueError, IndexError):
                    invalid_series += 1
                    continue
                if len(by_position) < 2:
                    invalid_series += 1
                    continue
                values = [by_position[position] for position in sorted(by_position)]
                omitted_points = len(grid) - len(values)
                summary["omitted_missing_or_non_finite_points"] += omitted_points
                identity = json.dumps([
                    rule.get("name"), rule.get("query"), selector, labels,
                ], sort_keys=True, separators=(",", ":"))
                captured.append({
                    "metric": metric_name,
                    "signal_origin": "alert_rule_source",
                    "series_id": "alert_source_" + hashlib.sha256(identity.encode()).hexdigest()[:16],
                    "alertname": alert.get("alertname"),
                    "alert_timestamp": alert.get("startsAt"),
                    "rule_name": rule.get("name"),
                    "labels": labels,
                    "scope": scope,
                    "time_range": {"start": _timestamp(start.timestamp()), "end": _timestamp(end.timestamp())},
                    "step_seconds": step,
                    "source": {
                        **summary["source"],
                        "selector": selector,
                        "query_group": group_index,
                        "omitted_missing_or_non_finite_points": omitted_points,
                        "note": summary["note"],
                    },
                    "values": values,
                })
            if over_limit:
                break
        summary["captured_series_count"] = len(captured)
        summary["series_ids"] = [series["series_id"] for series in captured]
        summary["invalid_series_count"] = invalid_series
        if captured:
            summary["status"] = "partial" if (
                summary["truncated"] or invalid_series or summary["query_failure_count"]
                or summary["response_byte_limit_count"]
            ) else "available"
            if summary["truncated"]:
                summary["reason"] = "capture_limit_reached"
            elif summary["response_byte_limit_count"]:
                summary["reason"] = "response_byte_limit"
            elif invalid_series or summary["query_failure_count"]:
                summary["reason"] = "some_query_results_unavailable"
        else:
            summary["status"] = "unavailable"
            summary["reason"] = "response_byte_limit" if summary["response_byte_limit_count"] else (
                "query_failed" if summary["query_failure_count"] else
                "no_finite_samples" if not invalid_series else "invalid_query_samples"
            )
        return {"series": captured, "evidence": summary}

    def pod_inventory(self, namespaces: set[str] | None = None) -> dict[tuple[str, str], dict[str, str]]:
        inventory: dict[tuple[str, str], dict[str, str]] = {}
        for item in self.query("kube_pod_info"):
            labels = {str(key): str(value) for key, value in item.get("metric", {}).items()}
            namespace = labels.get("namespace")
            pod = labels.get("pod")
            if not namespace or not pod or (namespaces and namespace not in namespaces):
                continue
            inventory[(namespace, pod)] = labels
        return inventory

    def collect_pod_metrics(
        self,
        namespace: str,
        pod: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        selector = f'namespace="{_promql_escape(namespace)}",pod="{_promql_escape(pod)}"'
        expressions = {
            "pod_cpu_cores": f'sum(rate(container_cpu_usage_seconds_total{{{selector},container!="",image!=""}}[2m])) by (namespace,pod)',
            "pod_memory_working_set_bytes": f'sum(container_memory_working_set_bytes{{{selector},container!="",image!=""}}) by (namespace,pod)',
            "pod_container_restarts_total": f'sum(kube_pod_container_status_restarts_total{{{selector}}}) by (namespace,pod)',
            "pod_ready": f'min(kube_pod_status_ready{{{selector},condition="true"}}) by (namespace,pod)',
            "pod_memory_limit_bytes": f'sum(kube_pod_container_resource_limits{{{selector},resource="memory"}}) by (namespace,pod)',
            "pod_cpu_limit_cores": f'sum(kube_pod_container_resource_limits{{{selector},resource="cpu"}}) by (namespace,pod)',
            "pod_cpu_throttled_ratio": f'sum(rate(container_cpu_cfs_throttled_periods_total{{{selector},container!=""}}[2m])) / clamp_min(sum(rate(container_cpu_cfs_periods_total{{{selector},container!=""}}[2m])), 0.000001)',
            "pod_oom_terminated": f'max(kube_pod_container_status_last_terminated_reason{{{selector},reason="OOMKilled"}}) by (namespace,pod)',
        }
        duration = max(1, int((end - start).total_seconds()))
        step = max(15, min(60, duration // 30 or 15))
        series: list[dict[str, Any]] = []
        response_limited_metrics: list[str] = []
        successful_queries = 0
        for metric_name, expression in expressions.items():
            try:
                results = self.query_range(expression, start, end, step)
            except ResponseTooLargeError:
                response_limited_metrics.append(metric_name)
                continue
            successful_queries += 1
            for result in results:
                points = [[_timestamp(float(timestamp)), float(value)] for timestamp, value in result.get("values", []) if math.isfinite(float(value))]
                if len(points) < 2:
                    continue
                labels = {str(key): str(value) for key, value in result.get("metric", {}).items()}
                labels.update({"namespace": namespace, "pod": pod})
                series.append({"metric": metric_name, "labels": labels, "values": points})
        response_limited = bool(response_limited_metrics)
        self.last_pod_metric_capture_info = {
            "status": "unavailable" if response_limited and not successful_queries else (
                "partial" if response_limited else "complete"
            ),
            "available": bool(successful_queries),
            "truncated": response_limited,
            "query_count": len(expressions),
            "successful_query_count": successful_queries,
            "response_byte_limit_count": len(response_limited_metrics),
            "response_limited_metrics": response_limited_metrics,
            "response_limit_bytes": MAX_PROMETHEUS_RANGE_RESPONSE_BYTES,
            "retained_series": len(series),
            "reason": "response_byte_limit" if response_limited else None,
        }
        return series


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _response_limit(path: str) -> int:
    endpoint = path.split("?", 1)[0]
    if endpoint == "/api/v1/query_range":
        return MAX_PROMETHEUS_RANGE_RESPONSE_BYTES
    if endpoint == "/api/v1/query":
        return MAX_PROMETHEUS_QUERY_RESPONSE_BYTES
    if endpoint in {"/api/v1/alerts", "/api/v1/targets", "/api/v1/rules"}:
        return MAX_PROMETHEUS_DISCOVERY_RESPONSE_BYTES
    if endpoint.startswith("/api/v1/status/"):
        return MAX_PROMETHEUS_STATUS_RESPONSE_BYTES
    return MAX_PROMETHEUS_DEFAULT_RESPONSE_BYTES


def _selector_signature(selector: Any) -> tuple[Any, ...]:
    matchers = tuple(sorted((matcher.name, str(matcher.op), matcher.value) for matcher in selector.matchers.matchers))
    or_matchers = tuple(sorted(
        tuple(sorted((matcher.name, str(matcher.op), matcher.value) for matcher in group))
        for group in selector.matchers.or_matchers
    ))
    return selector.name, str(selector.offset), str(selector.at), matchers, or_matchers


def _source_metric_family_expression(metric_names: list[str], matchers: list[Any]) -> str | None:
    if not metric_names or any(matcher.name == "__name__" for matcher in matchers):
        return None
    metric_pattern = "(" + "|".join(re.escape(name) for name in sorted(set(metric_names))) + ")"
    all_matchers = [promql.Matcher(promql.MatchOp.Re, "__name__", metric_pattern), *matchers]
    expression = "{" + ",".join(
        matcher.name + _MATCHER_SYMBOLS[str(matcher.op)] + json.dumps(matcher.value)
        for matcher in all_matchers
    ) + "}"
    try:
        parsed = promql.parse(expression)
    except ValueError:
        return None
    return expression if isinstance(parsed, promql.VectorSelector) and not parsed.name else None


def _scoped_source_selector(selector: Any, namespace: str, pod: str, service: str) -> str | None:
    if (not namespace or (not pod and not service) or not selector.name or selector.offset is not None
            or selector.at is not None or selector.matchers.or_matchers):
        return None
    expected = {"namespace": namespace}
    if pod:
        expected["pod"] = pod
    elif service:
        expected["service"] = service
    for matcher in selector.matchers.matchers:
        if matcher.name == "__name__":
            return None
        if matcher.name == "namespace":
            if matcher.op != promql.MatchOp.Equal or matcher.value != namespace:
                return None
        elif matcher.name == "pod":
            if not pod or matcher.op != promql.MatchOp.Equal or matcher.value != pod:
                return None
        elif matcher.name == "service" and service:
            if matcher.op != promql.MatchOp.Equal or matcher.value != service:
                return None
    matchers = list(selector.matchers.matchers)
    for name, value in expected.items():
        if not any(matcher.name == name and matcher.op == promql.MatchOp.Equal and matcher.value == value
                   for matcher in matchers):
            matchers.append(promql.Matcher(promql.MatchOp.Equal, name, value))
    expression = selector.name + "{" + ",".join(
        matcher.name + _MATCHER_SYMBOLS[str(matcher.op)] + json.dumps(matcher.value)
        for matcher in matchers
    ) + "}"
    try:
        parsed = promql.parse(expression)
    except ValueError:
        return None
    return expression if isinstance(parsed, promql.VectorSelector) else None


def _promql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
