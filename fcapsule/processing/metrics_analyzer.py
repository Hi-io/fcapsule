"""Explainable metric anomaly analysis for bounded incident windows."""

from __future__ import annotations

import math
import statistics
from typing import Any

from fcapsule.models.schemas import CaseBundle, parse_timestamp


def _median_absolute_deviation(values: list[float]) -> float:
    median = statistics.median(values)
    return statistics.median(abs(value - median) for value in values)


def analyze_metrics(bundle: CaseBundle) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, series in enumerate(bundle.metrics, start=1):
        points = sorted(
            ((parse_timestamp(point[0], "metric.timestamp"), float(point[1])) for point in series["values"]),
            key=lambda item: item[0],
        )
        is_counter = str(series["metric"]).endswith("_total")
        analyzed_points = points
        if is_counter:
            analyzed_points = [
                (points[position][0], max(0.0, points[position][1] - points[position - 1][1]))
                for position in range(1, len(points))
            ]
        baseline_points = [point for point in analyzed_points if point[0] < bundle.alert_time]
        incident_points = [point for point in analyzed_points if point[0] >= bundle.alert_time]
        if not baseline_points or not incident_points:
            if len(analyzed_points) == 1:
                incident_points = analyzed_points
                baseline_points = [(analyzed_points[0][0], 0.0)] if is_counter else analyzed_points
            else:
                midpoint = min(max(1, len(analyzed_points) // 2), len(analyzed_points) - 1)
                baseline_points = analyzed_points[:midpoint]
                incident_points = analyzed_points[midpoint:]

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
            (incident_peak - baseline_median) / abs(baseline_median) * 100 if baseline_median != 0 else math.copysign(1000.0, incident_peak)
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
