"""Select capsule evidence while retaining domain diversity."""

from __future__ import annotations

from collections import Counter
from typing import Any

from fcapsule.config import MAX_SELECTED_EVIDENCE


def _representatives(items: list[dict[str, Any]], groups: tuple[tuple[str, ...], ...]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for terms in groups:
        match = next(
            (
                item
                for item in items
                if any(term in (str(item.get("title", "")) + " " + str(item.get("summary", ""))).lower() for term in terms)
            ),
            None,
        )
        if match and match not in result:
            result.append(match)
    return result


def select_evidence(evidence: list[dict[str, Any]], limit: int = MAX_SELECTED_EVIDENCE) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    type_limits = {"alert": 4, "log_template": 6, "metric_anomaly": 10}
    for evidence_type, type_limit in type_limits.items():
        matches = [item for item in evidence if item["type"] == evidence_type and item["score"] >= 0.25]
        required: list[dict[str, Any]] = []
        if evidence_type == "log_template":
            required = _representatives(
                matches,
                (
                    ("error", "failed", "unavailable", "aborted"),
                    ("retry", "attempt", "breaker"),
                    ("pool", "exhaust"),
                    ("lock", "deadline", "timeout"),
                ),
            )
        elif evidence_type == "metric_anomaly":
            required = _representatives(
                matches,
                (
                    ("error", "failure"),
                    ("latency", "duration"),
                    ("retry", "attempt"),
                    ("pool", "exhaust", "saturation"),
                    ("log_indexing", "scrape", "telemetry"),
                ),
            )
        ordered = required + [item for item in matches if item not in required]
        for item in ordered[:type_limit]:
            if item not in selected and len(selected) < limit:
                selected.append(item)
    for item in evidence:
        if item not in selected and len(selected) < limit and item["score"] >= 0.25:
            selected.append(item)
    selected.sort(key=lambda item: (-item["score"], item["evidence_id"]))
    discarded = [item for item in evidence if item not in selected]
    summary = {
        "candidate_count": len(evidence),
        "selected_count": len(selected),
        "discarded_count": len(discarded),
        "selected_by_type": dict(Counter(item["type"] for item in selected)),
        "discarded_by_type": dict(Counter(item["type"] for item in discarded)),
        "discard_reason": "Below the evidence threshold or outside the capsule size limit.",
    }
    return selected, summary
