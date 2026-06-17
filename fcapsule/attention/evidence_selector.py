"""Select capsule evidence while retaining domain diversity."""

from __future__ import annotations

from collections import Counter
from typing import Any

from fcapsule.config import MAX_SELECTED_EVIDENCE


def select_evidence(evidence: list[dict[str, Any]], limit: int = MAX_SELECTED_EVIDENCE) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for evidence_type in ("alert", "log_template", "metric_anomaly"):
        match = next((item for item in evidence if item["type"] == evidence_type), None)
        if match and match not in selected:
            selected.append(match)
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
