"""Read-only Prometheus HTTP API adapter."""

from __future__ import annotations

import time
import math
import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from fcapsule.adapters.transport import JsonTransport
from fcapsule.adapters.alert_expression import UnavailableExpression, plan_alert_expression

MAX_ALERT_SERIES = 12
MAX_ALERT_POINTS = 241
MAX_ALERT_WINDOW_SECONDS = 4 * 60 * 60


class PrometheusAdapter:
    """Query alerts, targets, pod identity, and bounded metric ranges."""

    def __init__(self, base_url: str, timeout: float = 8) -> None:
        self.base_url = base_url.rstrip("/")
        self.transport = JsonTransport(self.base_url, timeout=timeout)

    def _api(self, path: str, params: dict[str, Any] | None = None) -> Any:
        suffix = path
        if params:
            suffix += "?" + urlencode(params)
        payload = self.transport.request(suffix)
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
        build = self.transport.request("/api/v1/status/buildinfo")
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

    def scrape_targets(self, namespace: str, pods: set[str] | None = None) -> dict[str, list[dict[str, Any]]]:
        """Return a bounded, label-only view of Prometheus target discovery.

        `up == 0` describes a contacted target. A dropped target or the absence of
        a discovered target describes a different failure mode, so both remain
        explicit rather than being collapsed into one health flag.
        """

        data = self._api("/api/v1/targets") or {}
        expected = pods or set()

        def normalized(item: dict[str, Any], state: str) -> dict[str, Any] | None:
            labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
            discovered = item.get("discoveredLabels") if isinstance(item.get("discoveredLabels"), dict) else {}
            combined = {str(key): str(value) for key, value in {**discovered, **labels}.items()}
            target_namespace = combined.get("namespace") or combined.get("__meta_kubernetes_namespace") or ""
            pod = combined.get("pod") or combined.get("__meta_kubernetes_pod_name") or ""
            service = combined.get("service") or combined.get("__meta_kubernetes_service_name") or ""
            if target_namespace != namespace and pod not in expected:
                return None
            return {
                "state": state,
                "health": str(item.get("health") or "unknown"),
                "last_error": str(item.get("lastError") or "")[:400],
                "scrape_pool": str(item.get("scrapePool") or ""),
                "job": str(labels.get("job") or combined.get("job") or ""),
                "namespace": target_namespace,
                "pod": pod,
                "service": service,
                "labels": {key: value for key, value in combined.items() if key in {
                    "namespace", "pod", "service", "job", "instance", "__meta_kubernetes_namespace",
                    "__meta_kubernetes_pod_name", "__meta_kubernetes_service_name", "__meta_kubernetes_service_label_fcapsule_io_app_metrics",
                }},
            }

        active = [value for item in data.get("activeTargets", []) if isinstance(item, dict)
                  if (value := normalized(item, "active")) is not None][:40]
        dropped = [value for item in data.get("droppedTargets", []) if isinstance(item, dict)
                   if (value := normalized(item, "dropped")) is not None][:40]
        return {"active": active, "dropped": dropped}

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
        step = max(15, math.ceil(duration / (MAX_ALERT_POINTS - 1)))
        evidence["step_seconds"] = step
        try:
            data = self._api("/api/v1/query_range", {
                "query": evidence["expression"], "start": start.timestamp(), "end": end.timestamp(),
                "step": step, "timeout": "4s", "limit": MAX_ALERT_SERIES + 1,
            }) or {}
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
        return result

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
        for metric_name, expression in expressions.items():
            for result in self.query_range(expression, start, end, step):
                points = [[_timestamp(float(timestamp)), float(value)] for timestamp, value in result.get("values", []) if math.isfinite(float(value))]
                if len(points) < 2:
                    continue
                labels = {str(key): str(value) for key, value in result.get("metric", {}).items()}
                labels.update({"namespace": namespace, "pod": pod})
                series.append({"metric": metric_name, "labels": labels, "values": points})
        return series


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _promql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
