"""Deterministic, evidence-only hypothesis generator for reproducible P1 runs."""

from __future__ import annotations

from typing import Any


def _first(items: list[dict[str, Any]], evidence_type: str, terms: tuple[str, ...] = ()) -> dict[str, Any] | None:
    matches = [item for item in items if item["type"] == evidence_type]
    if terms:
        semantic = [item for item in matches if any(term in (item["title"] + " " + item["summary"]).lower() for term in terms)]
        if semantic:
            return semantic[0]
    return matches[0] if matches else None


def generate_hypotheses(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hypotheses: list[dict[str, Any]] = []
    alert = _first(selected, "alert")
    error_log = _first(selected, "log_template", ("failed", "error", "timeout", "unavailable"))
    error_metric = _first(selected, "metric_anomaly", ("error", "failure"))
    latency_metric = _first(selected, "metric_anomaly", ("latency", "duration"))
    log_metric = _first(selected, "metric_anomaly", ("log", "dropped", "queue"))

    if error_log and (error_metric or alert):
        support = [error_log["evidence_id"]]
        if error_metric:
            support.append(error_metric["evidence_id"])
        if alert:
            support.append(alert["evidence_id"])
        hypotheses.append(
            {
                "hypothesis_id": "hyp_001",
                "hypothesis": "A dependency or request-processing failure may be driving the observed service errors.",
                "confidence": round(min(0.85, sum(next(item["score"] for item in selected if item["evidence_id"] == value) for value in support) / len(support)), 3),
                "supporting_evidence": support,
                "contradicting_evidence": [],
                "missing_evidence": ["Upstream dependency health and distributed traces"],
                "next_checks": ["Inspect dependency health during the alert window.", "Trace failed requests across the affected pod and upstream service."],
            }
        )

    if latency_metric:
        support = [latency_metric["evidence_id"]]
        if error_log:
            support.append(error_log["evidence_id"])
        hypotheses.append(
            {
                "hypothesis_id": f"hyp_{len(hypotheses) + 1:03d}",
                "hypothesis": "Elevated request latency may be part of the same degradation window as the alert.",
                "confidence": round(sum(next(item["score"] for item in selected if item["evidence_id"] == value) for value in support) / len(support) * 0.85, 3),
                "supporting_evidence": support,
                "contradicting_evidence": [],
                "missing_evidence": ["Per-endpoint latency and saturation metrics"],
                "next_checks": ["Break latency down by endpoint and dependency.", "Check CPU, memory, and worker saturation around the peak."],
            }
        )

    if log_metric:
        support = [log_metric["evidence_id"]]
        if alert:
            support.append(alert["evidence_id"])
        hypotheses.append(
            {
                "hypothesis_id": f"hyp_{len(hypotheses) + 1:03d}",
                "hypothesis": "Higher application log production may add ingestion pressure, but an effect on telemetry completeness is not established.",
                "confidence": round(sum(next(item["score"] for item in selected if item["evidence_id"] == value) for value in support) / len(support) * 0.8, 3),
                "supporting_evidence": support,
                "contradicting_evidence": [],
                "missing_evidence": ["Kafka consumer lag and OpenSearch indexing queue metrics"],
                "next_checks": ["Check log pipeline lag and rejected events.", "Confirm whether late-arriving logs exist after the captured window."],
            }
        )

    if not hypotheses and selected:
        support = [item["evidence_id"] for item in selected[:3]]
        hypotheses.append(
            {
                "hypothesis_id": "hyp_001",
                "hypothesis": "The selected telemetry indicates a bounded degradation that requires cross-domain investigation.",
                "confidence": round(sum(item["score"] for item in selected[:3]) / len(support) * 0.65, 3),
                "supporting_evidence": support,
                "contradicting_evidence": [],
                "missing_evidence": ["Additional service-specific diagnostic telemetry"],
                "next_checks": ["Inspect the highest-ranked evidence in timestamp order."],
            }
        )
    return hypotheses
