"""Trust policy for configured telemetry and Kubernetes API endpoints."""

from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlsplit


DEFAULT_SOURCE_URLS = {
    "prometheus": "http://prometheus-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090",
    "opensearch": "http://opensearch.logging.svc.cluster.local:9200",
}


def default_kubernetes_url() -> str:
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS", "443")
    authority = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"https://{authority}:{port}"


def _origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value.strip())
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").rstrip(".").lower()
        if scheme not in {"http", "https"} or not host or parsed.username is not None or parsed.password is not None:
            return None
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None
    return scheme, host, port


def _configured_origins(source: str) -> set[tuple[str, str, int]]:
    if source == "kubernetes":
        configured = os.environ.get("FCAPSULE_KUBERNETES_URL", "").strip()
        if configured:
            values = [configured]
        else:
            values = [default_kubernetes_url()]
    else:
        env_name = f"FCAPSULE_{source.upper()}_URL"
        configured = os.environ.get(env_name, "").strip()
        values = [configured or DEFAULT_SOURCE_URLS[source]]

    values.extend(os.environ.get("FCAPSULE_SOURCE_ALLOWED_ORIGINS", "").split(","))
    return {origin for value in values if (origin := _origin(value)) is not None}


def validate_source_url(
    value: object,
    source: str,
    label: str,
    *,
    allow_empty: bool = False,
) -> str:
    """Require the URL origin to be pinned by deployment configuration."""
    if source not in {"prometheus", "opensearch", "kubernetes"}:
        raise ValueError("Unknown source type")
    text = str(value or "").strip().rstrip("/")
    if not text and allow_empty:
        return ""
    parsed_origin = _origin(text)
    if parsed_origin is None:
        raise ValueError(f"{label} must be an absolute http(s) URL without embedded credentials")
    parsed = urlsplit(text)
    if parsed.query or parsed.fragment or any(ord(character) < 32 for character in text):
        raise ValueError(f"{label} cannot contain a query, fragment, or control character")
    if source == "kubernetes" and parsed.scheme.lower() != "https":
        raise ValueError("Kubernetes URL must use HTTPS")

    _, host, _ = parsed_origin
    if host in {"metadata", "metadata.google.internal", "metadata.azure.internal", "instance-data"}:
        raise ValueError(f"{label} points to a blocked metadata service")
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError(f"{label} cannot point to localhost")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_loopback or address.is_link_local or address.is_multicast
        or address.is_unspecified or address.is_reserved
    ):
        raise ValueError(f"{label} points to a blocked IP address")

    if parsed_origin not in _configured_origins(source):
        raise ValueError(
            f"{label} origin is not trusted; configure its exact origin in FCAPSULE_SOURCE_ALLOWED_ORIGINS"
        )
    return text
