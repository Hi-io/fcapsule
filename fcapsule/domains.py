"""Observability domain definitions used across the P1 pipeline."""

from __future__ import annotations

from collections import Counter
from typing import Any


OBSERVABILITY_DOMAINS: dict[str, dict[str, Any]] = {
    "fault_events": {
        "label": "Fault events",
        "signal_family": "Discrete alert and incident event stream",
        "source_examples": ["Alertmanager alert", "synthetic alert rule result"],
        "methods": ["severity ranking", "timeline construction", "entity matching"],
        "p1_role": "Defines the incident trigger and the investigation window.",
    },
    "log_text": {
        "label": "Log text",
        "signal_family": "Semi-structured textual telemetry",
        "source_examples": ["application JSON logs", "representative log lines"],
        "methods": ["masking", "template extraction", "frequency analysis", "severity detection"],
        "p1_role": "Explains repeated application behavior without retaining every raw line.",
    },
    "time_series_metrics": {
        "label": "Time-series metrics",
        "signal_family": "Numeric measurements over time",
        "source_examples": ["Prometheus-style counters", "latency gauges", "runtime gauges"],
        "methods": ["counter delta analysis", "baseline comparison", "robust anomaly scoring"],
        "p1_role": "Shows whether numeric behavior changed during the alert window.",
    },
    "topology_metadata": {
        "label": "Topology and metadata",
        "signal_family": "Entity identity and relationship context",
        "source_examples": ["service", "namespace", "cluster", "pod", "CNCC UUID"],
        "methods": ["entity normalization", "cross-source alignment", "coverage checks"],
        "p1_role": "Connects evidence from different telemetry domains to the same system entity.",
    },
    "llm_reasoning": {
        "label": "LLM reasoning",
        "signal_family": "Generated evidence-grounded interpretation",
        "source_examples": ["hypotheses", "next checks", "missing evidence notes"],
        "methods": ["prompted analysis", "citation validation", "rubric evaluation"],
        "p1_role": "Turns selected evidence into structured investigation notes while preserving citations.",
    },
}


TYPE_TO_DOMAIN = {
    "alert": "fault_events",
    "log_template": "log_text",
    "metric_anomaly": "time_series_metrics",
}


def domain_for_evidence_type(evidence_type: str) -> str:
    """Return the observability domain for a selected evidence item type."""

    return TYPE_TO_DOMAIN.get(evidence_type, "topology_metadata")


def build_domain_summary(payload: dict[str, Any], candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Build a compact, explicit summary of the P1 telemetry domains."""

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
        elif domain_id == "llm_reasoning":
            raw_count = len(payload.get("hypotheses", []))
        result[domain_id] = {
            **definition,
            "raw_items": raw_count,
            "candidate_evidence_items": candidate_counts.get(domain_id, 0),
            "selected_evidence_items": count,
        }
    return result
