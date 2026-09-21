"""Deterministic prompt compaction and token-budget helpers.

The retained investigation state is intentionally richer than the prompt sent to a
model. These helpers keep evidence identifiers and diagnostic facts while avoiding
the repeated transmission of representative log lines and full tool payloads.
"""

from __future__ import annotations

import json
import math
from typing import Any


def estimate_tokens(value: Any) -> int:
    """Use a deliberately conservative character estimate before a provider replies."""

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    # Compact JSON and identifier-heavy telemetry usually tokenize more densely than
    # prose. Reserve against 3.2 characters per token so a missing provider usage
    # field cannot turn a nominal budget into an optimistic estimate.
    return max(1, math.ceil(len(text) / 3.2))


def _short(value: Any, limit: int = 320) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "…"


def _bounded(value: Any, depth: int = 0, max_depth: int = 4, max_items: int = 6) -> Any:
    """Keep structured facts readable without replaying complete source responses."""

    if isinstance(value, str):
        return _short(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if depth >= max_depth:
        return _short(json.dumps(value, ensure_ascii=True, default=str), 420)
    if isinstance(value, list):
        kept = [_bounded(item, depth + 1, max_depth, max_items) for item in value[:max_items]]
        if len(value) > max_items:
            kept.append({"omitted_items": len(value) - max_items})
        return kept
    if isinstance(value, dict):
        preferred = (
            "source", "scope", "pod", "service", "namespace", "window", "observed_at", "latest_alert_at",
            "status", "health", "error", "limitation", "unavailable_sources", "sampling", "method",
            "observations", "patterns", "matching_patterns", "scanned_lines", "matching_candidates",
            "declared_dependencies", "metric_semantics", "comparability", "affected", "reference",
            "configuration", "labels", "fields", "count", "first_seen", "last_seen", "examples",
        )
        keys = [key for key in preferred if key in value]
        keys.extend(key for key in value if key not in keys)
        kept: dict[str, Any] = {}
        for key in keys[:max_items * 2]:
            kept[str(key)] = _bounded(value[key], depth + 1, max_depth, max_items)
        if len(value) > len(kept):
            kept["omitted_fields"] = len(value) - len(kept)
        return kept
    return _short(value)


def _evidence_item(item: dict[str, Any]) -> dict[str, Any]:
    examples = item.get("examples") or []
    return {
        "id": item.get("id"),
        "domain": item.get("domain"),
        "title": _short(item.get("title"), 180),
        "summary": _short(item.get("summary"), 360),
        "time_range": _bounded(item.get("time_range"), max_items=3),
        "diagnostic_example": _bounded(examples[:1], max_items=1) if examples else None,
        "configuration": _bounded(item.get("configuration"), max_items=3) if item.get("configuration") else None,
    }


def _check_item(check: dict[str, Any], latest: bool) -> dict[str, Any]:
    result = _bounded(check.get("result"), max_items=8 if latest else 4)
    return {
        "id": check.get("id"),
        "tool": check.get("tool"),
        "status": check.get("status"),
        "question": _short(check.get("question"), 180),
        "distinguishes": _short(check.get("distinguishes"), 220),
        "observation": result,
    }


def compact_for_model(
    context: dict[str, Any],
    checks: list[dict[str, Any]],
    *,
    max_prompt_tokens: int = 5200,
) -> tuple[dict[str, Any], list[str]]:
    """Return a compact prompt context and the evidence IDs visible to the model.

    The full context/checks remain on disk. The model receives a bounded fact ledger
    plus the newest observation; it can still cite every record actually shown.
    """

    evidence = [_evidence_item(item) for item in (context.get("evidence") or [])[:28]]
    visible_ids = [str(item["id"]) for item in evidence if item.get("id")]
    retained_checks = checks[-4:]
    recent = [_check_item(item, index == len(retained_checks) - 1) for index, item in enumerate(retained_checks)]
    visible_ids.extend(str(item["id"]) for item in recent if item.get("id") and item.get("status") == "completed")
    alerts = []
    for item in (context.get("alerts") or [])[:12]:
        alerts.append({
            "incident_id": item.get("incident_id"),
            "alertname": item.get("alertname") or item.get("name"),
            "severity": item.get("severity"),
            "status": item.get("current_status") or item.get("status"),
            "startsAt": item.get("startsAt") or item.get("started_at"),
            "endsAt": item.get("ended_at") or item.get("endsAt"),
            "labels": _bounded(item.get("labels"), max_items=6),
            "annotations": _bounded(item.get("annotations"), max_items=4),
        })
    payload: dict[str, Any] = {
        "episode_id": context.get("episode_id"),
        "episode_lifecycle": _bounded(context.get("episode_lifecycle"), max_items=6),
        "live_capture": bool(context.get("live_capture")),
        "alerts": alerts,
        "impact": _bounded((context.get("impact") or [])[:8], max_items=5),
        "evidence": evidence,
        "prior_checks": recent,
        "historical_candidates": _bounded((context.get("historical_candidates") or [])[:3], max_items=4),
        "constraints": "Evidence is bounded and may be incomplete. Current state is not incident-time state. Time correlation is not causation.",
    }

    def refresh_visible_ids() -> list[str]:
        ids = [str(item["id"]) for item in payload["evidence"] if item.get("id")]
        ids.extend(str(item["id"]) for item in payload["prior_checks"]
                   if item.get("id") and item.get("status") == "completed")
        return list(dict.fromkeys(ids))

    # Tighten in a deterministic order until the payload meets its intended budget.
    while estimate_tokens(payload) > max_prompt_tokens:
        if len(payload["evidence"]) > 3:
            payload["evidence"].pop()
            visible_ids = refresh_visible_ids()
        elif len(payload["prior_checks"]) > 1:
            payload["prior_checks"].pop(0)
            visible_ids = refresh_visible_ids()
        elif payload["impact"]:
            payload["impact"] = []
        elif any(item.get("diagnostic_example") is not None for item in payload["evidence"]):
            for item in payload["evidence"]:
                item["diagnostic_example"] = None
        elif any(item.get("configuration") is not None for item in payload["evidence"]):
            for item in payload["evidence"]:
                item["configuration"] = None
        elif any(len(str(item.get("summary", ""))) > 140 for item in payload["evidence"]):
            for item in payload["evidence"]:
                item["summary"] = _short(item.get("summary"), 140)
                item["title"] = _short(item.get("title"), 100)
        elif any(
            item.get("observation")
            and (not isinstance(item.get("observation"), str) or len(item["observation"]) > 180)
            for item in payload["prior_checks"]
        ):
            for item in payload["prior_checks"]:
                item["observation"] = _short(json.dumps(item.get("observation"), ensure_ascii=True), 180)
        elif len(payload["evidence"]) > 1:
            payload["evidence"].pop()
            visible_ids = refresh_visible_ids()
        elif payload["alerts"] and len(payload["alerts"]) > 1:
            payload["alerts"].pop()
        else:
            break
    return payload, list(dict.fromkeys(visible_ids))
