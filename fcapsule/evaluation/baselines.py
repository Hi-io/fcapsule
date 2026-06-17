"""Simple comparison baselines required by the P1 guide."""

from __future__ import annotations

from typing import Any

from fcapsule.models.schemas import CaseBundle, parse_timestamp


def build_baselines(bundle: CaseBundle, sample_size: int = 20) -> dict[str, Any]:
    fields = bundle.metadata.get("fields", {})
    message_field = fields.get("log_message_field", "message")
    level_field = fields.get("log_level_field", "level")
    time_field = fields.get("log_time_field", "@timestamp")
    terms = {
        "error", "warn", "warning", str(bundle.metadata["service"]).lower(),
        *(str(alert["alertname"]).lower() for alert in bundle.alerts),
    }
    keyword_matches = []
    for index, log in enumerate(bundle.logs):
        haystack = f"{log.get(level_field, '')} {log.get(message_field, '')}".lower()
        if any(term in haystack for term in terms):
            keyword_matches.append(index)

    ordered = sorted(
        range(len(bundle.logs)),
        key=lambda index: abs((parse_timestamp(bundle.logs[index][time_field], "log.timestamp") - bundle.alert_time).total_seconds()),
    )
    time_matches = ordered[:sample_size]
    return {
        "raw_telemetry": {
            "description": "All source logs, metric series, and alerts without reduction.",
            "selected_log_lines": len(bundle.logs),
        },
        "keyword_filter": {
            "description": "Logs containing ERROR/WARN, service, or alert-name terms.",
            "selected_log_lines": len(keyword_matches),
            "selected_log_indexes": keyword_matches,
        },
        "time_window_sample": {
            "description": f"The {sample_size} logs nearest to the first alert timestamp.",
            "selected_log_lines": len(time_matches),
            "selected_log_indexes": time_matches,
        },
        "single_llm": {
            "description": "Not executed in offline P1; represented as an experimental protocol in EVALUATION_PLAN.md.",
            "status": "not_run",
        },
    }
