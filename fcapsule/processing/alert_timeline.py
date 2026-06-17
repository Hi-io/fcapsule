"""Normalize alert events into a chronological timeline."""

from __future__ import annotations

from typing import Any

from fcapsule.models.schemas import CaseBundle, parse_timestamp


def build_alert_timeline(bundle: CaseBundle) -> list[dict[str, Any]]:
    timeline = []
    for index, alert in enumerate(bundle.alerts, start=1):
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        timeline.append(
            {
                "timeline_id": f"alert_event_{index:03d}",
                "timestamp": alert["startsAt"],
                "type": "alert",
                "severity": str(alert.get("severity", "unknown")).lower(),
                "title": str(alert["alertname"]),
                "description": annotations.get("description") or annotations.get("summary") or "Alert fired",
                "entities": [str(labels[key]) for key in ("service", "namespace", "cluster", "pod", "cncc_uuid") if labels.get(key)],
            }
        )
    return sorted(timeline, key=lambda item: parse_timestamp(item["timestamp"], "timeline.timestamp"))
