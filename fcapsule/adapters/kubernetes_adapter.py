"""Read-only Kubernetes API adapter using an in-cluster ServiceAccount."""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fcapsule.adapters.transport import JsonTransport

SERVICE_ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")
SENSITIVE_KEY = re.compile(r"password|passwd|secret|token|credential|api[-_]?key|private[-_]?key", re.I)


class KubernetesAdapter:
    def __init__(self, base_url: str | None = None, timeout: float = 8) -> None:
        host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
        self.base_url = (base_url or f"https://{host}:{port}").rstrip("/")
        token_path = SERVICE_ACCOUNT / "token"
        ca_path = SERVICE_ACCOUNT / "ca.crt"
        token = token_path.read_text(encoding="utf-8").strip() if token_path.is_file() else os.environ.get("FCAPSULE_KUBERNETES_TOKEN", "")
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

    def list_pods(self, namespaces: set[str] | None = None) -> list[dict[str, Any]]:
        payload = self.transport.request("/api/v1/pods")
        pods = []
        for item in payload.get("items", []):
            metadata = item.get("metadata", {})
            namespace = str(metadata.get("namespace", "default"))
            if namespaces and namespace not in namespaces:
                continue
            status = item.get("status", {})
            spec = item.get("spec", {})
            labels = metadata.get("labels", {})
            owners = metadata.get("ownerReferences", [])
            owner = owners[0] if owners else {}
            ready = any(condition.get("type") == "Ready" and condition.get("status") == "True" for condition in status.get("conditions", []))
            pod_name = str(metadata.get("name", "unknown"))
            workload = labels.get("app.kubernetes.io/name") or labels.get("app") or labels.get("k8s-app") or owner.get("name") or pod_name
            pods.append(
                {
                    "name": pod_name,
                    "namespace": namespace,
                    "uid": metadata.get("uid"),
                    "workload": str(workload),
                    "owner_kind": owner.get("kind"),
                    "owner_name": owner.get("name"),
                    "node": spec.get("nodeName"),
                    "phase": status.get("phase", "Unknown"),
                    "ready": ready,
                    "labels": labels,
                    "containers": [container.get("name") for container in spec.get("containers", [])],
                    "images": [container.get("image") for container in spec.get("containers", [])],
                    "raw_spec": spec,
                    "container_statuses": status.get("containerStatuses", []),
                }
            )
        return pods

    def declared_services(self, pod: dict[str, Any]) -> list[dict[str, str]]:
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
                try:
                    endpoint = urlsplit(str(value) if "://" in str(value) else "//" + str(value))
                    host = (endpoint.hostname or "").rstrip(".")
                except ValueError:
                    continue
                parts = host.split(".")
                if len(parts) > 1 and parts[1:] not in ([namespace], [namespace, "svc"], [namespace, "svc", "cluster", "local"]):
                    continue
                if not re.fullmatch(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", parts[0]):
                    continue
                declarations.append({"service": parts[0], "namespace": namespace, "configured_via": source + ":" + key})
        return [dict(item) for item in sorted({tuple(sorted(row.items())) for row in declarations})][:12]

    def service_pods(self, namespace: str, service: str, pods: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for value in (namespace, service):
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
                raise ValueError("Invalid Kubernetes identity")
        spec = self.transport.request(f"/api/v1/namespaces/{namespace}/services/{service}").get("spec", {})
        selector = spec.get("selector", {})
        if spec.get("type") == "ExternalName" or not selector:
            raise ValueError("Only selector-backed local Services are supported")
        return sorted((pod for pod in pods if pod.get("namespace") == namespace
                       and all(pod.get("labels", {}).get(key) == value for key, value in selector.items())),
                      key=lambda pod: (not pod.get("ready", False), pod["name"]))

    def list_services(self, namespace: str) -> list[dict[str, Any]]:
        payload = self.transport.request(f"/api/v1/namespaces/{namespace}/services")
        result = []
        for item in payload.get("items", [])[:200]:
            metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
            spec = item.get("spec", {}) if isinstance(item.get("spec"), dict) else {}
            name = str(metadata.get("name") or "")
            if not name:
                continue
            result.append(
                {
                    "name": name,
                    "namespace": namespace,
                    "labels": {str(key): str(value) for key, value in (metadata.get("labels") or {}).items()},
                    "selector": {str(key): str(value) for key, value in (spec.get("selector") or {}).items()},
                    "ports": [
                        {"name": port.get("name"), "port": port.get("port"), "target_port": port.get("targetPort")}
                        for port in spec.get("ports", [])[:12] if isinstance(port, dict)
                    ],
                }
            )
        return result

    def monitoring_resources(self, namespaces: set[str]) -> list[dict[str, Any]]:
        """Read bounded Prometheus-operator selectors without changing cluster state."""

        resources = []
        for plural, kind, endpoint_key in (
            ("servicemonitors", "ServiceMonitor", "endpoints"),
            ("podmonitors", "PodMonitor", "podMetricsEndpoints"),
        ):
            payload = self.transport.request(f"/apis/monitoring.coreos.com/v1/{plural}")
            for item in payload.get("items", [])[:200]:
                metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
                spec = item.get("spec", {}) if isinstance(item.get("spec"), dict) else {}
                monitor_namespace = str(metadata.get("namespace") or "default")
                selector = spec.get("selector", {}) if isinstance(spec.get("selector"), dict) else {}
                labels = selector.get("matchLabels", {}) if isinstance(selector.get("matchLabels"), dict) else {}
                namespace_selector = spec.get("namespaceSelector", {}) if isinstance(spec.get("namespaceSelector"), dict) else {}
                selected = namespace_selector.get("matchNames") if isinstance(namespace_selector.get("matchNames"), list) else [monitor_namespace]
                selected = [str(value) for value in selected]
                if not set(selected).intersection(namespaces):
                    continue
                endpoints = []
                for endpoint in spec.get(endpoint_key, [])[:12]:
                    if not isinstance(endpoint, dict):
                        continue
                    endpoints.append({key: endpoint.get(key) for key in ("port", "targetPort", "path", "interval", "scheme") if endpoint.get(key) is not None})
                resources.append(
                    {
                        "kind": kind,
                        "name": str(metadata.get("name") or "unknown"),
                        "namespace": monitor_namespace,
                        "target_namespaces": selected,
                        "match_labels": {str(key): str(value) for key, value in labels.items()},
                        "endpoints": endpoints,
                        "resource_version": metadata.get("resourceVersion"),
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
