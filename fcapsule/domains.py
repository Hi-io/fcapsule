"""Operational signal-domain definitions used across FCAPSule."""

from __future__ import annotations

from collections import Counter
from typing import Any


OBSERVABILITY_DOMAINS: dict[str, dict[str, Any]] = {
    "fault_events": {
        "label": "Fault management (FM)",
        "signal_family": "Discrete alert and incident event stream",
        "source_examples": ["Alertmanager alert", "synthetic alert rule result"],
        "methods": ["severity ranking", "timeline construction", "entity matching"],
        "role": "Defines the incident trigger, severity, and investigation window.",
    },
    "log_text": {
        "label": "Application logs",
        "signal_family": "Semi-structured textual telemetry",
        "source_examples": ["application JSON logs", "representative log lines"],
        "methods": ["masking", "template extraction", "frequency analysis", "severity detection"],
        "role": "Explains repeated application behavior without retaining every raw line.",
    },
    "time_series_metrics": {
        "label": "Performance management (PM)",
        "signal_family": "Numeric measurements over time",
        "source_examples": ["Prometheus-style counters", "latency gauges", "runtime gauges"],
        "methods": ["counter delta analysis", "baseline comparison", "robust anomaly scoring"],
        "role": "Shows whether performance and capacity changed during the alert window.",
    },
    "topology_metadata": {
        "label": "Topology and metadata",
        "signal_family": "Entity identity and relationship context",
        "source_examples": ["service", "namespace", "cluster", "pod", "CNCC UUID"],
        "methods": ["entity normalization", "cross-source alignment", "coverage checks"],
        "role": "Connects evidence from different telemetry domains to the same system entity.",
    },
    "configuration_state": {
        "label": "Configuration state",
        "signal_family": "Kubernetes workload and referenced ConfigMap snapshots",
        "source_examples": ["PodSpec", "referenced ConfigMap"],
        "methods": ["reference resolution", "secret-key masking", "content hashing"],
        "role": "Preserves the deployment and non-secret configuration context present during the incident.",
    },
    "trace_access": {
        "label": "On-demand traces",
        "signal_family": "Ephemeral request-path evidence queried only when needed",
        "source_examples": ["OpenTelemetry collector", "temporary trace buffer"],
        "methods": ["availability probe", "retention-window check", "request-time retrieval"],
        "role": "Confirms that traces can be retrieved during investigation without retaining raw spans in FCAPSule.",
    },
    "llm_reasoning": {
        "label": "LLM reasoning",
        "signal_family": "Generated evidence-grounded interpretation",
        "source_examples": ["hypotheses", "next checks", "missing evidence notes"],
        "methods": ["prompted analysis", "citation validation", "rubric evaluation"],
        "role": "Turns selected evidence into structured investigation notes while preserving citations.",
    },
}


TYPE_TO_DOMAIN = {
    "alert": "fault_events",
    "log_template": "log_text",
    "metric_anomaly": "time_series_metrics",
    "configuration": "configuration_state",
}


def domain_for_evidence_type(evidence_type: str) -> str:
    """Return the observability domain for a selected evidence item type."""

    return TYPE_TO_DOMAIN.get(evidence_type, "topology_metadata")


def build_domain_summary(payload: dict[str, Any], candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build a compact, explicit summary of operational telemetry domains."""

    selected = payload.get("selected_evidence", [])
    evidence_counts = Counter(item.get("domain", domain_for_evidence_type(item.get("type", ""))) for item in selected)
    candidate_counts = Counter(
        item.get("domain", domain_for_evidence_type(item.get("type", ""))) for item in (candidates or [])
    )
    result: dict[str, Any] = {}
    for domain_id, definition in OBSERVABILITY_DOMAINS.items():
        count = evidence_counts.get(domain_id, 0)
        raw_count = 0
        if domain_id == "fault_events":
            raw_count = len(payload.get("alerts", []))
        elif domain_id == "log_text":
            raw_count = payload.get("log_summary", {}).get("raw_lines", 0)
        elif domain_id == "time_series_metrics":
            raw_count = len(payload.get("metric_anomalies", []))
        elif domain_id == "topology_metadata":
            raw_count = len(payload.get("entity_resolution", {}).get("entities", {}))
            if not raw_count:
                raw_count = len(payload.get("case", {}).get("topology", []))
        elif domain_id == "configuration_state":
            raw_count = len(payload.get("configuration", []))
        elif domain_id == "trace_access":
            raw_count = int(bool(payload.get("case", {}).get("trace_access", {}).get("available")))
        elif domain_id == "llm_reasoning":
            raw_count = len(payload.get("hypotheses", []))
        result[domain_id] = {
            **definition,
            "raw_items": raw_count,
            "candidate_evidence_items": candidate_counts.get(domain_id, 0),
            "selected_evidence_items": count,
        }
    return result
