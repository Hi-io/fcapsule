"""Live source discovery and bounded incident capture orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from fcapsule.adapters import KubernetesAdapter, OpenSearchAdapter, PrometheusAdapter
from fcapsule.adapters.opensearch_adapter import (
    DEFAULT_MAX_COLLECTION_BYTES,
    DEFAULT_MAX_MESSAGE_BYTES,
    DEFAULT_MAX_RESPONSE_BYTES,
)
from fcapsule.io.output_writer import write_json
from fcapsule.models.schemas import parse_timestamp
from fcapsule.store import FCAPSuleStore, utc_now

SOURCE_SETTING = "live_source_configuration"
GRAFANA_ALERT_SETTING = "grafana_firing_alerts"
GRAFANA_RECEIPT_SETTING = "grafana_last_received_at"
IGNORED_ALERTS = {"Watchdog", "InfoInhibitor"}
MAX_GROUP_PODS = 4
DEFAULT_IDENTITY_LABELS = [
    {"name": "CNFC", "alert_label": "cnfc", "pod_label": "cnfc"},
    {"name": "VNFC", "alert_label": "vnfc", "pod_label": "vnfc"},
]


def default_source_configuration() -> dict[str, Any]:
    return {
        "enabled": os.environ.get("FCAPSULE_LIVE_ENABLED", "false").lower() in {"1", "true", "yes"},
        "cluster_name": os.environ.get("FCAPSULE_CLUSTER_NAME", "kubernetes"),
        "environment": os.environ.get("FCAPSULE_ENVIRONMENT", "development"),
        "prometheus_url": os.environ.get(
            "FCAPSULE_PROMETHEUS_URL",
            "http://prometheus-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090",
        ),
        "opensearch_url": os.environ.get("FCAPSULE_OPENSEARCH_URL", "http://opensearch.logging.svc.cluster.local:9200"),
        "opensearch_index": os.environ.get("FCAPSULE_OPENSEARCH_INDEX", "k8s-logs-*"),
        "opensearch_max_message_bytes": _environment_limit(
            "FCAPSULE_OPENSEARCH_MAX_MESSAGE_BYTES", DEFAULT_MAX_MESSAGE_BYTES, 256, 256 * 1024
        ),
        "opensearch_max_collection_bytes": _environment_limit(
            "FCAPSULE_OPENSEARCH_MAX_COLLECTION_BYTES", DEFAULT_MAX_COLLECTION_BYTES, 4096, 16 * 1024 * 1024
        ),
        "opensearch_max_response_bytes": _environment_limit(
            "FCAPSULE_OPENSEARCH_MAX_RESPONSE_BYTES", DEFAULT_MAX_RESPONSE_BYTES, 4096, 64 * 1024 * 1024
        ),
        "kubernetes_url": os.environ.get("FCAPSULE_KUBERNETES_URL", ""),
        "namespaces": [value.strip() for value in os.environ.get("FCAPSULE_NAMESPACES", "default").split(",") if value.strip()],
        "poll_interval_seconds": int(os.environ.get("FCAPSULE_POLL_INTERVAL_SECONDS", "30")),
        "incident_window_minutes": int(os.environ.get("FCAPSULE_INCIDENT_WINDOW_MINUTES", "10")),
        "auto_build_reports": os.environ.get("FCAPSULE_AUTO_BUILD_REPORTS", "true").lower() in {"1", "true", "yes"},
        "grafana_webhook_enabled": False,
        "identity_labels": DEFAULT_IDENTITY_LABELS,
    }


class LiveSourceCoordinator:
    def __init__(self, store: FCAPSuleStore, state_dir: Path) -> None:
        self.store = store
        self.state_dir = state_dir
        self.case_root = state_dir / "live-cases"
        self.case_root.mkdir(parents=True, exist_ok=True)
        self.webhook_lock = threading.RLock()

    def configuration(self) -> dict[str, Any]:
        defaults = default_source_configuration()
        saved = self.store.get_setting(SOURCE_SETTING)
        if saved:
            try:
                defaults.update(json.loads(saved))
            except json.JSONDecodeError:
                pass
        defaults["opensearch_credentials_configured"] = bool(os.environ.get("OPENSEARCH_USERNAME"))
        defaults["grafana_webhook_token_configured"] = bool(os.environ.get("FCAPSULE_GRAFANA_WEBHOOK_TOKEN"))
        defaults["grafana_last_received_at"] = self.store.get_setting(GRAFANA_RECEIPT_SETTING)
        return defaults

    def update_configuration(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.configuration()
        namespaces = payload.get("namespaces", current["namespaces"])
        if isinstance(namespaces, str):
            namespaces = [value.strip() for value in namespaces.split(",") if value.strip()]
        if not isinstance(namespaces, list):
            raise ValueError("namespaces must be a comma-separated string or list")
        updated = {
            "enabled": bool(payload.get("enabled", current["enabled"])),
            "cluster_name": str(payload.get("cluster_name", current["cluster_name"])).strip(),
            "environment": str(payload.get("environment", current["environment"])).strip(),
            "prometheus_url": _url(payload.get("prometheus_url", current["prometheus_url"]), "Prometheus URL"),
            "opensearch_url": _url(payload.get("opensearch_url", current["opensearch_url"]), "OpenSearch URL"),
            "opensearch_index": str(payload.get("opensearch_index", current["opensearch_index"])).strip(),
            "opensearch_max_message_bytes": _bounded_int(
                payload.get("opensearch_max_message_bytes", current["opensearch_max_message_bytes"]), 256, 256 * 1024
            ),
            "opensearch_max_collection_bytes": _bounded_int(
                payload.get("opensearch_max_collection_bytes", current["opensearch_max_collection_bytes"]), 4096, 16 * 1024 * 1024
            ),
            "opensearch_max_response_bytes": _bounded_int(
                payload.get("opensearch_max_response_bytes", current["opensearch_max_response_bytes"]), 4096, 64 * 1024 * 1024
            ),
            "kubernetes_url": str(payload.get("kubernetes_url", current["kubernetes_url"])).strip(),
            "namespaces": sorted(set(str(value).strip() for value in namespaces if str(value).strip())),
            "poll_interval_seconds": min(3600, max(10, int(payload.get("poll_interval_seconds", current["poll_interval_seconds"])))),
            "incident_window_minutes": min(120, max(2, int(payload.get("incident_window_minutes", current["incident_window_minutes"])))),
            "auto_build_reports": bool(payload.get("auto_build_reports", current["auto_build_reports"])),
            "grafana_webhook_enabled": bool(payload.get("grafana_webhook_enabled", current["grafana_webhook_enabled"])),
            "identity_labels": _identity_labels(payload.get("identity_labels", current["identity_labels"])),
        }
        if not updated["cluster_name"]:
            raise ValueError("Cluster name is required")
        if not updated["opensearch_index"] or any(character.isspace() for character in updated["opensearch_index"]):
            raise ValueError("OpenSearch index pattern cannot be empty or contain spaces")
        if updated["grafana_webhook_enabled"] and not os.environ.get("FCAPSULE_GRAFANA_WEBHOOK_TOKEN"):
            raise ValueError("Set FCAPSULE_GRAFANA_WEBHOOK_TOKEN before enabling the Grafana webhook")
        self.store.set_setting(SOURCE_SETTING, json.dumps(updated, separators=(",", ":")))
        if current["grafana_webhook_enabled"] and not updated["grafana_webhook_enabled"]:
            with self.webhook_lock:
                self.store.set_setting(GRAFANA_ALERT_SETTING, "{}")
        config_path = self.state_dir / "source-settings.json"
        write_json(config_path, updated)
        return self.configuration()

    def adapters(self, config: dict[str, Any] | None = None) -> tuple[PrometheusAdapter, OpenSearchAdapter, KubernetesAdapter]:
        config = config or self.configuration()
        prometheus = PrometheusAdapter(config["prometheus_url"])
        opensearch = OpenSearchAdapter(
            config["opensearch_url"],
            config["opensearch_index"],
            os.environ.get("OPENSEARCH_USERNAME"),
            os.environ.get("OPENSEARCH_PASSWORD"),
            max_message_bytes=config["opensearch_max_message_bytes"],
            max_collection_bytes=config["opensearch_max_collection_bytes"],
            max_response_bytes=config["opensearch_max_response_bytes"],
        )
        kubernetes = KubernetesAdapter(config.get("kubernetes_url") or None)
        return prometheus, opensearch, kubernetes

    def test_connections(self) -> dict[str, Any]:
        prometheus, opensearch, kubernetes = self.adapters()
        targets: dict[str, Any] = {}
        for name, probe in (
            ("prometheus", prometheus.test_connection),
            ("opensearch", opensearch.test_connection),
            ("kubernetes", kubernetes.test_connection),
        ):
            try:
                targets[name] = probe()
            except Exception as exc:
                targets[name] = {"ok": False, "error": str(exc)}
        return {"tested_at": utc_now(), "targets": targets, "ok": all(item.get("ok") for item in targets.values())}

    def receive_grafana_alerts(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Retain only active, normalized webhook alerts until their resolve notification."""
        from fcapsule.adapters.grafana_webhook_adapter import normalize_notification

        if not self.configuration()["grafana_webhook_enabled"]:
            raise ValueError("Grafana webhook is disabled")
        changes = normalize_notification(payload)
        with self.webhook_lock:
            current = self._grafana_alerts()
            for key, alert in changes:
                if alert is None:
                    current.pop(key, None)
                else:
                    alert["received_at"] = utc_now()
                    current[key] = alert
            if len(current) > 100:
                current = dict(sorted(current.items(), key=lambda item: item[1]["startsAt"], reverse=True)[:100])
            self.store.set_setting(GRAFANA_ALERT_SETTING, json.dumps(current, separators=(",", ":")))
            received_at = utc_now()
            self.store.set_setting(GRAFANA_RECEIPT_SETTING, received_at)
        return {"accepted": len(changes), "firing": len(current)}

    def _grafana_alerts(self) -> dict[str, dict[str, Any]]:
        try:
            parsed = json.loads(self.store.get_setting(GRAFANA_ALERT_SETTING) or "{}")
            if not isinstance(parsed, dict):
                return {}
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            return {key: alert for key, alert in parsed.items() if isinstance(alert, dict)
                    and parse_timestamp(alert.get("received_at", alert.get("startsAt")), "grafana.received_at") >= cutoff}
        except (json.JSONDecodeError, ValueError, TypeError):
            return {}

    def synchronize(self) -> dict[str, Any]:
        config = self.configuration()
        namespaces = set(config["namespaces"]) or None
        prometheus, opensearch, kubernetes = self.adapters(config)
        probes = self.test_connections()
        if not probes["targets"]["kubernetes"].get("ok"):
            raise RuntimeError(probes["targets"]["kubernetes"].get("error", "Kubernetes API is unavailable"))

        pod_inventory = kubernetes.list_pods(namespaces)
        pod_status = getattr(pod_inventory, "status", "observed")
        if pod_status == "observed" and (getattr(pod_inventory, "complete", True) is False
                                          or getattr(pod_inventory, "has_more", False) is True):
            pod_status = "partial"
        if pod_status == "unavailable":
            raise RuntimeError("Kubernetes pod inventory is unavailable")
        pods = pod_inventory
        prometheus_inventory: dict[tuple[str, str], dict[str, str]] = {}
        log_counts: dict[tuple[str, str], int] = {}
        if probes["targets"]["prometheus"].get("ok"):
            prometheus_inventory = prometheus.pod_inventory(namespaces)
        if probes["targets"]["opensearch"].get("ok"):
            log_counts = opensearch.pod_log_counts(datetime.now(timezone.utc) - timedelta(minutes=15), namespaces)

        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for pod in pods:
            grouped[(pod["namespace"], pod["workload"])].append(pod)
        observed_app_ids: set[str] = set()
        for (namespace, workload), workload_pods in grouped.items():
            app_id = _app_id(config["cluster_name"], namespace, workload)
            observed_app_ids.add(app_id)
            metric_pods = sum((namespace, item["name"]) in prometheus_inventory for item in workload_pods)
            recent_logs = sum(log_counts.get((namespace, item["name"]), 0) for item in workload_pods)
            pod_records = [
                {
                    "name": item["name"],
                    "uid": item.get("uid"),
                    "node": item.get("node"),
                    "phase": item.get("phase"),
                    "ready": item.get("ready"),
                    "metrics_observed": (namespace, item["name"]) in prometheus_inventory,
                    "recent_log_count": log_counts.get((namespace, item["name"]), 0),
                    "identifiers": [
                        {"name": mapping["name"], "value": item.get("labels", {}).get(mapping["pod_label"])}
                        for mapping in config["identity_labels"] if item.get("labels", {}).get(mapping["pod_label"])
                    ],
                }
                for item in workload_pods
            ]
            source_config = {
                "faults": {"adapter": "prometheus", "status": "connected" if probes["targets"]["prometheus"].get("ok") else "error"},
                "metrics": {"adapter": "prometheus", "status": "observed" if metric_pods else "missing", "pods_observed": metric_pods},
                "logs": {"adapter": "opensearch", "status": "observed" if recent_logs else "waiting", "recent_documents": recent_logs},
                "configuration": {
                    "adapter": "kubernetes",
                    "status": "partial" if pod_status == "partial" else "available",
                    "pods_visible": len(workload_pods),
                    "complete": getattr(pod_inventory, "complete", True) is True,
                    "has_more": getattr(pod_inventory, "has_more", False),
                    "omitted_pods": getattr(pod_inventory, "omitted_pods", 0),
                },
                "traces": {"adapter": "not_configured", "status": "not_configured", "retain_raw_spans": False},
                "pods": pod_records,
            }
            healthy = all(item.get("ready") for item in workload_pods) and bool(metric_pods)
            self.store.upsert_application(
                app_id,
                workload,
                namespace,
                config["cluster_name"],
                config["environment"],
                "healthy" if healthy else "degraded",
                source_config,
            )
        self._mark_unobserved_applications(config, observed_app_ids)

        alerts = prometheus.active_alerts() if probes["targets"]["prometheus"].get("ok") else []
        if config["grafana_webhook_enabled"]:
            with self.webhook_lock:
                alerts.extend(self._grafana_alerts().values())
        alert_rules: dict[str, dict[str, Any]] = {}
        if probes["targets"]["prometheus"].get("ok"):
            try:
                alert_rules = prometheus.alert_rules()
            except Exception:
                # Alert capture remains available on Prometheus versions or proxies
                # that do not expose rule definitions.
                alert_rules = {}
        captured: list[dict[str, Any]] = []
        unmapped_alerts: list[dict[str, str]] = []
        active_incident_ids: set[str] = set()
        for alert in alerts:
            rule = alert_rules.get(str(alert.get("alertname", ""))) if alert.get("source") != "grafana_webhook" else None
            if rule:
                alert["rule"] = rule
            labels = alert.get("labels", {})
            namespace = str(labels.get("namespace", ""))
            if (
                str(alert.get("status", "")).lower() != "firing"
                or alert["alertname"] in IGNORED_ALERTS
                or (namespaces and namespace and namespace not in namespaces)
            ):
                continue
            service_candidates: list[dict[str, Any]] | None = None
            service_name = str(
                labels.get("target_service") or labels.get("kubernetes_service") or labels.get("service", "")
            ).strip()
            if service_name and not labels.get("target_workload"):
                try:
                    # A target-discovery alert is often scoped to a Service rather than a
                    # single pod. Resolve it through the Service selector, not its name.
                    service_candidates = kubernetes.service_pods(namespace, service_name, pods)
                except (ValueError, OSError, RuntimeError):
                    service_candidates = []
            scope = _resolve_alert_scope(pods, namespace, labels, config["identity_labels"], service_candidates)
            if scope is None:
                if len(unmapped_alerts) < 20:
                    unmapped_alerts.append({
                        "alertname": str(alert["alertname"])[:128],
                        "namespace": namespace,
                        "source": str(alert.get("source") or "prometheus"),
                        "reason": "No unique visible Kubernetes scope matched the alert labels",
                    })
                continue
            if not alert.get("startsAt"):
                continue
            scope["inventory_complete"] = pod_status == "observed"
            scope_pods = scope["pods"]
            namespace = scope_pods[0]["namespace"]
            alert["resolved_scope"] = {key: value for key, value in scope.items() if key != "pods"}
            alert["resolved_scope"]["pods"] = [item["name"] for item in scope_pods]
            incident_id = _incident_id(alert, namespace, f"{scope['kind']}:{scope['name']}")
            active_incident_ids.add(incident_id)
            if self.store.get_incident(incident_id):
                self.store.activate_live_incident(incident_id)
                continue
            workloads = {item["workload"] for item in scope_pods}
            app_name = next(iter(workloads)) if len(workloads) == 1 else f"{scope['kind'].upper()} {scope['name']}"
            app_id = _app_id(config["cluster_name"], namespace,
                             app_name if len(workloads) == 1 else f"identity-{scope['kind']}-{scope['name']}")
            if len(workloads) > 1:
                self.store.upsert_application(
                    app_id, app_name, namespace, config["cluster_name"], config["environment"],
                    "not_observed", {"configuration": {"adapter": "kubernetes", "status": "not_observed"}, "pods": []},
                )
            case_dir = self._capture_case(config, prometheus, opensearch, kubernetes, alert, scope_pods[0], incident_id, scope=scope)
            captured.append({"case_dir": str(case_dir), "app_id": app_id, "app_name": app_name})

        return {
            **probes,
            "last_sync_at": utc_now(),
            "pods_visible": len(pods),
            "applications_visible": len(grouped),
            "active_alerts": len(active_incident_ids),
            "unmapped_alerts": unmapped_alerts,
            "active_incident_ids": sorted(active_incident_ids),
            "captured": captured,
            "configuration": config,
        }

    def _mark_unobserved_applications(self, config: dict[str, Any], observed_app_ids: set[str]) -> None:
        """Keep incident history while making current discovery coverage truthful."""

        for application in self.store.list_applications():
            if application["cluster"] != config["cluster_name"] or application["app_id"] in observed_app_ids:
                continue
            source_config = application.get("source_config", {})
            if source_config.get("configuration", {}).get("adapter") != "kubernetes":
                continue
            updated = json.loads(json.dumps(source_config))
            for domain in ("metrics", "logs", "configuration"):
                if domain in updated:
                    updated[domain]["status"] = "not_observed"
            updated.get("metrics", {}).update({"pods_observed": 0})
            updated.get("logs", {}).update({"recent_documents": 0})
            updated.get("configuration", {}).update({"pods_visible": 0})
            updated["pods"] = []
            self.store.upsert_application(
                application["app_id"],
                application["name"],
                application["namespace"],
                application["cluster"],
                application["environment"],
                "not_observed",
                updated,
            )

    def _capture_case(
        self,
        config: dict[str, Any],
        prometheus: PrometheusAdapter,
        opensearch: OpenSearchAdapter,
        kubernetes: KubernetesAdapter,
        alert: dict[str, Any],
        pod: dict[str, Any],
        incident_id: str,
        *,
        scope: dict[str, Any] | None = None,
    ) -> Path:
        case_dir = self.case_root / incident_id
        if case_dir.exists():
            # Retained evidence is immutable, even if a previous store import failed.
            if all((case_dir / name).is_file() for name in (
                "metadata.yaml", "alert.json", "prometheus_metrics.json", "opensearch_logs.json", "kubernetes_config.json",
            )):
                return case_dir
            raise FileExistsError(f"Incomplete retained capture must not be overwritten: {case_dir}")
        alert_time = parse_timestamp(alert["startsAt"], "alert.startsAt")
        now = datetime.now(timezone.utc)
        window = timedelta(minutes=min(120, max(2, config["incident_window_minutes"])))
        start = alert_time - window
        end = min(max(now, alert_time + timedelta(minutes=2)), alert_time + window)
        case_dir.mkdir(parents=True, exist_ok=False)
        all_pods = scope["pods"] if scope else [pod]
        selected_pods = sorted(all_pods, key=lambda item: (bool(item.get("ready")), item["name"]))[:MAX_GROUP_PODS]
        group = scope is not None and scope["kind"] != "pod"
        metadata = {
            "case_id": incident_id,
            "case_title": alert.get("annotations", {}).get("summary") or alert["alertname"],
            "service": pod["workload"] if not group else (
                pod["workload"] if len({item["workload"] for item in all_pods}) == 1
                else f"{scope['kind'].upper()} {scope['name']}"
            ),
            "namespace": pod["namespace"],
            "cluster": config["cluster_name"],
            "pod": pod["name"] if not group else None,
            "resource_scope": {
                "kind": scope["kind"] if scope else "pod", "name": scope["name"] if scope else pod["name"],
                "matched_pods": len(all_pods), "captured_pods": len(selected_pods),
                "omitted_pods": max(0, len(all_pods) - len(selected_pods)),
                "inventory_complete": scope.get("inventory_complete") if scope else True,
                "identifiers": scope.get("identifiers", []) if scope else [],
            },
            "window": {"start": _iso(start), "end": _iso(end)},
            "timezone": "UTC",
            "telemetry_sources": {
                "alerts": "Grafana webhook" if alert.get("source") == "grafana_webhook" else "Prometheus /api/v1/alerts",
                "metrics": "Prometheus /api/v1/query_range",
                "logs": f"OpenSearch {config['opensearch_index']}",
                "configuration": "Kubernetes API",
            },
            "fields": {"log_time_field": "@timestamp", "log_message_field": "message", "log_level_field": "level"},
            "privacy": {"anonymized": True, "synthetic": False, "secrets_collected": False},
            "trace_access": {"available": False, "raw_spans_retained": False},
            "topology": [
                {"kind": "pod", "name": item["name"], "namespace": item["namespace"],
                 "node": item.get("node"), "ready": item.get("ready"), "workload": item.get("workload")}
                for item in selected_pods
            ],
        }
        (case_dir / "metadata.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
        alert_metrics = prometheus.collect_alert_metrics(alert, pod["namespace"], "" if group else pod["name"], start, end)
        alert = {**alert, "metric_evidence": alert_metrics["alert_evidence"]}
        write_json(case_dir / "alert.json", alert)
        pod_metrics = []
        metric_captures = []
        for member in selected_pods:
            try:
                pod_metrics.extend(prometheus.collect_pod_metrics(member["namespace"], member["name"], start, end))
                metric_captures.append(getattr(prometheus, "last_pod_metric_capture_info", None))
            except (OSError, RuntimeError, ValueError) as exc:
                metric_captures.append({"status": "unavailable", "pod": member["name"], "reason": type(exc).__name__})
        pod_metric_capture = metric_captures[0] if len(metric_captures) == 1 else {
            "status": "unavailable" if all(isinstance(item, dict) and item.get("status") == "unavailable" for item in metric_captures) else "partial" if len(all_pods) > len(selected_pods) or any(
                isinstance(item, dict) and item.get("status") != "complete" for item in metric_captures
            ) else "complete",
            "available": bool(pod_metrics),
            "truncated": len(all_pods) > len(selected_pods) or any(
                isinstance(item, dict) and item.get("truncated") for item in metric_captures
            ),
            "captured_pods": [item["name"] for item in selected_pods],
            "omitted_pods": max(0, len(all_pods) - len(selected_pods)),
            "retained_series": len(pod_metrics),
        }
        if not isinstance(pod_metric_capture, dict):
            pod_metric_capture = {
                "status": "unreported", "available": None, "truncated": None,
                "retained_series": len(pod_metrics),
            }
        write_json(case_dir / "prometheus_metrics.json", {
            "window": metadata["window"], "alert_evidence": alert_metrics["alert_evidence"],
            "pod_metric_capture": pod_metric_capture,
            "series": alert_metrics["series"] + pod_metrics,
        })
        logs = []
        log_captures = []
        for member in selected_pods:
            try:
                logs.extend(opensearch.collect_logs(member["namespace"], member["name"], start, end,
                                                    limit=max(100, 2000 // len(selected_pods)), focus=alert_time))
                log_captures.append(getattr(opensearch, "last_collection_info", None))
            except (OSError, RuntimeError, ValueError) as exc:
                log_captures.append({"status": "unavailable", "pod": member["name"], "reason": type(exc).__name__})
        log_capture = log_captures[0] if len(log_captures) == 1 else {
            "status": "unavailable" if all(isinstance(item, dict) and item.get("status") == "unavailable" for item in log_captures) else "partial" if len(all_pods) > len(selected_pods) or any(
                isinstance(item, dict) and item.get("status") != "complete" for item in log_captures
            ) else "complete",
            "available": bool(logs),
            "truncated": len(all_pods) > len(selected_pods) or any(
                isinstance(item, dict) and item.get("truncated") for item in log_captures
            ),
            "captured_pods": [item["name"] for item in selected_pods],
            "omitted_pods": max(0, len(all_pods) - len(selected_pods)),
            "retained_hits": len(logs),
        }
        if not isinstance(log_capture, dict):
            log_capture = {
                "status": "unreported",
                "available": None,
                "truncated": None,
                "retained_hits": len(logs),
            }
        logs.sort(key=lambda item: str(item.get("@timestamp", "")))
        write_json(case_dir / "opensearch_logs.json", {"hits": logs, "capture": log_capture})
        configurations = []
        seen_configuration_keys = set()
        for member in selected_pods:
            for item in kubernetes.configuration_snapshot(member):
                key = (item.get("kind"), item.get("namespace"), item.get("name"))
                if key not in seen_configuration_keys:
                    seen_configuration_keys.add(key)
                    configurations.append(item)
        write_json(case_dir / "kubernetes_config.json", {"items": configurations})
        return case_dir


def _url(value: Any, label: str) -> str:
    text = str(value).strip().rstrip("/")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label} must be an http(s) URL")
    return text


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected an integer between {minimum} and {maximum}") from exc
    return min(maximum, max(minimum, number))


def _environment_limit(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return min(maximum, max(minimum, value))


def _identity_labels(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError("identity_labels must contain at most eight mappings")
    result = []
    used = set()
    used_names = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError("Each identity mapping must be an object")
        name = str(entry.get("name") or "").strip()
        alert_label = str(entry.get("alert_label") or "").strip()
        pod_label = str(entry.get("pod_label") or "").strip()
        if not name or len(name) > 32 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alert_label):
            raise ValueError("Identity name and Prometheus alert label are required")
        if not pod_label or len(pod_label) > 253 or any(character.isspace() for character in pod_label):
            raise ValueError("A valid Kubernetes pod label is required for each identity")
        if alert_label in used:
            raise ValueError("Alert identity labels must be unique")
        if name.lower() in used_names:
            raise ValueError("Identifier names must be unique")
        used.add(alert_label)
        used_names.add(name.lower())
        result.append({"name": name, "alert_label": alert_label, "pod_label": pod_label})
    return result


def _app_id(cluster: str, namespace: str, workload: str) -> str:
    return ":".join(_slug(value) for value in (cluster, namespace, workload))


def _incident_id(alert: dict[str, Any], namespace: str, pod: str) -> str:
    if (pod.startswith("pod:") or ":" not in pod) and not alert.get("source") and not alert.get("fingerprint"):
        # Preserve IDs of existing Prometheus pod incidents across this upgrade.
        seed = f"{alert['alertname']}|{alert['startsAt']}|{namespace}|{pod.removeprefix('pod:')}"
    else:
        seed = f"{alert.get('source', 'prometheus')}|{alert.get('fingerprint', '')}|{alert['alertname']}|{alert['startsAt']}|{namespace}|{pod}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10]
    timestamp = re.sub(r"[^0-9]", "", str(alert["startsAt"]))[:14]
    return f"incident-{timestamp}-{_slug(str(alert['alertname']))}-{digest}"


def _resolve_alert_scope(
    pods: list[dict[str, Any]], namespace: str, labels: dict[str, Any],
    identity_labels: list[dict[str, str]], service_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Resolve a fault to its actual Kubernetes scope, never an arbitrary replica."""
    candidates = [item for item in pods if not namespace or item["namespace"] == namespace]
    identifiers = [
        {"name": item["name"], "alert_label": item["alert_label"],
         "pod_label": item["pod_label"], "value": str(labels[item["alert_label"]])}
        for item in identity_labels if labels.get(item["alert_label"])
    ]
    pod_name = str(labels.get("pod") or labels.get("pod_name") or labels.get("kubernetes_pod_name") or "").strip()
    if pod_name:
        matches = [item for item in candidates if item["name"] == pod_name]
        return {"kind": "pod", "name": pod_name, "pods": matches, "identifiers": identifiers} if len(matches) == 1 else None
    pod_uid = str(labels.get("pod_uid") or labels.get("kubernetes_pod_uid") or "").strip()
    if pod_uid:
        matches = [item for item in candidates if item.get("uid") == pod_uid]
        return {"kind": "pod", "name": matches[0]["name"], "pods": matches, "identifiers": identifiers} if len(matches) == 1 else None
    if identifiers:
        matches = [item for item in candidates if all(
            item.get("labels", {}).get(identity["pod_label"]) == identity["value"]
            for identity in identifiers
        )]
        if not matches or len({item["namespace"] for item in matches}) != 1:
            return None
        primary = identifiers[-1]
        return {"kind": primary["name"].lower(), "name": primary["value"],
                "pods": matches, "identifiers": identifiers}

    workload_name = next((str(labels[key]) for key in ("deployment", "statefulset", "daemonset", "job_name", "target_workload") if labels.get(key)), "")
    if workload_name:
        matches = [item for item in candidates if item.get("workload") == workload_name]
        if matches and len({item["namespace"] for item in matches}) == 1:
            return {"kind": "workload", "name": workload_name, "pods": matches, "identifiers": []}
        return None
    service_name = str(labels.get("target_service") or labels.get("kubernetes_service") or labels.get("service") or "").strip()
    if service_name:
        matches = service_candidates if service_candidates is not None else [
            item for item in candidates if item.get("workload") == service_name
        ]
        if matches and len({item["namespace"] for item in matches}) == 1:
            return {"kind": "service", "name": service_name, "pods": matches, "identifiers": []}
        return None
    return {"kind": "pod", "name": candidates[0]["name"], "pods": candidates, "identifiers": []} if len(candidates) == 1 else None


def _resolve_alert_pod(
    pods: list[dict[str, Any]],
    namespace: str,
    labels: dict[str, Any],
    service_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    pod_name = str(labels.get("pod", ""))
    if pod_name:
        return next((item for item in pods if item["namespace"] == namespace and item["name"] == pod_name), None)
    workload_name = next(
        (str(labels[key]) for key in ("deployment", "statefulset", "daemonset", "job_name") if labels.get(key)),
        "",
    )
    if workload_name:
        matches = [item for item in pods if item["namespace"] == namespace and item.get("workload") == workload_name]
        return matches[0] if len(matches) == 1 else None
    target_workload = str(labels.get("target_workload", "")).strip()
    if target_workload:
        matches = [item for item in pods if item["namespace"] == namespace and item.get("workload") == target_workload]
        return sorted(matches, key=lambda item: (not item.get("ready", False), item["name"]))[0] if matches else None
    service_name = str(labels.get("service", "")).strip()
    if service_name:
        # ``None`` keeps this helper useful for offline captures. Live collection passes
        # selector-backed candidates, including an explicit empty list when none match.
        candidates = service_candidates
        if candidates is None:
            candidates = [
                item for item in pods
                if item["namespace"] == namespace and item.get("workload") == service_name
            ]
        workloads = {str(item.get("workload", "")) for item in candidates}
        if len(workloads) == 1 and workloads != {""}:
            return sorted(candidates, key=lambda item: (not item.get("ready", False), item["name"]))[0]
        return None
    namespace_pods = [item for item in pods if item["namespace"] == namespace]
    return namespace_pods[0] if len(namespace_pods) == 1 else None


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-") or "unknown"


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
