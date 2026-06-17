"""Align case identity across alert, log, and metric telemetry."""

from __future__ import annotations

from collections import Counter
from typing import Any

from fcapsule.models.schemas import CaseBundle

ENTITY_KEYS = ("service", "namespace", "cluster", "pod", "cncc_uuid")


def _dominant(values: list[str]) -> str | None:
    return Counter(values).most_common(1)[0][0] if values else None


def resolve_entities(bundle: CaseBundle) -> dict[str, Any]:
    expected = {key: bundle.metadata.get(key) for key in ENTITY_KEYS}
    observed: dict[str, list[str]] = {key: [] for key in ENTITY_KEYS}

    for alert in bundle.alerts:
        labels = alert.get("labels", {})
        for key in ENTITY_KEYS:
            if labels.get(key):
                observed[key].append(str(labels[key]))
    for log in bundle.logs:
        for key in ENTITY_KEYS:
            if log.get(key):
                observed[key].append(str(log[key]))
    for series in bundle.metrics:
        labels = series.get("labels", {})
        for key in ENTITY_KEYS:
            if labels.get(key):
                observed[key].append(str(labels[key]))

    warnings: list[str] = []
    matched_labels: dict[str, str] = {}
    for key in ENTITY_KEYS:
        dominant = _dominant(observed[key])
        target = expected.get(key)
        if target:
            matched_labels[key] = str(target)
        elif dominant:
            matched_labels[key] = dominant
        if target and dominant and str(target) != dominant:
            warnings.append(f"{key} mismatch: metadata={target!r}, telemetry={dominant!r}")

    return {
        "primary_entity": str(bundle.metadata["service"]),
        "matched_labels": matched_labels,
        "coverage": {
            "logs_found": bool(bundle.logs),
            "metrics_found": bool(bundle.metrics),
            "alert_found": bool(bundle.alerts),
        },
        "observed_values": {key: sorted(set(values)) for key, values in observed.items() if values},
        "warnings": [*bundle.warnings, *warnings],
    }
