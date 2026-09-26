"""Read-only Kubernetes API adapter using an in-cluster ServiceAccount."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from fcapsule.adapters.transport import JsonTransport, ResponseTooLargeError
from fcapsule.adapters.source_url_policy import default_kubernetes_url, validate_source_url

SERVICE_ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")
SENSITIVE_KEY = re.compile(r"password|passwd|secret|token|credential|api[-_]?key|private[-_]?key", re.I)
MAX_SELECTOR_LABELS = 32
MAX_SELECTOR_EXPRESSIONS = 16
MAX_SELECTOR_VALUES = 16
MAX_ENDPOINT_SLICE_PAGE_SIZE = 50
MAX_ENDPOINT_SLICE_PAGES = 8
MAX_ENDPOINT_SLICE_BYTES = 1024 * 1024
MAX_ENDPOINT_SLICES = 200
MAX_ENDPOINT_SLICE_ENDPOINTS = 24
MAX_ENDPOINT_SLICE_PORTS = 12
MAX_ENDPOINT_SLICE_CURSOR_LENGTH = 4096
MAX_ENDPOINT_SLICE_SELECTOR_QUERY = 2048
LABEL_VALUE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?\Z")
MAX_POD_PAGE_SIZE = 50
MAX_POD_PAGES = 8
MAX_POD_RESPONSE_BYTES = 1024 * 1024
MAX_PODS = 200
MAX_POD_CURSOR_LENGTH = 4096


class KubernetesInventory(list[dict[str, Any]]):
    """A list-compatible Kubernetes result with explicit discovery completeness."""

    def __init__(
        self,
        items: list[dict[str, Any]] | None = None,
        *,
        status: str = "observed",
        complete: bool = True,
        has_more: bool | None = False,
        omitted_pods: int = 0,
        reason: str | None = None,
        observed_at: str | None = None,
    ) -> None:
        super().__init__(items or [])
        self.status = status
        self.complete = complete
        self.has_more = has_more
        self.omitted_pods = omitted_pods
        self.reason = reason
        self.observed_at = observed_at


def _observed_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_labels(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, label in sorted(value.items(), key=lambda item: str(item[0]))[:MAX_SELECTOR_LABELS]:
        key = str(key)[:253]
        result[key] = "<redacted>" if SENSITIVE_KEY.search(key) else str(label)[:128]
    return result


def _selector_parts(selector: Any) -> tuple[dict[str, str], list[dict[str, Any]], bool]:
    """Normalize the bounded Kubernetes LabelSelector fields used for matching."""
    if not isinstance(selector, dict):
        return {}, [], False
    raw_labels = selector.get("matchLabels", {})
    if not isinstance(raw_labels, dict):
        return {}, [], False
    labels = _safe_labels(raw_labels)
    complete = len(raw_labels) <= MAX_SELECTOR_LABELS
    raw_expressions = selector.get("matchExpressions", [])
    if not isinstance(raw_expressions, list):
        return labels, [], False
    expressions = []
    if len(raw_expressions) > MAX_SELECTOR_EXPRESSIONS:
        complete = False
    for expression in raw_expressions[:MAX_SELECTOR_EXPRESSIONS]:
        if not isinstance(expression, dict):
            complete = False
            continue
        key = str(expression.get("key") or "")[:253]
        operator = str(expression.get("operator") or "")[:32]
        values = expression.get("values", [])
        if not key or not isinstance(values, list):
            complete = False
            continue
        if len(values) > MAX_SELECTOR_VALUES:
            complete = False
        normalized_values = [str(item)[:128] for item in values[:MAX_SELECTOR_VALUES]]
        if operator not in {"In", "NotIn", "Exists", "DoesNotExist"}:
            complete = False
        if operator in {"In", "NotIn"} and not normalized_values:
            complete = False
        if operator in {"Exists", "DoesNotExist"} and normalized_values:
            complete = False
        expressions.append({
            "key": key,
            "operator": operator,
            "values": ["<redacted>"] if SENSITIVE_KEY.search(key) and normalized_values else normalized_values,
        })
    return labels, expressions, complete


def evaluate_label_selector(
    labels: Any,
    match_labels: Any,
    match_expressions: Any,
    *,
    complete: bool = True,
) -> dict[str, Any]:
    """Evaluate supported Kubernetes LabelSelector rules without guessing on bad input."""
    if not isinstance(labels, dict) or not isinstance(match_labels, dict) or not isinstance(match_expressions, list):
        return {"status": "unknown", "requirements": [], "reason": "unsupported_selector_shape"}

    requirements: list[dict[str, Any]] = []
    outcomes: list[bool | None] = []

    def add_requirement(key: str, operator: str, expected: Any, actual: Any, matches: bool | None) -> None:
        row = {"key": key[:253], "operator": operator}
        if not SENSITIVE_KEY.search(key):
            row["expected"] = expected
            row["observed"] = "<missing>" if actual is None else str(actual)[:128]
        else:
            row["redacted"] = True
        if matches is not None:
            row["matches"] = matches
        requirements.append(row)
        outcomes.append(matches)

    for raw_key, expected in list(match_labels.items())[:MAX_SELECTOR_LABELS]:
        key = str(raw_key)
        actual = labels.get(key) if key in labels else None
        if SENSITIVE_KEY.search(key):
            matches = None
        elif not isinstance(expected, str):
            matches = None
        else:
            matches = key in labels and str(actual) == expected
        add_requirement(key, "Equals", expected, actual, matches)

    for expression in match_expressions[:MAX_SELECTOR_EXPRESSIONS]:
        if not isinstance(expression, dict):
            outcomes.append(None)
            continue
        key = str(expression.get("key") or "")
        operator = str(expression.get("operator") or "")
        values = expression.get("values", [])
        if not key or not isinstance(values, list) or len(values) > MAX_SELECTOR_VALUES:
            outcomes.append(None)
            continue
        present = key in labels
        actual = labels.get(key)
        if SENSITIVE_KEY.search(key):
            matches = None
        elif operator == "In" and values:
            matches = present and str(actual) in {str(item) for item in values}
        elif operator == "NotIn" and values:
            matches = not present or str(actual) not in {str(item) for item in values}
        elif operator == "Exists" and not values:
            matches = present
        elif operator == "DoesNotExist" and not values:
            matches = not present
        else:
            matches = None
        expected = [str(item)[:128] for item in values[:MAX_SELECTOR_VALUES]]
        add_requirement(key, operator, expected, actual, matches)

    if any(outcome is False for outcome in outcomes):
        status = "not_matched"
    elif not complete or any(outcome is None for outcome in outcomes):
        status = "unknown"
    else:
        status = "matched"
    return {
        "status": status,
        "requirements": requirements[:MAX_SELECTOR_LABELS + MAX_SELECTOR_EXPRESSIONS],
        "reason": "selector_truncated_or_unsupported" if status == "unknown" else None,
    }


class KubernetesAdapter:
    def __init__(self, base_url: str | None = None, timeout: float = 8) -> None:
        self.base_url = (base_url or default_kubernetes_url()).rstrip("/")
        token_path = SERVICE_ACCOUNT / "token"
        ca_path = SERVICE_ACCOUNT / "ca.crt"
        token = token_path.read_text(encoding="utf-8").strip() if token_path.is_file() else os.environ.get("FCAPSULE_KUBERNETES_TOKEN", "")
        if token:
            self.base_url = validate_source_url(self.base_url, "kubernetes", "Kubernetes API URL")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        context = ssl.create_default_context(cafile=str(ca_path)) if ca_path.is_file() else ssl.create_default_context()
        self.transport = JsonTransport(self.base_url, timeout=timeout, headers=headers, ssl_context=context)

    def test_connection(self) -> dict[str, Any]:
        started = time.perf_counter()
        version = self.transport.request("/version")
        return {
            "ok": True,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "version": version.get("gitVersion"),
            "platform": version.get("platform"),
            "authentication": "service-account" if (SERVICE_ACCOUNT / "token").is_file() else "external-token",
        }

    def list_pods(self, namespaces: set[str] | None = None) -> KubernetesInventory:
        """Return bounded Pods, querying each requested namespace independently."""
        requested_namespaces = sorted(name for name in (namespaces or set())
                                      if isinstance(name, str) and name)
        invalid_scope = namespaces is not None and bool(namespaces) and len(requested_namespaces) != len(namespaces)
        if namespaces and not requested_namespaces:
            return KubernetesInventory(status="unavailable", complete=False, has_more=None,
                                       reason="invalid_namespace_scope")
        scopes: list[str | None] = requested_namespaces if namespaces else [None]
        pods = []
        omitted_pods = 0
        partial = invalid_scope
        has_more: bool | None = bool(invalid_scope)
        reason = "invalid_namespace_scope" if invalid_scope else None
        observed_at: str | None = None
        page_count = 0
        stop = False

        def retain(item: Any, namespace_hint: str | None) -> None:
            nonlocal omitted_pods, partial, stop
            if not isinstance(item, dict):
                partial = True
                return
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            namespace = str(metadata.get("namespace") or namespace_hint or "default")
            if namespaces and namespace not in namespaces:
                partial = True
                return
            if len(pods) >= MAX_PODS:
                omitted_pods += 1
                partial = True
                stop = True
                return

            raw_status = item.get("status", {})
            status_data = raw_status if isinstance(raw_status, dict) else {}
            raw_spec = item.get("spec", {})
            spec = raw_spec if isinstance(raw_spec, dict) else {}
            if not isinstance(raw_status, dict) or not isinstance(raw_spec, dict):
                partial = True
            raw_labels = metadata.get("labels", {})
            labels = _safe_labels(raw_labels)
            if not isinstance(raw_labels, dict) or len(raw_labels) > MAX_SELECTOR_LABELS:
                partial = True
            raw_owners = metadata.get("ownerReferences", [])
            owners = raw_owners if isinstance(raw_owners, list) else []
            if not isinstance(raw_owners, list) or len(owners) > 16:
                partial = True
            owner = next((candidate for candidate in owners[:16] if isinstance(candidate, dict)), {})
            conditions = status_data.get("conditions", [])
            if not isinstance(conditions, list):
                partial = True
                conditions = []
            elif len(conditions) > 100:
                partial = True
            ready_condition = next((condition for condition in conditions[:100]
                                    if isinstance(condition, dict) and condition.get("type") == "Ready"), {})
            ready_status = str(ready_condition.get("status") or "unknown").lower()
            pod_name = str(metadata.get("name") or "unknown")[:253]
            raw_containers = spec.get("containers", [])
            containers = raw_containers if isinstance(raw_containers, list) else []
            if not isinstance(raw_containers, list) or len(containers) > 16:
                partial = True
            if any(not isinstance(container, dict) for container in containers[:16]):
                partial = True
            container_statuses = status_data.get("containerStatuses", [])
            if not isinstance(container_statuses, list):
                partial = True
                container_statuses = []
            elif len(container_statuses) > 12:
                partial = True
            if any(not isinstance(container, dict) for container in container_statuses[:12]):
                partial = True
            workload = (labels.get("app.kubernetes.io/name") or labels.get("app") or labels.get("k8s-app")
                        or owner.get("name") or pod_name)
            pods.append({
                "name": pod_name,
                "namespace": namespace,
                "uid": str(metadata.get("uid") or "")[:128] or None,
                "workload": str(workload)[:253],
                "owner_kind": str(owner.get("kind") or "")[:63] or None,
                "owner_name": str(owner.get("name") or "")[:253] or None,
                "node": str(spec.get("nodeName") or "")[:253] or None,
                "phase": str(status_data.get("phase") or "Unknown")[:32],
                "ready": ready_status == "true",
                "ready_status": ready_status if ready_status in {"true", "false", "unknown"} else "unknown",
                "labels": labels,
                "containers": [str(container.get("name") or "")[:63] or None
                                for container in containers[:16] if isinstance(container, dict)],
                "images": [str(container.get("image") or "")[:512] or None
                           for container in containers[:16] if isinstance(container, dict)],
                "raw_spec": spec,
                "container_statuses": container_statuses[:12],
            })

        for scope_index, namespace_hint in enumerate(scopes):
            if stop:
                break
            resource_path = (f"/api/v1/namespaces/{quote(namespace_hint, safe='')}/pods"
                             if namespace_hint is not None else "/api/v1/pods")
            cursor: str | None = None
            seen_cursors: set[str] = set()
            while True:
                if page_count >= MAX_POD_PAGES:
                    partial = True
                    has_more = True
                    reason = reason or "page_limit_reached"
                    stop = True
                    break
                params = {"limit": str(MAX_POD_PAGE_SIZE)}
                if cursor is not None:
                    params["continue"] = cursor
                try:
                    payload = self.transport.request(
                        f"{resource_path}?{urlencode(params)}",
                        max_response_bytes=MAX_POD_RESPONSE_BYTES,
                    )
                except ResponseTooLargeError:
                    if page_count == 0:
                        return KubernetesInventory(status="unavailable", complete=False, has_more=None,
                                                   reason="response_too_large")
                    partial = True
                    has_more = True
                    reason = "page_unavailable"
                    stop = True
                    break
                except (RuntimeError, OSError, ValueError):
                    if page_count == 0:
                        return KubernetesInventory(status="unavailable", complete=False, has_more=None,
                                                   reason="request_failed")
                    partial = True
                    has_more = True
                    reason = "page_unavailable"
                    stop = True
                    break
                if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                    if page_count == 0:
                        return KubernetesInventory(status="unavailable", complete=False, has_more=None,
                                                   reason="unsupported_response")
                    partial = True
                    has_more = True
                    reason = "unsupported_response"
                    stop = True
                    break

                page_count += 1
                if observed_at is None:
                    observed_at = _observed_at()
                raw_items = payload["items"]
                page_items = raw_items[:MAX_POD_PAGE_SIZE]
                if len(raw_items) > MAX_POD_PAGE_SIZE:
                    partial = True
                    has_more = True
                    reason = reason or "page_limit_ignored"
                for item in page_items:
                    retain(item, namespace_hint)
                    if stop:
                        break
                if stop or len(raw_items) > MAX_POD_PAGE_SIZE:
                    has_more = True
                    stop = True
                    break

                metadata = payload.get("metadata")
                if not isinstance(metadata, dict):
                    partial = True
                    has_more = True
                    reason = "unsupported_response"
                    stop = True
                    break
                next_cursor = metadata.get("continue")
                if next_cursor is not None and not isinstance(next_cursor, str):
                    partial = True
                    has_more = True
                    reason = "unsupported_response"
                    stop = True
                    break
                remaining = metadata.get("remainingItemCount")
                if not next_cursor:
                    if type(remaining) is int and remaining > 0:
                        partial = True
                        has_more = True
                        reason = "pagination_incomplete"
                        stop = True
                    break
                if len(next_cursor) > MAX_POD_CURSOR_LENGTH or next_cursor in seen_cursors:
                    partial = True
                    has_more = True
                    reason = "pagination_incomplete"
                    stop = True
                    break
                seen_cursors.add(next_cursor)
                cursor = next_cursor
                if len(pods) >= MAX_PODS:
                    partial = True
                    has_more = True
                    reason = reason or "pod_limit_reached"
                    stop = True
                    break

            if stop:
                break
            if scope_index < len(scopes) - 1 and len(pods) >= MAX_PODS:
                partial = True
                has_more = True
                reason = reason or "pod_limit_reached"
                break

        return KubernetesInventory(
            pods,
            status="partial" if partial else "observed",
            complete=not partial,
            has_more=has_more,
            omitted_pods=omitted_pods,
            reason=reason,
            observed_at=observed_at,
        )

    def declared_services(self, pod: dict[str, Any]) -> list[dict[str, Any]]:
        """Resolve explicit endpoint environment values, never Secrets or arbitrary URLs."""
        namespace = pod["namespace"]
        declarations = []
        maps = {}

        def config(name):
            if name not in maps:
                maps[name] = self.transport.request(f"/api/v1/namespaces/{namespace}/configmaps/{name}").get("data", {})
            return maps[name]

        for container in pod.get("raw_spec", {}).get("containers", [])[:8]:
            values = {}
            for source in container.get("envFrom", [])[:8]:
                name = source.get("configMapRef", {}).get("name")
                if name:
                    for key, value in config(name).items():
                        values[source.get("prefix", "") + key] = (value, "ConfigMap/" + name)
            for variable in container.get("env", [])[:100]:
                key = variable.get("name", "")
                values.pop(key, None)  # Explicit Secret/downward references override envFrom too.
                reference = variable.get("valueFrom", {}).get("configMapKeyRef", {})
                if "value" in variable:
                    values[key] = (variable["value"], "PodSpec/" + pod["name"])
                elif reference.get("name"):
                    values[key] = (config(reference["name"]).get(reference.get("key"), ""), "ConfigMap/" + reference["name"])
            for key, (value, source) in values.items():
                if SENSITIVE_KEY.search(key) or not re.search(r"(?:^|_)(?:URL|HOST|ENDPOINT)$", key, re.I):
                    continue
                raw_value = str(value).strip()
                if len(raw_value) > 2048 or "@" in raw_value:
                    continue
                try:
                    endpoint = urlsplit(raw_value if "://" in raw_value else "//" + raw_value)
                    host = (endpoint.hostname or "").rstrip(".")
                    explicit_port = endpoint.port
                except ValueError:
                    continue
                scheme = endpoint.scheme.lower() if endpoint.scheme else None
                if scheme not in {None, "http", "https"}:
                    continue
                parts = host.split(".")
                if len(parts) > 1 and parts[1:] not in ([namespace], [namespace, "svc"], [namespace, "svc", "cluster", "local"]):
                    continue
                if not re.fullmatch(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", parts[0]):
                    continue
                if explicit_port is not None and not 1 <= explicit_port <= 65535:
                    continue
                if explicit_port is not None:
                    port, port_source = explicit_port, "explicit"
                elif scheme == "http":
                    port, port_source = 80, "http_default"
                elif scheme == "https":
                    port, port_source = 443, "https_default"
                else:
                    port, port_source = None, "not_declared"
                port_key = key.rsplit("_", 1)[0] + "_PORT" if "_" in key else "PORT"
                port_source_path = None
                if port is None and port_key in values:
                    paired_value, paired_source = values[port_key]
                    paired_text = str(paired_value).strip()
                    if re.fullmatch(r"[0-9]{1,5}", paired_text) and 1 <= int(paired_text) <= 65535:
                        port, port_source = int(paired_text), "paired_environment"
                        port_source_path = paired_source + ":" + port_key
                declaration = {
                    "service": parts[0],
                    "namespace": namespace,
                    "configured_via": source + ":" + key,
                    "configured_endpoint": {"host": host, "scheme": scheme, "port": port, "port_source": port_source},
                    "observed_at": _observed_at(),
                }
                if port_source_path:
                    declaration["port_configured_via"] = port_source_path
                declarations.append(declaration)
        unique = {}
        for item in declarations:
            endpoint = item["configured_endpoint"]
            identity = (item["service"], item["configured_via"], endpoint["host"], endpoint["scheme"], endpoint["port"])
            unique[identity] = item
        return [unique[key] for key in sorted(unique)][:12]

    def service_pods(self, namespace: str, service: str, pods: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.resolve_service(namespace, service, pods)["pods"]

    def resolve_service(self, namespace: str, service: str, pods: list[dict[str, Any]]) -> dict[str, Any]:
        """Read one declared, selector-backed local Service and its current matching pods."""
        for value in (namespace, service):
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
                raise ValueError("Invalid Kubernetes identity")
        resource = self.transport.request(f"/api/v1/namespaces/{namespace}/services/{service}")
        spec = resource.get("spec", {}) if isinstance(resource.get("spec"), dict) else {}
        selector = spec.get("selector", {}) if isinstance(spec.get("selector"), dict) else {}
        if spec.get("type") == "ExternalName" or not selector:
            raise ValueError("Only selector-backed local Services are supported")
        matched_pods = sorted((pod for pod in pods if pod.get("namespace") == namespace
                               and all(pod.get("labels", {}).get(key) == value for key, value in selector.items())),
                              key=lambda pod: (not pod.get("ready", False), pod["name"]))
        ports = []
        raw_ports = spec.get("ports", [])
        for port in raw_ports[:12] if isinstance(raw_ports, list) else []:
            if not isinstance(port, dict):
                continue
            number = port.get("port")
            if type(number) is not int or not 1 <= number <= 65535:
                continue
            target_port = port.get("targetPort")
            if type(target_port) is int:
                target_port = target_port if 1 <= target_port <= 65535 else None
            elif target_port is not None:
                target_port = str(target_port)[:63]
            ports.append({
                "name": str(port.get("name") or "")[:63] or None,
                "port": number,
                "target_port": target_port,
                "protocol": port.get("protocol") if port.get("protocol") in {"TCP", "UDP", "SCTP"} else "TCP",
            })
        metadata = resource.get("metadata", {}) if isinstance(resource.get("metadata"), dict) else {}
        observed_at = _observed_at()
        return {
            "service": {
                "name": service,
                "namespace": namespace,
                "type": spec.get("type", "ClusterIP"),
                "labels": _safe_labels(metadata.get("labels", {})),
                "selector": _safe_labels(selector),
                "ports": ports,
                "resource_version": str(metadata.get("resourceVersion") or "")[:80] or None,
                "source": "Kubernetes API Service",
                "observed_at": observed_at,
            },
            "pods": matched_pods,
        }

    def list_services(self, namespace: str) -> list[dict[str, Any]]:
        payload = self.transport.request(f"/api/v1/namespaces/{namespace}/services")
        result = []
        observed_at = _observed_at()
        for item in payload.get("items", [])[:200]:
            metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
            spec = item.get("spec", {}) if isinstance(item.get("spec"), dict) else {}
            name = str(metadata.get("name") or "")
            if not name:
                continue
            raw_ports = spec.get("ports")
            result.append(
                {
                    "name": name,
                    "namespace": namespace,
                    "labels": _safe_labels(metadata.get("labels") or {}),
                    "selector": _safe_labels(spec.get("selector") or {}),
                    "ports": [
                        {"name": str(port.get("name") or "")[:63] or None,
                         "port": port.get("port") if type(port.get("port")) is int and 1 <= port.get("port") <= 65535 else None,
                         "target_port": port.get("targetPort") if type(port.get("targetPort")) is int else str(port.get("targetPort"))[:63] if port.get("targetPort") is not None else None}
                         for port in (raw_ports[:12] if isinstance(raw_ports, list) else []) if isinstance(port, dict)
                    ],
                    "omitted_ports": max(0, len(raw_ports) - 12) if isinstance(raw_ports, list) else 0,
                    "ports_complete": isinstance(raw_ports, list) and len(raw_ports) <= 12
                        and all(isinstance(port, dict) for port in raw_ports),
                    "source": "Kubernetes API ServiceList",
                    "observed_at": observed_at,
                }
            )
        return result

    def list_endpoint_slices(self, namespace: str, service_names: set[str] | None = None) -> dict[str, Any]:
        """Return bounded EndpointSlice readiness facts for Services in one namespace."""
        endpoint = f"/apis/discovery.k8s.io/v1/namespaces/{namespace}/endpointslices"
        wanted_services = ({name for name in service_names if isinstance(name, str)}
                           if service_names is not None else None)
        if wanted_services == set():
            return {"status": "observed", "complete": True, "has_more": False,
                    "slices": [], "omitted_slices": 0, "observed_at": _observed_at()}

        # Keep each selector under the practical URL budget. Invalid label values
        # fall back to bounded client-side filtering rather than entering a selector.
        selector_groups: list[list[str] | None] = [None]
        if wanted_services is not None and all(LABEL_VALUE.fullmatch(name) for name in wanted_services):
            selector_groups = []
            group: list[str] = []
            for name in sorted(wanted_services):
                candidate = [*group, name]
                selector = f"kubernetes.io/service-name in ({','.join(candidate)})"
                if group and len(urlencode({"labelSelector": selector})) > MAX_ENDPOINT_SLICE_SELECTOR_QUERY:
                    selector_groups.append(group)
                    group = [name]
                else:
                    group = candidate
            if group:
                selector_groups.append(group)

        observed_at: str | None = None
        slices = []
        omitted_slices = 0
        partial = False
        has_more = False
        failure_reason: str | None = None
        page_count = 0
        stop = False

        def retain(item: Any) -> None:
            nonlocal omitted_slices, partial, stop
            if not isinstance(item, dict):
                partial = True
                return
            metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
            labels = metadata.get("labels", {}) if isinstance(metadata.get("labels"), dict) else {}
            service = str(labels.get("kubernetes.io/service-name") or "")
            if not service or (wanted_services is not None and service not in wanted_services):
                return
            if len(slices) >= MAX_ENDPOINT_SLICES:
                omitted_slices += 1
                partial = True
                stop = True
                return

            raw_endpoints_value = item.get("endpoints")
            raw_endpoints = raw_endpoints_value if isinstance(raw_endpoints_value, list) else []
            endpoint_rows = []
            for endpoint_row in raw_endpoints[:MAX_ENDPOINT_SLICE_ENDPOINTS]:
                if not isinstance(endpoint_row, dict):
                    continue
                target = endpoint_row.get("targetRef") if isinstance(endpoint_row.get("targetRef"), dict) else {}
                conditions = endpoint_row.get("conditions") if isinstance(endpoint_row.get("conditions"), dict) else {}
                target_ref = {key: str(target[key])[:253] for key in ("kind", "name", "namespace")
                              if isinstance(target.get(key), str) and target[key]}
                endpoint_rows.append({
                    "target_ref": target_ref,
                    "ready": conditions.get("ready") if type(conditions.get("ready")) is bool else None,
                    "serving": conditions.get("serving") if type(conditions.get("serving")) is bool else None,
                    "terminating": conditions.get("terminating") if type(conditions.get("terminating")) is bool else None,
                    "address_count": len(endpoint_row.get("addresses", []))
                    if isinstance(endpoint_row.get("addresses"), list) else None,
                })
            omitted_endpoints = max(0, len(raw_endpoints) - len(endpoint_rows))
            endpoints_complete = (
                (raw_endpoints_value is None or isinstance(raw_endpoints_value, list))
                and omitted_endpoints == 0
            )
            if not endpoints_complete:
                partial = True

            raw_ports_value = item.get("ports")
            raw_ports = raw_ports_value if isinstance(raw_ports_value, list) else []
            ports = []
            for port in raw_ports[:MAX_ENDPOINT_SLICE_PORTS]:
                if not isinstance(port, dict):
                    continue
                number = port.get("port")
                ports.append({
                    "name": str(port.get("name"))[:63] if isinstance(port.get("name"), str) and port.get("name") else None,
                    "port": number if type(number) is int and 1 <= number <= 65535 else None,
                    "protocol": str(port.get("protocol") or "TCP")[:16],
                })
            omitted_ports = max(0, len(raw_ports) - len(ports))
            ports_complete = (
                (raw_ports_value is None or isinstance(raw_ports_value, list))
                and omitted_ports == 0
            )
            if not ports_complete:
                partial = True

            slices.append({
                "name": str(metadata.get("name") or "")[:253],
                "namespace": namespace,
                "service": service[:253],
                "address_type": str(item.get("addressType") or "")[:32] or None,
                "ports": ports,
                "endpoints": endpoint_rows,
                "omitted_endpoints": omitted_endpoints,
                "endpoints_complete": endpoints_complete,
                "omitted_ports": omitted_ports,
                "ports_complete": ports_complete,
                "source": "Kubernetes API EndpointSliceList",
                "observed_at": observed_at,
            })

        for group_index, names in enumerate(selector_groups):
            if stop:
                break
            cursor: str | None = None
            seen_cursors: set[str] = set()
            group_selector = (f"kubernetes.io/service-name in ({','.join(names)})"
                              if names is not None else None)
            while True:
                if page_count >= MAX_ENDPOINT_SLICE_PAGES:
                    partial = True
                    has_more = True
                    stop = True
                    break
                params: dict[str, str] = {"limit": str(MAX_ENDPOINT_SLICE_PAGE_SIZE)}
                if group_selector is not None:
                    params["labelSelector"] = group_selector
                if cursor is not None:
                    params["continue"] = cursor
                request_path = f"{endpoint}?{urlencode(params)}"
                try:
                    payload = self.transport.request(
                        request_path, max_response_bytes=MAX_ENDPOINT_SLICE_BYTES,
                    )
                except ResponseTooLargeError:
                    if page_count == 0:
                        return {"status": "unavailable", "complete": False, "has_more": None,
                                "reason": "response_too_large",
                                "slices": [], "omitted_slices": 0, "observed_at": None}
                    partial = True
                    has_more = True
                    failure_reason = "page_unavailable"
                    stop = True
                    break
                except (RuntimeError, OSError, ValueError):
                    if page_count == 0:
                        return {"status": "unavailable", "complete": False, "has_more": None,
                                "reason": "request_failed", "slices": [],
                                "omitted_slices": 0, "observed_at": None}
                    partial = True
                    has_more = True
                    failure_reason = "page_unavailable"
                    stop = True
                    break
                if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                    if page_count == 0:
                        return {"status": "unavailable", "complete": False, "has_more": None,
                                "reason": "unsupported_response", "slices": [],
                                "omitted_slices": 0, "observed_at": None}
                    partial = True
                    has_more = True
                    failure_reason = "unsupported_response"
                    stop = True
                    break

                page_count += 1
                if observed_at is None:
                    observed_at = _observed_at()
                raw_items = payload["items"]
                page_items = raw_items[:MAX_ENDPOINT_SLICE_PAGE_SIZE]
                if len(raw_items) > MAX_ENDPOINT_SLICE_PAGE_SIZE:
                    partial = True
                    has_more = True
                for item in page_items:
                    retain(item)
                    if stop:
                        break
                if stop:
                    has_more = True
                    break

                metadata = payload.get("metadata")
                if not isinstance(metadata, dict):
                    partial = True
                    has_more = True
                    failure_reason = "unsupported_response"
                    stop = True
                    break
                next_cursor = metadata.get("continue")
                if next_cursor is not None and not isinstance(next_cursor, str):
                    partial = True
                    has_more = True
                    failure_reason = "unsupported_response"
                    stop = True
                    break
                if not next_cursor:
                    break
                if len(next_cursor) > MAX_ENDPOINT_SLICE_CURSOR_LENGTH or next_cursor in seen_cursors:
                    partial = True
                    has_more = True
                    failure_reason = "pagination_incomplete"
                    stop = True
                    break
                seen_cursors.add(next_cursor)
                cursor = next_cursor

            if stop:
                break
            if group_index < len(selector_groups) - 1 and len(slices) >= MAX_ENDPOINT_SLICES:
                partial = True
                has_more = True
                break

        return {
            "status": "partial" if partial else "observed",
            "complete": not partial,
            "has_more": has_more,
            "reason": failure_reason,
            "slices": slices,
            "omitted_slices": omitted_slices,
            "observed_at": observed_at,
        }

    def monitoring_resources(self, namespaces: set[str]) -> list[dict[str, Any]]:
        """Read bounded Prometheus-operator selectors relevant to captured namespaces."""

        resources = []
        observed_at = _observed_at()
        for plural, kind, endpoint_key in (
            ("servicemonitors", "ServiceMonitor", "endpoints"),
            ("podmonitors", "PodMonitor", "podMetricsEndpoints"),
        ):
            payload = self.transport.request(f"/apis/monitoring.coreos.com/v1/{plural}")
            for item in payload.get("items", [])[:200]:
                metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
                spec = item.get("spec", {}) if isinstance(item.get("spec"), dict) else {}
                monitor_namespace = str(metadata.get("namespace") or "default")
                raw_namespace_selector = spec.get("namespaceSelector", {})
                namespace_selector_valid = isinstance(raw_namespace_selector, dict)
                namespace_selector = raw_namespace_selector if namespace_selector_valid else {}
                any_namespace = namespace_selector.get("any", False)
                raw_names = namespace_selector.get("matchNames", [])
                if type(any_namespace) is not bool or not isinstance(raw_names, list):
                    namespace_selector_valid = False
                match_names = [str(value)[:253] for value in raw_names[:100] if isinstance(value, str)] if isinstance(raw_names, list) else []
                if isinstance(raw_names, list) and len(match_names) != len(raw_names):
                    namespace_selector_valid = False
                if isinstance(raw_names, list) and len(raw_names) > 100:
                    namespace_selector_valid = False
                if namespace_selector_valid and any_namespace is True:
                    effective_namespaces = sorted(namespaces)
                elif namespace_selector_valid and match_names:
                    effective_namespaces = sorted(set(match_names).intersection(namespaces))
                elif namespace_selector_valid:
                    effective_namespaces = [monitor_namespace] if monitor_namespace in namespaces else []
                else:
                    # Keep malformed scope visible as a candidate only inside the
                    # requested observation scope; the caller must retain unknown.
                    effective_namespaces = sorted(namespaces)
                if not effective_namespaces:
                    continue
                raw_selector = spec.get("selector", {})
                match_labels, match_expressions, selector_complete = _selector_parts(raw_selector)
                if "selector" not in spec or not isinstance(raw_selector, dict):
                    selector_complete = False
                raw_match_labels = raw_selector.get("matchLabels", {}) if isinstance(raw_selector, dict) else {}
                if isinstance(raw_match_labels, dict) and len(raw_match_labels) > MAX_SELECTOR_LABELS:
                    selector_complete = False
                endpoints = []
                raw_endpoints = spec.get(endpoint_key, [])
                for endpoint in raw_endpoints[:12] if isinstance(raw_endpoints, list) else []:
                    if not isinstance(endpoint, dict):
                        continue
                    record = {}
                    for key in ("port", "portNumber", "targetPort", "path", "interval", "scheme"):
                        value = endpoint.get(key)
                        if value is None:
                            continue
                        if isinstance(value, (int, float)):
                            record[key] = value
                        else:
                            text = str(value)
                            if key == "path":
                                text = text.split("?", 1)[0].split("#", 1)[0]
                            record[key] = text[:160]
                    endpoints.append(record)
                resources.append(
                    {
                        "kind": kind,
                        "name": str(metadata.get("name") or "unknown"),
                        "namespace": monitor_namespace,
                        "target_namespaces": effective_namespaces,
                        "effective_namespaces": effective_namespaces,
                        "namespace_selector": {
                            "any": any_namespace is True,
                            "match_names": match_names[:8],
                            "omitted_match_names": max(0, len(match_names) - 8),
                            "defaults_to_monitor_namespace": not any_namespace and not match_names,
                            "status": "resolved" if namespace_selector_valid else "unknown",
                        },
                        "match_labels": match_labels,
                        "match_expressions": match_expressions,
                        "selector_complete": selector_complete,
                        "endpoints": endpoints,
                        "endpoints_complete": isinstance(raw_endpoints, list) and len(raw_endpoints) <= 12,
                        "omitted_endpoints": max(0, len(raw_endpoints) - len(endpoints)) if isinstance(raw_endpoints, list) else 0,
                        "resource_version": str(metadata.get("resourceVersion") or "")[:80] or None,
                        "source": f"Kubernetes API {kind}",
                        "observed_at": observed_at,
                    }
                )
        return resources[:80]

    def configuration_snapshot(self, pod: dict[str, Any]) -> list[dict[str, Any]]:
        namespace = str(pod["namespace"])
        names = _referenced_configmaps(pod.get("raw_spec", {}))
        items: list[dict[str, Any]] = [
            {
                "kind": "PodSpec",
                "name": pod["name"],
                "namespace": namespace,
                "workload": pod.get("workload"),
                "images": pod.get("images", []),
                "containers": pod.get("containers", []),
                "node": pod.get("node"),
                "phase": pod.get("phase"),
                "ready": pod.get("ready"),
                "configmap_refs": sorted(names),
                "uid": pod.get("uid"),
                "resources": [
                    {"name": item.get("name"), "requests": item.get("resources", {}).get("requests", {}),
                     "limits": item.get("resources", {}).get("limits", {})}
                    for item in pod.get("raw_spec", {}).get("containers", [])
                ],
                "container_states": [
                    {"name": item.get("name"), "restart_count": item.get("restartCount", 0),
                     "ready": item.get("ready"), "state": _termination_state(item.get("state", {})),
                     "last_state": _termination_state(item.get("lastState", {}))}
                    for item in pod.get("container_statuses", [])
                ],
            }
        ]
        for name in sorted(names):
            try:
                configmap = self.transport.request(f"/api/v1/namespaces/{namespace}/configmaps/{name}")
            except RuntimeError as exc:
                items.append({"kind": "ConfigMap", "name": name, "namespace": namespace, "error": str(exc)})
                continue
            data = configmap.get("data", {}) if isinstance(configmap.get("data"), dict) else {}
            sanitized = {
                str(key): "<redacted>" if SENSITIVE_KEY.search(str(key)) else str(value)[:2000]
                for key, value in data.items()
            }
            digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()[:16]
            items.append(
                {
                    "kind": "ConfigMap",
                    "name": name,
                    "namespace": namespace,
                    "resource_version": configmap.get("metadata", {}).get("resourceVersion"),
                    "content_hash": digest,
                    "keys": sorted(str(key) for key in data),
                    "data": sanitized,
                }
            )
        return items


def _termination_state(state: dict[str, Any]) -> dict[str, Any]:
    # Preserve diagnostic fields, never termination messages containing arbitrary logs.
    allowed = {"reason", "exitCode", "signal", "startedAt", "finishedAt"}
    return {kind: {key: value for key, value in fields.items() if key in allowed}
            for kind, fields in state.items() if isinstance(fields, dict)}


def _referenced_configmaps(spec: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for volume in spec.get("volumes", []):
        if isinstance(volume.get("configMap"), dict) and volume["configMap"].get("name"):
            names.add(str(volume["configMap"]["name"]))
    for container in [*spec.get("initContainers", []), *spec.get("containers", [])]:
        for source in container.get("envFrom", []):
            reference = source.get("configMapRef", {})
            if reference.get("name"):
                names.add(str(reference["name"]))
        for variable in container.get("env", []):
            reference = variable.get("valueFrom", {}).get("configMapKeyRef", {})
            if reference.get("name"):
                names.add(str(reference["name"]))
    return names
