"""Future live telemetry adapters."""
"""Read-only observability and Kubernetes source adapters."""

from fcapsule.adapters.kubernetes_adapter import KubernetesAdapter
from fcapsule.adapters.opensearch_adapter import OpenSearchAdapter
from fcapsule.adapters.prometheus_adapter import PrometheusAdapter

__all__ = ["KubernetesAdapter", "OpenSearchAdapter", "PrometheusAdapter"]
