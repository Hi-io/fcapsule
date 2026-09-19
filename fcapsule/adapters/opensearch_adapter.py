"""Bounded, read-only OpenSearch log adapter."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from fcapsule.adapters.transport import JsonTransport


class OpenSearchAdapter:
    def __init__(
        self,
        base_url: str,
        index_pattern: str = "k8s-logs-*",
        username: str | None = None,
        password: str | None = None,
        timeout: float = 8,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.index_pattern = index_pattern.strip() or "k8s-logs-*"
        self.transport = JsonTransport(self.base_url, username, password, timeout)

    @property
    def search_path(self) -> str:
        return f"/{quote(self.index_pattern, safe='*-_,')}/_search"

    def test_connection(self) -> dict[str, Any]:
        started = time.perf_counter()
        root = self.transport.request("/")
        count = self.transport.request(f"/{quote(self.index_pattern, safe='*-_,')}/_count")
        return {
            "ok": True,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "cluster_name": root.get("cluster_name"),
            "version": root.get("version", {}).get("number"),
            "documents": int(count.get("count", 0)),
        }

    def pod_log_counts(self, start: datetime, namespaces: set[str] | None = None) -> dict[tuple[str, str], int]:
        filters: list[dict[str, Any]] = [{"range": {"@timestamp": {"gte": _iso(start)}}}]
        if namespaces:
            filters.append({"terms": {"kubernetes.namespace.keyword": sorted(namespaces)}})
        payload = self.transport.request(
            self.search_path,
            method="POST",
            body={
                "size": 0,
                "query": {"bool": {"filter": filters}},
                "aggs": {
                    "namespaces": {
                        "terms": {"field": "kubernetes.namespace.keyword", "size": 200},
                        "aggs": {"pods": {"terms": {"field": "kubernetes.pod.name.keyword", "size": 500}}},
                    }
                },
            },
        )
        result: dict[tuple[str, str], int] = {}
        for namespace in payload.get("aggregations", {}).get("namespaces", {}).get("buckets", []):
            for pod in namespace.get("pods", {}).get("buckets", []):
                result[(str(namespace["key"]), str(pod["key"]))] = int(pod.get("doc_count", 0))
        return result

    def collect_logs(
        self,
        namespace: str,
        pod: str,
        start: datetime,
        end: datetime,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        payload = self.transport.request(
            self.search_path,
            method="POST",
            body={
                "size": min(max(1, limit), 10000),
                "sort": [{"@timestamp": "asc"}],
                "query": {
                    "bool": {
                        "filter": [
                            {"range": {"@timestamp": {"gte": _iso(start), "lte": _iso(end)}}},
                            {"term": {"kubernetes.namespace.keyword": namespace}},
                            {"term": {"kubernetes.pod.name.keyword": pod}},
                        ]
                    }
                },
            },
        )
        logs = []
        for hit in payload.get("hits", {}).get("hits", []):
            source = dict(hit.get("_source", {}))
            kubernetes = source.get("kubernetes", {}) if isinstance(source.get("kubernetes"), dict) else {}
            pod_data = kubernetes.get("pod", {}) if isinstance(kubernetes.get("pod"), dict) else {}
            container = kubernetes.get("container", {}) if isinstance(kubernetes.get("container"), dict) else {}
            labels = kubernetes.get("labels", {}) if isinstance(kubernetes.get("labels"), dict) else {}
            message = str(source.get("message") or source.get("log") or "")
            logs.append(
                {
                    "@timestamp": source.get("@timestamp"),
                    "indexed_at": source.get("event", {}).get("ingested") if isinstance(source.get("event"), dict) else None,
                    "level": _log_level(source, message),
                    "service": labels.get("app_kubernetes_io/name") or labels.get("app") or pod,
                    "component": container.get("name") or pod,
                    "namespace": kubernetes.get("namespace") or namespace,
                    "pod": pod_data.get("name") or pod,
                    "message": message,
                }
            )
        return [item for item in logs if item.get("@timestamp") and item["message"]]


def _log_level(source: dict[str, Any], message: str) -> str:
    log = source.get("log", {}) if isinstance(source.get("log"), dict) else {}
    direct = source.get("level") or log.get("level")
    if direct:
        return str(direct).upper()
    match = re.search(r"\b(?:level[=: ]+)?(critical|fatal|error|warn(?:ing)?|info|debug)\b", message, re.I)
    return match.group(1).upper().replace("WARNING", "WARN") if match else "INFO"


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
