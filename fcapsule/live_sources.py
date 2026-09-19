"""Live source discovery and bounded incident capture orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from fcapsule.adapters import KubernetesAdapter, OpenSearchAdapter, PrometheusAdapter
from fcapsule.io.output_writer import write_json
from fcapsule.models.schemas import parse_timestamp
from fcapsule.store import FCAPSuleStore, utc_now

SOURCE_SETTING = "live_source_configuration"
IGNORED_ALERTS = {"Watchdog", "InfoInhibitor"}


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
        "kubernetes_url": os.environ.get("FCAPSULE_KUBERNETES_URL", ""),
        "namespaces": [value.strip() for value in os.environ.get("FCAPSULE_NAMESPACES", "default").split(",") if value.strip()],
        "poll_interval_seconds": int(os.environ.get("FCAPSULE_POLL_INTERVAL_SECONDS", "30")),
        "incident_window_minutes": int(os.environ.get("FCAPSULE_INCIDENT_WINDOW_MINUTES", "10")),
        "auto_build_reports": os.environ.get("FCAPSULE_AUTO_BUILD_REPORTS", "true").lower() in {"1", "true", "yes"},
    }


class LiveSourceCoordinator:
    def __init__(self, store: FCAPSuleStore, state_dir: Path) -> None:
        self.store = store
        self.state_dir = state_dir
        self.case_root = state_dir / "live-cases"
        self.case_root.mkdir(parents=True, exist_ok=True)

    def configuration(self) -> dict[str, Any]:
        defaults = default_source_configuration()
        saved = self.store.get_setting(SOURCE_SETTING)
        if saved:
            try:
                defaults.update(json.loads(saved))
            except json.JSONDecodeError:
                pass
        defaults["opensearch_credentials_configured"] = bool(os.environ.get("OPENSEARCH_USERNAME"))
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
            "kubernetes_url": str(payload.get("kubernetes_url", current["kubernetes_url"])).strip(),
            "namespaces": sorted(set(str(value).strip() for value in namespaces if str(value).strip())),
            "poll_interval_seconds": min(3600, max(10, int(payload.get("poll_interval_seconds", current["poll_interval_seconds"])))),
            "incident_window_minutes": min(120, max(2, int(payload.get("incident_window_minutes", current["incident_window_minutes"])))),
            "auto_build_reports": bool(payload.get("auto_build_reports", current["auto_build_reports"])),
        }
        if not updated["cluster_name"]:
            raise ValueError("Cluster name is required")
        if not updated["opensearch_index"] or any(character.isspace() for character in updated["opensearch_index"]):
            raise ValueError("OpenSearch index pattern cannot be empty or contain spaces")
        self.store.set_setting(SOURCE_SETTING, json.dumps(updated, separators=(",", ":")))
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

    def synchronize(self) -> dict[str, Any]:
        config = self.configuration()
        namespaces = set(config["namespaces"]) or None
        prometheus, opensearch, kubernetes = self.adapters(config)
        probes = self.test_connections()
        if not probes["targets"]["kubernetes"].get("ok"):
            raise RuntimeError(probes["targets"]["kubernetes"].get("error", "Kubernetes API is unavailable"))

        pods = kubernetes.list_pods(namespaces)
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
                }
                for item in workload_pods
            ]
            source_config = {
                "faults": {"adapter": "prometheus", "status": "connected" if probes["targets"]["prometheus"].get("ok") else "error"},
                "metrics": {"adapter": "prometheus", "status": "observed" if metric_pods else "missing", "pods_observed": metric_pods},
                "logs": {"adapter": "opensearch", "status": "observed" if recent_logs else "waiting", "recent_documents": recent_logs},
                "configuration": {"adapter": "kubernetes", "status": "available", "pods_visible": len(workload_pods)},
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
        captured: list[dict[str, Any]] = []
        for alert in alerts:
            labels = alert.get("labels", {})
            namespace = str(labels.get("namespace", ""))
            if alert["alertname"] in IGNORED_ALERTS or (namespaces and namespace not in namespaces):
                continue
            pod_name = str(labels.get("pod", ""))
            pod = next((item for item in pods if item["namespace"] == namespace and item["name"] == pod_name), None)
            if pod is None:
                namespace_pods = [item for item in pods if item["namespace"] == namespace]
                pod = namespace_pods[0] if len(namespace_pods) == 1 else None
            if pod is None or not alert.get("startsAt"):
                continue
            incident_id = _incident_id(alert, namespace, pod["name"])
            if self.store.get_incident(incident_id):
                continue
            app_id = _app_id(config["cluster_name"], namespace, pod["workload"])
            case_dir = self._capture_case(config, prometheus, opensearch, kubernetes, alert, pod, incident_id)
            captured.append({"case_dir": str(case_dir), "app_id": app_id, "app_name": pod["workload"]})

        return {
            **probes,
            "last_sync_at": utc_now(),
            "pods_visible": len(pods),
            "applications_visible": len(grouped),
            "active_alerts": len([item for item in alerts if item["alertname"] not in IGNORED_ALERTS]),
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
    ) -> Path:
        alert_time = parse_timestamp(alert["startsAt"], "alert.startsAt")
        now = datetime.now(timezone.utc)
        window = timedelta(minutes=config["incident_window_minutes"])
        start = alert_time - window
        end = max(now, alert_time + timedelta(minutes=2))
        case_dir = self.case_root / incident_id
        case_dir.mkdir(parents=True, exist_ok=True)
        metadata = {
            "case_id": incident_id,
            "case_title": alert.get("annotations", {}).get("summary") or alert["alertname"],
            "service": pod["workload"],
            "namespace": pod["namespace"],
            "cluster": config["cluster_name"],
            "pod": pod["name"],
            "window": {"start": _iso(start), "end": _iso(end)},
            "timezone": "UTC",
            "telemetry_sources": {
                "alerts": "Prometheus /api/v1/alerts",
                "metrics": "Prometheus /api/v1/query_range",
                "logs": f"OpenSearch {config['opensearch_index']}",
                "configuration": "Kubernetes API",
            },
            "fields": {"log_time_field": "@timestamp", "log_message_field": "message", "log_level_field": "level"},
            "privacy": {"anonymized": True, "synthetic": False, "secrets_collected": False},
            "trace_access": {"available": False, "raw_spans_retained": False},
            "topology": [],
        }
        (case_dir / "metadata.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
        write_json(case_dir / "alert.json", alert)
        write_json(case_dir / "prometheus_metrics.json", {"window": metadata["window"], "series": prometheus.collect_pod_metrics(pod["namespace"], pod["name"], start, end)})
        write_json(case_dir / "opensearch_logs.json", {"hits": opensearch.collect_logs(pod["namespace"], pod["name"], start, end)})
        write_json(case_dir / "kubernetes_config.json", {"items": kubernetes.configuration_snapshot(pod)})
        return case_dir


def _url(value: Any, label: str) -> str:
    text = str(value).strip().rstrip("/")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label} must be an http(s) URL")
    return text


def _app_id(cluster: str, namespace: str, workload: str) -> str:
    return ":".join(_slug(value) for value in (cluster, namespace, workload))


def _incident_id(alert: dict[str, Any], namespace: str, pod: str) -> str:
    seed = f"{alert['alertname']}|{alert['startsAt']}|{namespace}|{pod}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10]
    timestamp = re.sub(r"[^0-9]", "", str(alert["startsAt"]))[:14]
    return f"incident-{timestamp}-{_slug(str(alert['alertname']))}-{digest}"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-") or "unknown"


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
