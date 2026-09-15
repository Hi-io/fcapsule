"""Operator-focused incident report projection built from a retained capsule."""

from __future__ import annotations

from typing import Any


_METRIC_ORDER = (
    "request_error_rate",
    "http_errors",
    "request_latency",
    "retry_amplification",
    "pool_peak_utilization",
    "pool_exhausted",
    "slow_queries",
)


def _metric_rank(metric: dict[str, Any]) -> tuple[int, float]:
    name = str(metric.get("metric", ""))
    priority = next((index for index, term in enumerate(_METRIC_ORDER) if term in name), len(_METRIC_ORDER))
    return priority, -float(metric.get("anomaly_score", 0))


def _metric_label(name: str) -> str:
    labels = {
        "checkout_request_error_rate": "Checkout error rate",
        "checkout_http_errors_total": "Checkout errors",
        "checkout_request_latency_p95_ms": "Checkout p95 latency",
        "checkout_retry_amplification_ratio": "Retry amplification",
        "inventory_db_pool_peak_utilization_ratio": "Inventory DB pool utilization",
        "inventory_db_pool_exhausted_total": "Inventory DB pool exhaustion",
        "inventory_slow_queries_total": "Slow reservation queries",
        "inventory_reservation_latency_p95_ms": "Inventory reservation p95 latency",
    }
    return labels.get(name, name.replace("_", " ").replace("p95", "p95").title())


def _display_value(name: str, value: Any) -> str:
    number = float(value or 0)
    if name.endswith("_rate") or "utilization_ratio" in name:
        return f"{number * 100:.1f}%"
    if "amplification_ratio" in name:
        return f"{number:.2f}x"
    if name.endswith("_ms"):
        return f"{number:.0f} ms"
    if name.endswith("_total"):
        return f"{number:.0f}"
    return f"{number:.2f}"


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def build_incident_report(capsule: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Create a concise, evidence-attributable view for incident responders.

    This deliberately keeps evaluation and model experimentation out of the
    primary report. They remain available under ``engineering_diagnostics``.
    """

    case = capsule.get("case", {})
    selected = capsule.get("selected_evidence", [])
    evidence_by_id = {item.get("evidence_id"): item for item in selected}
    evidence_by_source = {item.get("source_id"): item for item in selected}
    anomalies = sorted(capsule.get("metric_anomalies", []), key=_metric_rank)
    impact = []
    for metric in anomalies:
        name = str(metric.get("metric", ""))
        if not any(term in name for term in _METRIC_ORDER):
            continue
        evidence = evidence_by_source.get(metric.get("metric_id"), {})
        impact.append(
            {
                "label": _metric_label(name),
                "value": _display_value(name, metric.get("incident_peak")),
                "baseline": _display_value(name, metric.get("baseline_median")),
                "change_percent": round(float(metric.get("percentage_change", 0)), 1),
                "timestamp": metric.get("peak_timestamp"),
                "component": metric.get("labels", {}).get("component"),
                "evidence_id": evidence.get("evidence_id"),
            }
        )
        if len(impact) == 4:
            break

    hypotheses = capsule.get("hypotheses", [])
    primary = hypotheses[0] if hypotheses else {}
    supporting_ids = primary.get("supporting_evidence", [])
    supporting = [evidence_by_id[item] for item in supporting_ids if item in evidence_by_id]
    trace_access = case.get("trace_access", {})
    retention_seconds = int(trace_access.get("source_retention_seconds", 0) or 0)
    actions = []
    for item in _dedupe(list(primary.get("next_checks", [])) + list(capsule.get("next_steps", []))):
        urgent = "trace" in item.lower() and retention_seconds > 0
        actions.append({"action": item, "priority": "urgent" if urgent else "next", "reason": "Source traces expire" if urgent else None})

    domains = capsule.get("domain_summary", {})
    coverage = []
    for domain_id, domain in domains.items():
        if domain_id == "llm_reasoning":
            continue
        retained = int(domain.get("selected_evidence_items", 0) or 0)
        if domain_id == "topology_metadata":
            detail = f"{len(case.get('topology', []))} service relationships captured"
            available = bool(case.get("topology"))
        elif domain_id == "trace_access":
            detail = "available on demand" if trace_access.get("available") else "not available"
            available = bool(trace_access.get("available"))
        else:
            detail = f"{retained} retained evidence item{'s' if retained != 1 else ''}"
            available = bool(domain.get("raw_items", 0))
        coverage.append(
            {
                "domain": domain.get("label", domain_id),
                "available": available,
                "detail": detail,
            }
        )

    evaluation = capsule.get("evaluation", {})
    return {
        "report_version": "1.0",
        "incident": {
            "incident_id": record.get("incident_id", case.get("case_id")),
            "title": case.get("case_title", record.get("summary", "Incident report")),
            "status": record.get("status", "captured"),
            "severity": record.get("severity", "warning"),
            "service": case.get("service", record.get("app_id")),
            "cluster": case.get("cluster"),
            "namespace": case.get("namespace"),
            "started_at": record.get("started_at", case.get("window", {}).get("start")),
            "summary": record.get("summary", ""),
        },
        "impact": impact,
        "primary_hypothesis": {
            "statement": primary.get("hypothesis", "No hypothesis has been generated yet."),
            "confidence": primary.get("adjusted_confidence", primary.get("confidence")),
            "verdict": primary.get("verdict", "unknown"),
            "uncertainty": _dedupe(list(primary.get("missing_evidence", [])) + list(capsule.get("missing_evidence", []))),
            "verification_notes": primary.get("verification_notes", []),
            "supporting_evidence": supporting,
        },
        "timeline": sorted(capsule.get("timeline", []), key=lambda item: item.get("timestamp", "")),
        "topology": case.get("topology", []),
        "actions": actions,
        "retention": {
            "trace_available": bool(trace_access.get("available")),
            "trace_probe_status": trace_access.get("probe_status", "not checked"),
            "source_retention_seconds": retention_seconds,
            "raw_traces_retained": bool(trace_access.get("raw_spans_retained")),
            "message": (
                "Query source traces now before the source window expires."
                if trace_access.get("available") and retention_seconds
                else "No source trace window is currently available."
            ),
        },
        "coverage": coverage,
        "supporting_evidence": selected,
        "engineering_diagnostics": {
            "selected_evidence": len(selected),
            "log_compression_ratio": evaluation.get("log_compression_ratio"),
            "important_signal_preservation": evaluation.get("important_signal_preservation"),
            "hypothesis_grounding_score": evaluation.get("hypothesis_grounding_score"),
            "runtime_seconds": evaluation.get("runtime_seconds"),
        },
    }
