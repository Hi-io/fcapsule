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
        baseline = [value for timestamp, value in points if timestamp < bundle.alert_time]
        incident = [value for timestamp, value in points if timestamp >= bundle.alert_time]
        if not baseline or not incident:
            midpoint = max(1, len(points) // 2)
            baseline = [value for _, value in points[:midpoint]]
            incident = [value for _, value in points[midpoint:]]

        baseline_median = statistics.median(baseline)
        incident_peak = max(incident, key=lambda value: abs(value - baseline_median))
        mad = _median_absolute_deviation(baseline)
        scale = mad * 1.4826
        if scale == 0:
            scale = max(abs(baseline_median) * 0.05, 1e-9)
        robust_z = (incident_peak - baseline_median) / scale
        percentage_change = (
            (incident_peak - baseline_median) / abs(baseline_median) * 100 if baseline_median != 0 else math.copysign(1000.0, incident_peak)
        )
        anomaly_score = min(1.0, abs(robust_z) / 6 * 0.65 + min(abs(percentage_change), 200) / 200 * 0.35)
        peak_timestamp = next(timestamp for timestamp, value in points if value == incident_peak)
        labels = {str(key): str(value) for key, value in series.get("labels", {}).items()}
        entity = labels.get("pod") or labels.get("service") or labels.get("namespace") or "unknown"
        direction = "increased" if percentage_change >= 0 else "decreased"
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
                "reason": f"{series['metric']} {direction} {abs(percentage_change):.1f}% near the alert compared with the baseline median.",
            }
        )
    return sorted(results, key=lambda item: (-item["anomaly_score"], item["metric"]))
