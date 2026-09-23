"""Explainable metric anomaly analysis for bounded incident windows."""

from __future__ import annotations

import math
import operator
import statistics
from typing import Any

from fcapsule.models.schemas import CaseBundle, parse_timestamp

ALERT_METRIC_FIELDS = (
    "signal_origin", "series_id", "expression", "underlying_expression", "rule", "threshold", "operator",
    "unit", "labels", "source", "time_range", "step_seconds", "scope", "alertname", "alert_timestamp", "condition",
)
_COMPARE = {"==": operator.eq, "!=": operator.ne, ">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


def _iso(timestamp: Any) -> str:
    return timestamp.isoformat().replace("+00:00", "Z")


def _analyze_alert_metric(bundle: CaseBundle, series: dict[str, Any], points: list, index: int) -> dict[str, Any]:
    threshold = float(series["threshold"])
    comparison = _COMPARE[series["operator"]]
    matches = [point for point in points if comparison(point[1], threshold)]
    alert_time = parse_timestamp(series.get("alert_timestamp") or _iso(bundle.alert_time), "metric.alert_timestamp")
    baseline = [point for point in points if point[0] < alert_time]
    incident = [point for point in points if point[0] >= alert_time]
    # This is the actual sampled expression value, even when its metric name is a counter.
    candidates = incident or points
    if series["operator"] in {"<", "<="}:
        peak = min(candidates, key=lambda point: point[1])
    elif series["operator"] in {">", ">="}:
        peak = max(candidates, key=lambda point: point[1])
    elif series["operator"] == "==":
        peak = next((point for point in reversed(candidates) if comparison(point[1], threshold)), candidates[-1])
    else:
        peak = max(candidates, key=lambda point: abs(point[1] - threshold))
    condition = {
        "observed_samples": len(points), "matching_samples": len(matches),
        "missing_samples": len(series["values"]) - len(points),
        "incident_observed_samples": len(incident),
        "incident_matching_samples": sum(comparison(value, threshold) for _, value in incident),
        "min": min(value for _, value in points), "max": max(value for _, value in points),
        "latest": {"timestamp": _iso(points[-1][0]), "value": points[-1][1]},
        "first_match": _iso(matches[0][0]) if matches else None,
        "last_match": _iso(matches[-1][0]) if matches else None,
        "evaluation": "sampled_comparison_only",
        "limitation": "Sampled comparisons do not prove continuous truth, rule for-duration, or causation; gaps are unknown.",
    }
    labels = dict(series.get("labels", {}))
    reason = (
        f"Alert {series.get('alertname', '')}: {series['expression']} {series['operator']} {threshold:g}. "
        f"Observed values {condition['min']:g} to {condition['max']:g}; latest {points[-1][1]:g} at {_iso(points[-1][0])}. "
        f"Comparison matched {len(matches)}/{len(points)} sampled values "
        f"({condition['incident_matching_samples']}/{len(incident)} at or after alert start); "
        f"{condition['missing_samples']} missing samples. {condition['limitation']}"
    )
    return {
        **{key: series[key] for key in ALERT_METRIC_FIELDS if key in series},
        "metric_id": f"metric_{index:03d}", "metric": series["metric"],
        "entity": labels.get("pod") or labels.get("service") or labels.get("namespace") or "unknown",
        "labels": labels, "baseline_median": statistics.median(value for _, value in baseline) if baseline else None,
        "baseline_start": _iso(baseline[0][0]) if baseline else None,
        "baseline_end": _iso(baseline[-1][0]) if baseline else None,
        "baseline_basis": "pre_alert" if baseline else "unavailable",
        "incident_peak": peak[1], "peak_timestamp": _iso(peak[0]),
        "robust_z_score": 0.0, "percentage_change": 0.0,
        "anomaly_score": 1.0 if matches else 0.0, "analysis_mode": "alert_condition",
        "condition": condition, "reason": reason,
    }


def _median_absolute_deviation(values: list[float]) -> float:
    median = statistics.median(values)
    return statistics.median(abs(value - median) for value in values)


def analyze_metrics(bundle: CaseBundle) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, series in enumerate(bundle.metrics, start=1):
        points = sorted(
            ((parse_timestamp(point[0], "metric.timestamp"), float(point[1])) for point in series["values"]
             if point[1] is not None and math.isfinite(float(point[1]))),
            key=lambda item: item[0],
        )
        if not points:
            continue
        if series.get("signal_origin") == "alert_rule":
            results.append(_analyze_alert_metric(bundle, series, points, index))
            continue
        is_counter = str(series["metric"]).endswith("_total")
        analyzed_points = points
        if is_counter:
            analyzed_points = [
                (points[position][0], max(0.0, points[position][1] - points[position - 1][1]))
                for position in range(1, len(points))
            ]
        baseline_points = [point for point in analyzed_points if point[0] < bundle.alert_time]
        incident_points = [point for point in analyzed_points if point[0] >= bundle.alert_time]
        baseline_basis = "pre_alert"
        if not baseline_points or not incident_points:
            if len(analyzed_points) == 1:
                incident_points = analyzed_points
                baseline_points = [(analyzed_points[0][0], 0.0)] if is_counter else analyzed_points
                baseline_basis = "single_sample"
            else:
                midpoint = min(max(1, len(analyzed_points) // 2), len(analyzed_points) - 1)
                baseline_points = analyzed_points[:midpoint]
                incident_points = analyzed_points[midpoint:]
                baseline_basis = "split_window"

        baseline = [value for _, value in baseline_points]
        incident = [value for _, value in incident_points]

        baseline_median = statistics.median(baseline)
        peak_timestamp, incident_peak = max(
            incident_points,
            key=lambda point: abs(point[1] - baseline_median),
        )
        mad = _median_absolute_deviation(baseline)
        scale = mad * 1.4826
        if scale == 0:
            scale = max(abs(baseline_median) * 0.05, 1e-9)
        robust_z = (incident_peak - baseline_median) / scale
        percentage_change = (
            (incident_peak - baseline_median) / abs(baseline_median) * 100 if baseline_median != 0
            else (math.copysign(1000.0, incident_peak) if incident_peak != 0 else 0.0)
        )
        anomaly_score = min(1.0, abs(robust_z) / 6 * 0.65 + min(abs(percentage_change), 200) / 200 * 0.35)
        labels = {str(key): str(value) for key, value in series.get("labels", {}).items()}
        entity = labels.get("pod") or labels.get("service") or labels.get("namespace") or "unknown"
        direction = "increased" if percentage_change >= 0 else "decreased"
        measurement = "per-sample increase" if is_counter else "value"
        results.append(
            {
                "metric_id": f"metric_{index:03d}",
                "metric": series["metric"],
                "entity": entity,
                "labels": labels,
                "baseline_median": baseline_median,
                "baseline_start": baseline_points[0][0].isoformat().replace("+00:00", "Z"),
                "baseline_end": baseline_points[-1][0].isoformat().replace("+00:00", "Z"),
                "baseline_basis": baseline_basis,
                "incident_peak": incident_peak,
                "peak_timestamp": peak_timestamp.isoformat().replace("+00:00", "Z"),
                "robust_z_score": round(robust_z, 4),
                "percentage_change": round(percentage_change, 3),
                "anomaly_score": round(anomaly_score, 4),
                "analysis_mode": "counter_delta" if is_counter else "gauge",
                "reason": f"{series['metric']} {measurement} {direction} {abs(percentage_change):.1f}% near the alert compared with the baseline median.",
            }
        )
    return sorted(results, key=lambda item: (-item["anomaly_score"], item["metric"]))
