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
    "container_restarts",
    "memory_working_set",
    "cpu_usage",
    "pod_ready",
)

_METRIC_CONTEXT = {
    "checkout_request_error_rate": {
        "label": "Checkout API failure rate",
        "meaning": "Share of checkout API requests that failed during the peak interval.",
    },
    "checkout_http_errors_total": {
        "label": "Checkout API failed requests",
        "meaning": "Failed checkout API requests recorded during one collection interval.",
    },
    "checkout_request_latency_p95_ms": {
        "label": "Checkout API p95 response time",
        "meaning": "95% of checkout API requests completed within this time during the peak interval.",
    },
    "checkout_retry_amplification_ratio": {
        "label": "Inventory attempts per checkout",
        "meaning": "Average inventory attempts made for each checkout request.",
    },
    "inventory_db_pool_peak_utilization_ratio": {
        "label": "Inventory database pool usage",
        "meaning": "Share of available inventory database connections in use.",
    },
    "inventory_db_pool_exhausted_total": {
        "label": "Inventory database pool exhaustion",
        "meaning": "Intervals in which reservation work could not obtain a database connection.",
    },
    "inventory_slow_queries_total": {
        "label": "Slow reservation queries",
        "meaning": "Reservation database queries that exceeded their expected duration during one collection interval.",
    },
    "pod_cpu_usage_cores": {
        "label": "Pod CPU usage",
        "meaning": "CPU consumed by the affected pod during the captured incident window.",
    },
    "pod_memory_working_set_bytes": {
        "label": "Pod memory working set",
        "meaning": "Memory actively used by the affected pod during the captured incident window.",
    },
    "pod_container_restarts_total": {
        "label": "Container restart activity",
        "meaning": "Change in the container restart counter around the incident trigger.",
    },
    "pod_ready": {
        "label": "Pod readiness",
        "meaning": "Whether the affected pod was ready to receive work during the captured window.",
    },
}

_PM_CHART_METRICS = (
    "checkout_request_error_rate",
    "checkout_request_latency_p95_ms",
    "checkout_retry_amplification_ratio",
    "inventory_db_pool_peak_utilization_ratio",
    "pod_cpu_usage_cores",
    "pod_memory_working_set_bytes",
    "pod_container_restarts_total",
    "pod_ready",
)


def _metric_rank(metric: dict[str, Any]) -> tuple[int, float]:
    name = str(metric.get("metric", ""))
    priority = next((index for index, term in enumerate(_METRIC_ORDER) if term in name), len(_METRIC_ORDER))
    return priority, -float(metric.get("anomaly_score", 0))


def _metric_label(name: str) -> str:
    if name in _METRIC_CONTEXT:
        return str(_METRIC_CONTEXT[name]["label"])
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
    if name == "pod_ready":
        return "Ready" if number >= 1 else "Not ready"
    if name.endswith("_bytes"):
        return f"{number / 1024 / 1024:.1f} MiB"
    if name.endswith("_cores"):
        return f"{number * 1000:.1f} mCPU"
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


def _impact_context(name: str, value: Any, baseline: Any) -> tuple[str, str]:
    context = _METRIC_CONTEXT.get(name, {})
    meaning = str(context.get("meaning", "Observed during the peak incident interval."))
    if name.endswith("_total"):
        baseline_text = f"Typical interval: {_display_value(name, baseline)}"
    elif "amplification_ratio" in name:
        baseline_text = f"Normal: {_display_value(name, baseline)}"
    else:
        baseline_text = f"Normal: {_display_value(name, baseline)}"
    return meaning, baseline_text


def _window_counter_increase(source_metrics: list[dict[str, Any]] | None, name: str) -> float | None:
    series = next((item for item in source_metrics or [] if item.get("metric") == name), None)
    values = [float(point[1]) for point in (series or {}).get("values", []) if isinstance(point, list) and len(point) == 2]
    return max(values) - min(values) if len(values) >= 2 else None


