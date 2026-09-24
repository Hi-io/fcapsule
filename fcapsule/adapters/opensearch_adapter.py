"""Bounded, read-only OpenSearch log adapter."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from fcapsule.adapters.transport import JsonTransport, ResponseTooLargeError
from fcapsule.processing.anonymizer import diagnostic_fields

DEFAULT_MAX_MESSAGE_BYTES = 16 * 1024
DEFAULT_MAX_COLLECTION_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class OpenSearchAdapter:
    def __init__(
        self,
        base_url: str,
        index_pattern: str = "k8s-logs-*",
        username: str | None = None,
        password: str | None = None,
        timeout: float = 8,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        max_collection_bytes: int = DEFAULT_MAX_COLLECTION_BYTES,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.index_pattern = index_pattern.strip() or "k8s-logs-*"
        self.transport = JsonTransport(self.base_url, username, password, timeout)
        self.max_message_bytes = _bounded_limit(max_message_bytes, 256, 256 * 1024)
        self.max_collection_bytes = _bounded_limit(max_collection_bytes, 4096, 16 * 1024 * 1024)
        self.max_response_bytes = _bounded_limit(max_response_bytes, 4096, 64 * 1024 * 1024)

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
        focus: datetime | None = None,
        terms: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        logs, self.last_collection_info = self.collect_logs_with_info(
            namespace, pod, start, end, limit=limit, focus=focus, terms=terms
        )
        return logs

    def collect_logs_with_info(
        self,
        namespace: str,
        pod: str,
        start: datetime,
        end: datetime,
        limit: int = 2000,
        focus: datetime | None = None,
        terms: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Collect bounded log evidence and return explicit coverage metadata."""

        limit = min(max(1, limit), 10000)
        response_limited_segments: list[str] = []
        query_limit_reached = False
        successful_queries = 0

        def query(
            segment: str, segment_start: datetime, segment_end: datetime, size: int, order: str
        ) -> list[tuple[str, dict[str, Any]]]:
            nonlocal query_limit_reached, successful_queries
            try:
                segment_hits, hit_limit_reached = self._log_hits(
                    namespace, pod, segment_start, segment_end, size, order, terms
                )
            except ResponseTooLargeError:
                response_limited_segments.append(segment)
                return []
            successful_queries += 1
            query_limit_reached = query_limit_reached or hit_limit_reached
            return [(segment, hit) for hit in segment_hits]

        if focus and start < focus < end:
            baseline_size = min(max(1, limit // 4), limit - 1) if limit > 1 else 0
            hits = query("baseline", start, focus, baseline_size, "desc") if baseline_size else []
            hits.extend(query("incident", focus, end, limit - baseline_size, "asc"))
        elif terms:
            hits = query("window", start, end, limit, "asc")
        else:
            hits = query("window", start, end, limit, "desc")

        unique_hits: dict[tuple[Any, ...], tuple[str, dict[str, Any]]] = {}
        for segment, hit in hits:
            source = hit.get("_source", {})
            if hit.get("_id") is not None:
                key = ("id", str(hit["_id"]))
            else:
                message_value = source.get("message")
                if not isinstance(message_value, str):
                    message_value = source.get("log")
                key = ("fallback", source.get("@timestamp"), message_value if isinstance(message_value, str) else id(hit))
            existing = unique_hits.get(key)
            if existing is None or segment == "incident":
                unique_hits[key] = (segment, hit)

        candidates: list[tuple[str, dict[str, Any]]] = []
        for segment, hit in unique_hits.values():
            source = dict(hit.get("_source", {}))
            kubernetes = source.get("kubernetes", {}) if isinstance(source.get("kubernetes"), dict) else {}
            pod_data = kubernetes.get("pod", {}) if isinstance(kubernetes.get("pod"), dict) else {}
            container = kubernetes.get("container", {}) if isinstance(kubernetes.get("container"), dict) else {}
            labels = kubernetes.get("labels", {}) if isinstance(kubernetes.get("labels"), dict) else {}
            full_message = _message_text(source)
            structured_diagnostics = diagnostic_fields(source)
            message, message_truncated = _truncate_utf8(full_message, self.max_message_bytes)
            timestamp = source.get("@timestamp")
            if not timestamp or not message:
                continue
            log = {
                "@timestamp": timestamp,
                "indexed_at": source.get("event", {}).get("ingested") if isinstance(source.get("event"), dict) else None,
                "level": _log_level(source, full_message),
                "service": labels.get("app_kubernetes_io/name") or labels.get("app") or pod,
                "component": container.get("name") or pod,
                "namespace": kubernetes.get("namespace") or namespace,
                "pod": pod_data.get("name") or pod,
                "message": message,
                "diagnostic_fields": structured_diagnostics,
            }
            if message_truncated:
                log.update({
                    "message_truncated": True,
                    "message_truncation_reasons": ["message_byte_limit"],
                    "message_limit_bytes": self.max_message_bytes,
                })
            candidates.append((segment, log))

        logs, retained_bytes, collection_omitted = _select_bounded_logs(
            candidates,
            self.max_collection_bytes,
            max_message_bytes=self.max_message_bytes,
            reserve_baseline=bool(focus and start < focus < end),
        )

        unavailable_segments = [
            {"segment": segment, "reason": "response_byte_limit", "limit_bytes": self.max_response_bytes}
            for segment in response_limited_segments
        ]
        message_truncated_hits = sum(bool(item.get("message_truncated")) for item in logs)
        capture_truncated = bool(
            message_truncated_hits or collection_omitted or query_limit_reached or response_limited_segments
        )
        if response_limited_segments and successful_queries == 0:
            status = "unavailable"
        elif capture_truncated or response_limited_segments:
            status = "partial"
        else:
            status = "complete"
        info = {
            "status": status,
            "available": successful_queries > 0,
            "truncated": capture_truncated,
            "max_message_bytes": self.max_message_bytes,
            "max_collection_bytes": self.max_collection_bytes,
            "max_response_bytes": self.max_response_bytes,
            "retained_hits": len(logs),
            "retained_compact_bytes": retained_bytes,
            "message_truncated_hits": message_truncated_hits,
            "collection_omitted_hits": collection_omitted,
            "query_limit_reached": query_limit_reached,
            "unavailable_segments": unavailable_segments,
        }
        return logs, info

    def _log_hits(
        self,
        namespace: str,
        pod: str,
        start: datetime,
        end: datetime,
        size: int,
        order: str,
        terms: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], bool]:
        payload = self.transport.request(
            self.search_path,
            method="POST",
            body={
                "size": size,
                "sort": [{"@timestamp": order}],
                "query": {
                    "bool": {
                        "filter": [
                            {"range": {"@timestamp": {"gte": _iso(start), "lte": _iso(end)}}},
                            {"term": {"kubernetes.namespace.keyword": namespace}},
                            {"term": {"kubernetes.pod.name.keyword": pod}},
                        ],
                        **({"should": [{"match_phrase": {"message": term}} for term in terms], "minimum_should_match": 1} if terms else {}),
                    }
                },
            },
            max_response_bytes=self.max_response_bytes,
        )
        hit_data = payload.get("hits", {})
        hits = list(hit_data.get("hits", []))
        total = hit_data.get("total")
        if isinstance(total, dict):
            total = total.get("value")
        hit_limit_reached = len(hits) >= size or (isinstance(total, int) and total >= size)
        return hits, hit_limit_reached


def _bounded_limit(value: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = minimum
    return min(maximum, max(minimum, number))


def _truncate_utf8(value: str, max_bytes: int) -> tuple[str, bool]:
    if len(value) <= max_bytes:
        encoded = value.encode("utf-8")
        if len(encoded) <= max_bytes:
            return value, False
    prefix = value[:max_bytes].encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
    return prefix, True


def _compact_json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _select_bounded_logs(
    candidates: list[tuple[str, dict[str, Any]]],
    max_bytes: int,
    *,
    max_message_bytes: int,
    reserve_baseline: bool,
) -> tuple[list[dict[str, Any]], int, int]:
    if reserve_baseline:
        incidents = sorted(
            ((index, log) for index, (segment, log) in enumerate(candidates) if segment == "incident"),
            key=lambda pair: str(pair[1]["@timestamp"]),
        )
        baselines = sorted(
            ((index, log) for index, (segment, log) in enumerate(candidates) if segment == "baseline"),
            key=lambda pair: str(pair[1]["@timestamp"]), reverse=True,
        )
        windows = [(index, log) for index, (segment, log) in enumerate(candidates) if segment == "window"]
    else:
        incidents = []
        baselines = []
        windows = sorted(
            ((index, log) for index, (_, log) in enumerate(candidates)),
            key=lambda pair: str(pair[1]["@timestamp"]),
        )

    selected: dict[int, dict[str, Any]] = {}
    used_bytes = 2  # Opening and closing brackets of the compact JSON array.

    def take(group: list[tuple[int, dict[str, Any]]], share_bytes: int | None) -> None:
        nonlocal used_bytes
        share_used = 0
        for index, log in group:
            if index in selected:
                continue
            separator_bytes = 1 if selected else 0
            global_available = max_bytes - used_bytes - separator_bytes
            share_available = max_bytes if share_bytes is None else share_bytes - share_used
            available = min(global_available, share_available)
            item_bytes = _compact_json_bytes(log)
            if item_bytes <= available:
                selected[index] = log
                used_bytes += separator_bytes + item_bytes
                share_used += separator_bytes + item_bytes
                continue
            partial = _truncate_log_to_fit(log, available, max_message_bytes)
            if partial is not None:
                size = _compact_json_bytes(partial)
                selected[index] = partial
                used_bytes += separator_bytes + size
                share_used += separator_bytes + size

    if incidents and baselines:
        incident_share = max_bytes * 3 // 4
        take(incidents, incident_share)
        take(baselines, max_bytes - incident_share)
    elif incidents:
        take(incidents, max_bytes)
    elif baselines:
        take(baselines, max_bytes)
    take(windows, None)
    take(incidents, None)
    take(baselines, None)

    logs = sorted(selected.values(), key=lambda item: str(item["@timestamp"]))
    return logs, used_bytes, len(candidates) - len(selected)


def _truncate_log_to_fit(log: dict[str, Any], budget: int, message_limit: int) -> dict[str, Any] | None:
    if budget <= 0:
        return None
    text = log["message"]
    low, high = 0, len(text)
    best: dict[str, Any] | None = None
    while low <= high:
        middle = (low + high) // 2
        candidate = dict(log)
        candidate["message"] = text[:middle]
        reasons = list(candidate.get("message_truncation_reasons", []))
        if "aggregate_byte_limit" not in reasons:
            reasons.append("aggregate_byte_limit")
        candidate.update({
            "message_truncated": True,
            "message_truncation_reasons": reasons,
            "message_limit_bytes": message_limit,
        })
        size = _compact_json_bytes(candidate)
        if size <= budget:
            best = candidate if middle else None
            low = middle + 1
        else:
            high = middle - 1
    return best


def _log_level(source: dict[str, Any], message: str) -> str:
    log = source.get("log", {}) if isinstance(source.get("log"), dict) else {}
    direct = source.get("level") or log.get("level")
    if direct:
        return str(direct).upper()
    match = re.search(r"\b(?:level[=: ]+)?(critical|fatal|error|warn(?:ing)?|info|debug)\b", message, re.I)
    return match.group(1).upper().replace("WARNING", "WARN") if match else "INFO"


def _message_text(source: dict[str, Any]) -> str:
    """Normalize common parsed and unparsed Filebeat message shapes."""

    message = source.get("message")
    if isinstance(message, str) and message.strip():
        return message
    if isinstance(message, dict):
        for key in ("message", "log", "original", "content"):
            nested = message.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested
        return json.dumps(message, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if message is not None and not isinstance(message, (dict, list)):
        return str(message)

    log = source.get("log")
    if isinstance(log, str) and log.strip():
        return log
    if isinstance(log, dict):
        for key in ("message", "original", "content"):
            nested = log.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested
    event = source.get("event")
    if isinstance(event, dict):
        original = event.get("original")
        if isinstance(original, str) and original.strip():
            return original
    return ""


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
