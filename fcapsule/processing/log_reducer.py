"""Drain-inspired masking and exact template grouping."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from fcapsule.config import ALERT_PROXIMITY_WINDOW_SECONDS, REPRESENTATIVE_LINES_PER_TEMPLATE
from fcapsule.models.schemas import CaseBundle, isoformat_utc, parse_timestamp
from fcapsule.processing.anonymizer import anonymize_text, template_for_message, diagnostic_fields

SEVERITY_WEIGHT = {"CRITICAL": 1.0, "FATAL": 1.0, "ERROR": 0.85, "WARN": 0.55, "WARNING": 0.55, "INFO": 0.15, "DEBUG": 0.05}


def _proximity(times: list[datetime], alert_time: datetime) -> float:
    distance = min(abs((value - alert_time).total_seconds()) for value in times)
    return round(max(0.0, 1.0 - distance / ALERT_PROXIMITY_WINDOW_SECONDS), 4)


def reduce_logs(bundle: CaseBundle) -> list[dict[str, Any]]:
    fields = bundle.metadata.get("fields", {})
    time_field = fields.get("log_time_field", "@timestamp")
    message_field = fields.get("log_message_field", "message")
    level_field = fields.get("log_level_field", "level")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for event in bundle.logs:
        grouped[template_for_message(str(event[message_field]))].append(event)

    total = max(1, len(bundle.logs))
    templates: list[dict[str, Any]] = []
    ordered = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
    for index, (template, events) in enumerate(ordered, start=1):
        times = sorted(parse_timestamp(event[time_field], f"log.{time_field}") for event in events)
        levels = Counter(str(event.get(level_field, "UNKNOWN")).upper() for event in events)
        max_severity = max((SEVERITY_WEIGHT.get(level, 0.1) for level in levels), default=0.1)
        representatives = []
        ordered_events = sorted(events, key=lambda event: str(event[time_field]))
        sample = [ordered_events[0]]
        nearest = min(ordered_events, key=lambda event: abs((parse_timestamp(event[time_field], "log.time") - bundle.alert_time).total_seconds()))
        if nearest is not sample[0]:
            sample.append(nearest)
        elif ordered_events[-1] is not sample[0]:
            sample.append(ordered_events[-1])
        for event in sample[:REPRESENTATIVE_LINES_PER_TEMPLATE]:
            line = f"{event[time_field]} {anonymize_text(str(event[message_field]))}"
            representatives.append(line)
        count = len(events)
        rarity = 1.0 if count <= 2 else max(0.0, 1.0 - count / total)
        templates.append(
            {
                "template_id": f"log_template_{index:03d}",
                "template": template,
                "diagnostic_fields": diagnostic_fields(str(events[0][message_field])),
                "count": count,
                "volume_percentage": round(count / total * 100, 3),
                "levels": dict(levels),
                "first_seen": isoformat_utc(times[0]),
                "last_seen": isoformat_utc(times[-1]),
                "representative_lines": representatives,
                "temporal_proximity": _proximity(times, bundle.alert_time),
                "severity_score": max_severity,
                "rarity_score": round(rarity, 4),
                "linked_entities": sorted(
                    {
                        str(event[key])
                        for event in events
                        for key in ("service", "namespace", "cluster", "pod", "cncc_uuid")
                        if event.get(key)
                    }
                ),
            }
        )
    return templates