def _pm_signals(source_metrics: list[dict[str, Any]] | None, anomalies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep a small, chartable PM view beside the retained explanation."""

    if not source_metrics:
        return []
    anomaly_by_metric = {item.get("metric"): item for item in anomalies}
    by_name = {item.get("metric"): item for item in source_metrics}
    signals = []
    for name in _PM_CHART_METRICS:
        series = by_name.get(name)
        anomaly = anomaly_by_metric.get(name)
        if not series or not anomaly:
            continue
        values = [
            {"timestamp": str(point[0]), "value": round(float(point[1]), 4)}
            for point in series.get("values", [])
            if isinstance(point, list) and len(point) == 2
        ]
        if len(values) < 2:
            continue
        context = _METRIC_CONTEXT.get(
            name,
            {"label": _metric_label(name), "meaning": "Observed for the affected pod during the incident window."},
        )
        baseline_value = float(anomaly.get("baseline_median", 0))
        peak_value = float(anomaly.get("incident_peak", 0))
        if name == "pod_container_restarts_total":
            increase = _window_counter_increase(source_metrics, name)
            if increase is not None:
                baseline_value = 0.0
                peak_value = increase
        signals.append(
            {
                "evidence_id": f"ev_{anomaly.get('metric_id')}",
                "metric": name,
                "label": context["label"],
                "meaning": context["meaning"],
                "component": series.get("labels", {}).get("component") or series.get("labels", {}).get("pod"),
                "baseline": _display_value(name, baseline_value),
                "peak": _display_value(name, peak_value),
                "baseline_value": round(baseline_value, 4),
                "peak_value": round(peak_value, 4),
                "alert_timestamp": anomaly.get("peak_timestamp"),
                "values": values,
            }
        )
    return signals


def _fault_alerts(capsule: dict[str, Any]) -> list[dict[str, Any]]:
    alerts = []
    for index, item in enumerate(capsule.get("alerts", []), start=1):
        labels = item.get("labels", {})
        alerts.append(
            {
                "evidence_id": f"ev_alert_{index:03d}",
                "name": item.get("alertname", "Alert"),
                "severity": item.get("severity", "warning"),
                "timestamp": item.get("startsAt"),
                "description": item.get("annotations", {}).get("description", "Alert fired."),
                "service": labels.get("service") or labels.get("component"),
                "rule": item.get("rule"),
            }
        )
    return sorted(alerts, key=lambda item: str(item.get("timestamp", "")))


def _log_patterns(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "evidence_id": item.get("evidence_id"),
            "pattern": item.get("title"),
            "summary": item.get("summary"),
            "first_seen": item.get("time_range", {}).get("start"),
            "last_seen": item.get("time_range", {}).get("end"),
            "examples": item.get("representative_lines", []),
        }
        for item in selected
        if item.get("type") == "log_template"
    ]


def _configuration_evidence(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in selected:
        if item.get("type") != "configuration":
            continue
        config = item.get("configuration", {})
        result.append(
            {
                "evidence_id": item.get("evidence_id"),
                "kind": config.get("kind"),
                "name": config.get("name"),
                "namespace": config.get("namespace"),
                "content_hash": config.get("content_hash"),
                "resource_version": config.get("resource_version"),
                "keys": config.get("keys", []),
                "images": config.get("images", []),
                "configmap_refs": config.get("configmap_refs", []),
                "ready": config.get("ready"),
                "summary": item.get("summary"),
            }
        )
    return result


def build_incident_report(
    capsule: dict[str, Any],
    record: dict[str, Any],
    source_metrics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
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
        display_value = metric.get("incident_peak")
        display_baseline = metric.get("baseline_median")
        meaning, baseline_text = _impact_context(name, display_value, display_baseline)
        if name == "pod_container_restarts_total":
            increase = _window_counter_increase(source_metrics, name)
            if increase is not None:
                display_value = increase
                display_baseline = 0.0
                baseline_text = f"Captured window: +{increase:.0f} restart{'s' if increase != 1 else ''}"
        impact.append(
            {
                "label": _metric_label(name),
                "value": _display_value(name, display_value),
                "baseline": _display_value(name, display_baseline),
                "meaning": meaning,
                "baseline_text": baseline_text,
                "change_percent": round(float(metric.get("percentage_change", 0)), 1),
                "timestamp": metric.get("peak_timestamp"),
                "component": metric.get("labels", {}).get("component") or metric.get("labels", {}).get("pod"),
                "evidence_id": evidence.get("evidence_id"),
            }
        )
        if len(impact) == 6:
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
    actions.sort(key=lambda item: 0 if item["priority"] == "urgent" else 1)

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
    metric_names = {str(item.get("metric", "")).lower() for item in source_metrics or []}
    missing_pm = [label for label, terms in (("CPU", ("cpu",)), ("memory", ("memory", "mem_"))) if not any(term in name for name in metric_names for term in terms)]
    return {
        "report_version": "1.3",
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
        "fault_alerts": _fault_alerts(capsule),
        "pm_signals": _pm_signals(source_metrics, anomalies),
        "pm_coverage_note": (
            f"No {', '.join(missing_pm)} series were received for this incident, so FCAPSule cannot rule out host-level pressure."
            if missing_pm
            else None
        ),
        "log_patterns": _log_patterns(selected),
        "configuration_evidence": _configuration_evidence(selected),
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
