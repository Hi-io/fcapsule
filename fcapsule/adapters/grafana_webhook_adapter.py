"""Normalize the default Grafana Alerting webhook payload into fault events."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fcapsule.models.schemas import parse_timestamp


def _strings(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 64:
        raise ValueError("Grafana labels and annotations must be bounded objects")
    return {str(key)[:128]: str(item)[:2048] for key, item in value.items()}


def normalize_notification(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any] | None]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("alerts"), list):
        raise ValueError("Expected a Grafana webhook notification with alerts")
    if len(payload["alerts"]) > 100:
        raise ValueError("Grafana notification exceeds 100 alerts")
    common = _strings(payload.get("commonLabels") or {})
    result: list[tuple[str, dict[str, Any] | None]] = []
    for item in payload["alerts"]:
        if not isinstance(item, dict):
            raise ValueError("Grafana alert must be an object")
        labels = {**common, **_strings(item.get("labels") or {})}
        annotations = _strings(item.get("annotations") or {})
        status = str(item.get("status") or payload.get("status") or "").lower()
        if status not in {"firing", "resolved"}:
            raise ValueError("Grafana alert status must be firing or resolved")
        starts_at = str(item.get("startsAt") or "")
        parse_timestamp(starts_at, "grafana.startsAt")
        fingerprint = str(item.get("fingerprint") or "")[:128]
        if not fingerprint:
            fingerprint = hashlib.sha256(json.dumps([labels, starts_at], sort_keys=True).encode()).hexdigest()
        key = f"{fingerprint}:{starts_at}"
        if status == "resolved":
            result.append((key, None))
            continue
        result.append((key, {
            "alertname": labels.get("alertname") or str(item.get("ruleName") or "GrafanaAlert")[:256],
            "status": "firing",
            "severity": labels.get("severity", "warning"),
            "startsAt": starts_at,
            "endsAt": None,
            "labels": labels,
            "annotations": annotations,
            "source": "grafana_webhook",
            "fingerprint": fingerprint,
        }))
    return result
