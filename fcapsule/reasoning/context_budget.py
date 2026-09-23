"""Deterministic prompt compaction and token-budget helpers.

The retained investigation state is intentionally richer than the prompt sent to a
model. These helpers keep evidence identifiers and diagnostic facts while avoiding
the repeated transmission of representative log lines and full tool payloads.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from fcapsule.processing.anonymizer import anonymize_text


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


def _visual_observation(observations: list[Any], budget: int = 1000) -> dict[str, Any]:
    """Share space between complete extracted facts, independent of their meaning."""

    rows = []
    for index, item in enumerate(observations[:16]):
        if not isinstance(item, dict) or not isinstance(item.get("fact"), str):
            continue
        fact = anonymize_text(item["fact"]).strip()
        if fact:
            rows.append((index, {"fact": fact, "confidence": item.get("confidence")
                                if item.get("confidence") in {"high", "medium", "low"} else "unspecified"}))
    # Short complete facts release their unused share to longer ones. Never emit
    # a prefix as a fact or promote a fact because it resembles a known failure.
    kept = []
    remaining = len(rows)
    for index, row in sorted(rows, key=lambda pair: len(json.dumps(pair[1], ensure_ascii=True))):
        size = len(json.dumps(row, ensure_ascii=True))
        if size <= budget // max(1, remaining):
            kept.append((index, row))
            budget -= size
        remaining -= 1
    return {"facts": [row for _, row in sorted(kept)],
            "omitted_facts": len(observations) - len(kept)}


def _minimal_visual_observation(value: dict[str, Any]) -> dict[str, Any]:
    reduced = _visual_observation(value["facts"], budget=600)
    reduced["omitted_facts"] += value.get("omitted_facts", 0)
    return reduced


def _alert_time(item: dict[str, Any]) -> datetime:
    try:
        value = datetime.fromisoformat(str(item.get("startsAt") or item.get("started_at")).replace("Z", "+00:00"))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _evidence_item(item: dict[str, Any]) -> dict[str, Any]:
    examples = item.get("examples") or []
    values = {
        "id": item.get("id"),
        "domain": item.get("domain"),
        "title": _short(item.get("title"), 180),
        "summary": _short(item.get("summary"), 360),
        "time_range": _bounded(item.get("time_range"), max_items=3),
        "diagnostic_example": _bounded(examples[:1], max_items=1) if examples else None,
        "configuration": _bounded(item.get("configuration"), max_items=3) if item.get("configuration") else None,
        "operator_context": _bounded(item.get("operator_context"), max_items=3) if item.get("operator_context") else None,
        "limitation": _short(item.get("limitation"), 180),
        "revision_priority": True if item.get("revision_priority") else None,
        "metric_observation": compact_metric_observation(item.get("metric_observation")),
    }
    if item.get("domain") == "image_evidence" and item.get("visual_observations"):
        values["visual_observation"] = _visual_observation(item["visual_observations"])
        values.pop("summary", None)
        values.pop("diagnostic_example", None)
    # Empty keys cost meaningful tokens across several calls without helping a
    # model distinguish hypotheses. The full retained record stays on disk.
    return {key: value for key, value in values.items() if value not in (None, "", [], {})}


def compact_metric_observation(value: Any, *, minimal: bool = False) -> dict[str, Any]:
    """Retain sampled alert-rule facts, never raw points or arbitrary metadata."""

    if not isinstance(value, dict):
        return {}

    def fields(source, strings=(), numbers=()):
        if not isinstance(source, dict):
            return {}
        kept = {key: anonymize_text(source[key]).strip()[:limit] for key, limit in strings
                if isinstance(source.get(key), str) and source[key].strip()}
        kept.update({key: source[key] for key in numbers
                     if type(source.get(key)) in (int, float) and -1e308 <= source[key] <= 1e308})
        return kept

    result = fields(value, (("metric", 120), ("operator", 4), ("unit", 48)), ("threshold",))
    if not result.get("metric"):
        return {}
    condition = fields(value.get("condition"), numbers=(
        "observed_samples", "matching_samples", "missing_samples", "incident_observed_samples",
        "incident_matching_samples", "min", "max",
    ))
    raw_condition = value.get("condition") if isinstance(value.get("condition"), dict) else {}
    latest = fields(raw_condition.get("latest"), (("timestamp", 40),), ("value",))
    if latest:
        condition["latest"] = latest
    expression = value.get("expression")
    if isinstance(expression, str) and expression.strip():
        result["expression"] = _short(anonymize_text(expression), 180 if minimal else 300)
    for key, names in (
        ("labels", tuple((name, 253) for name in (("namespace", "pod") if minimal else
                                                 ("namespace", "pod", "container", "service", "job")))),
        ("rule", (("name", 120), ("duration", 40), ("keep_firing_for", 40))),
        ("time_range", (("start", 40), ("end", 40))),
    ):
        retained = fields(value.get(key), names, ("duration", "keep_firing_for") if key == "rule" else ())
        if retained:
            result[key] = retained
    if not minimal:
        condition.update(fields(raw_condition, (("first_match", 40), ("last_match", 40))))
        result.update(fields(value, numbers=("step_seconds",)))
        source = fields(value.get("source"), (("adapter", 40), ("endpoint", 80), ("captured_at", 40), ("capture_mode", 40)))
        if source:
            result["source"] = source
    if condition:
        condition["limitation"] = "Sampled comparison only; missing samples are unknown, not proof of continuous rule duration or cause."
        result["condition"] = condition
    return result


def _scope(value: Any) -> dict[str, Any]:
    """Whitelist retained identity, without copying arbitrary resource metadata."""

    if not isinstance(value, dict):
        return {}

    def fields(source, keys, limit):
        if not isinstance(source, dict):
            return {}
        return {key: anonymize_text(source[key]).strip()[:limit] for key in keys
                if isinstance(source.get(key), str) and source[key].strip()}

    result = fields(value, ("pod", "namespace", "service", "cluster"), 253)
    result.update(fields(value, ("alert_started_at",), 40))
    for key, names, limit in (("window", ("start", "end"), 40), ("resource", ("kind", "name"), 253)):
        retained = fields(value.get(key), names, limit)
        if retained:
            result[key] = retained
    return result


def _recurrence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    count = value.get("previous_count")
    if type(count) is not int or count < 0:
        return {}
    return {
        "previous_count": min(count, 9999),
        "count_capped": count > 9999 or value.get("count_capped") is True,
        "limitation": "Same-signature retained candidates only; not evidence of the same cause.",
    }


def _evidence_priority(item: dict[str, Any], priority_ids: set[str]) -> tuple[int, ...]:
    """Retain discriminating source evidence ahead of repetitive telemetry."""

    text = json.dumps(item, ensure_ascii=True, default=str).casefold()
    domain = str(item.get("domain") or "")
    if item.get("revision_addition"):
        # The service orders additions newest first. Preserve that order instead
        # of ranking operator observations by error keywords or modality.
        return (0, 0, 0, 0, 0, 0)
    failure_markers = (
        "critical", "fatal", "error", "exception", "traceback", "panic", "failed",
        "failure", "refused", "timeout", "oom", "crash", "sqlstate", "errno", "exit_code",
    )
    diagnostic_markers = (
        "buffered", "allocated", "memory", "throttl", "pbkdf", "kdf", "rounds",
        "limit", "retry", "rejected", "mismatch", "schema", "version", "endpoint",
        "route", "authorization", "connection", "deadlock", "lock wait", "constraint",
    )
    return (
        1,
        0 if str(item.get("id")) in priority_ids else 1,
        -int(item.get("signal_origin") == "alert_rule" and bool(item.get("metric_observation"))),
        -int(any(marker in text for marker in failure_markers)),
        -int(any(marker in text for marker in diagnostic_markers)),
        -int(domain == "log_template"),
    )


def _pattern_priority(item: dict[str, Any]) -> tuple[int, int, int, int]:
    """Promote failure signatures above frequent healthy heartbeat templates."""

    text = json.dumps(item, ensure_ascii=True, default=str).casefold()
    failure_markers = (
        "critical", "fatal", "error", "exception", "traceback", "panic", "failed",
        "failure", "refused", "timeout", "oom", "crash", "sqlstate", "errno", "exit_code",
    )
    diagnostic_markers = (
        "buffered", "allocated", "memory", "throttl", "pbkdf", "kdf", "rounds",
        "limit", "retry", "rejected", "mismatch", "schema", "version", "endpoint",
        "route", "authorization", "connection", "deadlock", "lock wait", "constraint",
    )
    try:
        count = int(item.get("count", 0))
    except (TypeError, ValueError):
        count = 0
    return (
        int(any(marker in text for marker in failure_markers)),
        int(any(marker in text for marker in diagnostic_markers)),
        int(bool(item.get("fields"))),
        count,
    )


def _log_example(item: dict[str, Any]) -> dict[str, Any] | str | None:
    """Extract the semantic portion of structured logs before applying a character cap."""

    examples = item.get("examples") or []
    example = examples[0] if examples else None
    if not isinstance(example, dict):
        return _short(example, 220) if example else None
    message = str(example.get("message", ""))
    try:
        structured = json.loads(message)
    except (TypeError, ValueError):
        structured = None
    if isinstance(structured, dict):
        values = {key: structured.get(key) for key in (
            "level", "message", "error", "error_type", "reason", "exit_code", "errno",
            "sqlstate", "mysql_error_code", "status_code", "disposition", "payload_encoding",
            "buffered_bytes", "page_bytes", "delivery", "rows", "kdf", "rounds", "mode",
            "timeout_seconds", "expected_schema", "response_schema", "query_revision", "endpoint",
        )}
        return {"timestamp": example.get("timestamp"),
                **{key: _short(value, 180) for key, value in values.items() if value not in (None, "")}}
    return {
        "timestamp": example.get("timestamp"),
        "level": example.get("level"),
        "message": _short(message, 220),
    }


def _log_observation(result: dict[str, Any]) -> dict[str, Any]:
    """Keep a small, failure-first ledger from a bounded log query."""

    patterns = [item for item in result.get("patterns", []) if isinstance(item, dict)]
    patterns.sort(key=_pattern_priority, reverse=True)
    primary = patterns[0] if patterns else {}
    values = {
        "matching_patterns": result.get("matching_patterns"),
        "top_signal": _log_example(primary) if primary else None,
        "first_seen": primary.get("first_seen"),
        "last_seen": primary.get("last_seen"),
        "occurrences": primary.get("count") if primary else None,
        "sampled": True,
    }
    return {key: value for key, value in values.items() if value not in (None, "", [], {})}


def _workload_observation(result: dict[str, Any]) -> dict[str, Any]:
    """Preserve the termination and resource facts needed to avoid re-querying it."""

    workloads = []
    configuration = []
    for item in result.get("observations", []):
        if not isinstance(item, dict):
            continue
        if item.get("kind") == "ConfigMap":
            values = item.get("data") if isinstance(item.get("data"), dict) else {}
            retained = _safe_configuration_values(values)
            if retained:
                configuration.append({
                    "kind": "ConfigMap",
                    "name": item.get("name"),
                    "values": retained,
                })
            continue
        if item.get("kind") != "PodSpec":
            continue
        resource = next((row for row in item.get("resources", []) if isinstance(row, dict)), {})
        state = next((row for row in item.get("container_states", []) if isinstance(row, dict)), {})
        terminated = state.get("last_state", {}).get("terminated", {}) if isinstance(state.get("last_state"), dict) else {}
        workloads.append({
            "ready": item.get("ready"),
            "limits": resource.get("limits"),
            "last_termination": {
                "reason": terminated.get("reason"),
                "exit_code": terminated.get("exitCode"),
                "finished_at": terminated.get("finishedAt"),
            },
            "restart_count": state.get("restart_count"),
        })
    values = {
        "declared_dependencies": [item.get("service") for item in result.get("declared_dependencies", [])
                                  if isinstance(item, dict) and item.get("service")][:4],
        "workloads": workloads,
        "configuration": configuration[:4],
    }
    return {key: value for key, value in values.items() if value not in (None, "", [], {})}


def _safe_configuration_values(values: dict[str, Any]) -> dict[str, str]:
    """Retain operational configuration without sending credentials to a model."""

    sensitive = ("password", "secret", "token", "credential", "private", "certificate", "apikey", "api_key")
    useful = ("url", "host", "port", "timeout", "revision", "schema", "version", "key_id", "key-id",
              "max", "limit", "mode", "service", "feature")
    retained: dict[str, str] = {}
    for key, value in values.items():
        normalized = str(key).casefold()
        if any(marker in normalized for marker in sensitive):
            continue
        text = str(value or "").strip()
        if any(marker in normalized for marker in useful):
            retained[str(key)] = _short(text, 180)
            continue
        # Config files can bundle safe operational settings with unrelated data.
        # Keep only their diagnostic assignment lines, never the entire file.
        lines = [
            line.strip() for line in text.splitlines()
            if "=" in line
            and not any(marker in line.casefold().split("=", 1)[0] for marker in sensitive)
            and any(marker in line.casefold().split("=", 1)[0] for marker in useful)
        ]
        if lines:
            retained[str(key)] = _short("; ".join(lines[:6]), 300)
    return retained


def _discovery_observation(result: dict[str, Any]) -> dict[str, Any]:
    """Keep selectors beside their captured target labels, without diagnosing them."""

    pods = set((result.get("scope") or {}).get("pods") or [])
    targets = [item for key in ("active_targets", "dropped_targets") for item in result.get(key, [])
               if isinstance(item, dict)]
    sensitive = ("password", "secret", "token", "credential", "private", "certificate", "apikey", "api_key", "authorization")

    def labels(value):
        return {str(key): _short(anonymize_text(str(item)), 120) for key, item in value.items()
                if not any(word in str(key).casefold() for word in sensitive)}

    selections = []
    represented_pools = set()
    for selection in result.get("monitor_selection", []):
        monitor = selection.get("monitor") or {}
        kind = monitor.get("kind")
        if kind not in {"ServiceMonitor", "PodMonitor"}:
            continue
        selector = labels(monitor.get("match_labels") or {})
        prefix = "__meta_kubernetes_" + ("service" if kind == "ServiceMonitor" else "pod") + "_label_"
        pool = f"{kind[0].lower() + kind[1:]}/{monitor.get('namespace')}/{monitor.get('name')}/"
        represented_pools.add(pool)
        matched = []
        for target in targets:
            if not str(target.get("scrape_pool") or "").startswith(pool):
                continue
            discovered = target.get("labels") or {}
            observed = {key: discovered[prefix + re.sub(r"[^a-zA-Z0-9_]", "_", key)] for key in selector
                        if prefix + re.sub(r"[^a-zA-Z0-9_]", "_", key) in discovered}
            matched.append({**{key: _short(anonymize_text(str(target[key])), 120) for key in
                              ("pod", "service", "state", "health", "last_error") if target.get(key)},
                            "selector_labels": labels(observed)})
        matched.sort(key=lambda item: (item.get("pod") not in pods, not bool(item.get("last_error")),
                                       not bool(item["selector_labels"])))
        selections.append({
            "kind": kind, "name": _short(anonymize_text(str(monitor.get("name") or "")), 120),
            "match_labels": dict(list(selector.items())[:8]),
            **{key: _bounded(selection[key], max_items=3) for key in ("matched_services", "matched_pods") if key in selection},
            "targets": matched[:2], "target_count": len(matched),
        })
        if kind == "PodMonitor":
            pod_labels = [item for item in result.get("current_pod_labels", []) if isinstance(item, dict)]
            pod_labels.sort(key=lambda item: item.get("pod") not in pods)
            selections[-1]["current_pod_labels"] = [
                {"pod": _short(anonymize_text(str(item.get("pod") or "")), 120),
                 "labels": labels({key: value for key, value in (item.get("labels") or {}).items() if key in selector})}
                for item in pod_labels[:2]]
    selections.sort(key=lambda item: (
        not any(target.get("pod") in pods for target in item["targets"] + item.get("current_pod_labels", [])),
        not any(target.get("selector_labels") for target in item["targets"]),
    ))
    other_targets = [target for target in targets if not any(
        str(target.get("scrape_pool") or "").startswith(pool) for pool in represented_pools)]
    other_targets.sort(key=lambda item: (item.get("pod") not in pods, not bool(item.get("last_error"))))
    return {
        "monitor_selection": selections[:3], "monitor_count": len(selections),
        "other_targets": [{key: _short(anonymize_text(str(target[key])), 120) for key in
                           ("pod", "service", "state", "health", "last_error") if target.get(key)}
                          for target in other_targets[:2]],
        "target_count": len(targets),
        "limitation": "Current bounded discovery, not incident-time state. Missing labels/targets may be omitted or filtered; absence is not proof of a cause.",
    }


def _check_item(check: dict[str, Any], latest: bool) -> dict[str, Any]:
    raw_result = check.get("result") if isinstance(check.get("result"), dict) else {}
    if check.get("tool") == "search_logs":
        result = _log_observation(raw_result)
    elif check.get("tool") == "workload_state":
        result = _workload_observation(raw_result)
    elif check.get("tool") == "scrape_discovery" and check.get("status") == "completed":
        result = _discovery_observation(raw_result)
    else:
        result = _bounded(raw_result, max_items=8 if latest else 4)
    return {
        "id": check.get("id"),
        "tool": check.get("tool"),
        "status": check.get("status"),
        "required_observation": bool(check.get("required_observation")),
        "question": _short(check.get("question"), 180),
        "distinguishes": _short(check.get("distinguishes"), 220),
        "observation": result,
    }


def _minimal_check_observation(check: dict[str, Any]) -> Any:
    observation = check.get("observation")
    if isinstance(observation, str) and len(observation) <= 180:
        return observation
    if check.get("tool") == "search_logs" and isinstance(observation, dict):
        return {key: observation[key] for key in
                ("top_signal", "first_seen", "last_seen", "occurrences", "sampled") if key in observation}
    if check.get("tool") == "scrape_discovery" and isinstance(observation, dict) and "monitor_selection" in observation:
        if observation.get("compacted_discovery"):
            return observation
        selections = [{**item, "targets": item.get("targets", [])[:1]}
                      for item in observation["monitor_selection"][:1]]
        return {**observation, "compacted_discovery": True, "monitor_selection": selections,
                "other_targets": observation.get("other_targets", [])[:1]}
    if check.get("tool") == "historical_episode" and isinstance(observation, dict):
        if observation.get("compacted_history"):
            return observation
        facts = []
        for item in (observation.get("observations") or [])[:2]:
            if not isinstance(item, dict):
                continue
            metric = item.get("metric_observation")
            fact = ({"metric_observation": _bounded(metric, max_items=3)} if metric else
                    {key: _bounded(item[key], max_items=2) for key in ("configuration", "examples", "summary") if item.get(key)})
            if fact:
                facts.append(fact)
        return {"compacted_history": True, "observations": facts,
                "limitation": "Partial retained history; missing facts cannot establish the same cause."}
    if isinstance(observation, dict):
        if observation.get("compacted_observation"):
            return observation
        facts = {key: value for key, value in observation.items()
                 if key not in {"source", "scope", "limitation", "metric_semantics"}}
        return {"compacted_observation": True, **_bounded(facts, max_items=2, max_depth=3),
                "limitation": _short(observation.get("limitation") or "Partial observation; omitted fields are unknown.", 120)}
    return _short(json.dumps(observation, ensure_ascii=True), 180)


def compact_for_model(
    context: dict[str, Any],
    checks: list[dict[str, Any]],
    *,
    max_prompt_tokens: int = 5200,
    priority_evidence_ids: list[str] | set[str] | tuple[str, ...] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Return a compact prompt context and the evidence IDs visible to the model.

    The full context/checks remain on disk. The model receives a bounded fact ledger
    plus the newest observation; it can still cite every record actually shown.
    """

    priority_ids = {str(item) for item in priority_evidence_ids or []}
    source_evidence = list(context.get("evidence") or [])
    # A user-requested revision stays first, then compact the source ledger around
    # failure signatures rather than high-volume heartbeats. This is especially
    # important when a realistic incident contains thousands of routine lines.
    source_evidence.sort(key=lambda item: _evidence_priority(item, priority_ids))
    evidence = []
    for item in source_evidence[:28]:
        compact_item = _evidence_item(item)
        if compact_item.get("revision_priority") or str(item.get("id")) in priority_ids:
            compact_item["revision_priority"] = True
        evidence.append(compact_item)
    visible_ids = [str(item["id"]) for item in evidence if item.get("id")]
    retained_checks = checks[-4:]
    recent = [_check_item(item, index == len(retained_checks) - 1) for index, item in enumerate(retained_checks)]
    visible_ids.extend(str(item["id"]) for item in recent if item.get("id") and item.get("status") == "completed")
    alerts = []
    source_alerts = context.get("alerts") or []
    source_alerts = sorted(source_alerts, key=_alert_time)
    selected_alerts = (source_alerts if len(source_alerts) <= 12 else
                       [source_alerts[round(index * (len(source_alerts) - 1) / 11)] for index in range(12)])
    for item in selected_alerts:
        alert = {
            "incident_id": item.get("incident_id"),
            "alertname": _short(item.get("alertname") or item.get("name") or item.get("alert_identity") or item.get("title"), 100),
            "severity": item.get("severity"),
            "status": item.get("current_status") or item.get("status"),
            "startsAt": item.get("startsAt") or item.get("started_at"),
            "endsAt": item.get("ended_at") or item.get("endsAt"),
            "labels": _bounded(item.get("labels"), max_items=6),
            "annotations": _bounded(item.get("annotations"), max_items=4),
        }
        alerts.append({key: value for key, value in alert.items() if value not in (None, "", [], {})})
    payload: dict[str, Any] = {
        "episode_id": context.get("episode_id"),
        "live_capture": bool(context.get("live_capture")),
        "evidence": evidence,
        "prior_checks": recent,
        "constraints": "Evidence is bounded and may be incomplete. Current state is not incident-time state. Time correlation is not causation.",
    }
    optional_fields = {
        "scope": _scope(context.get("scope")),
        "recurrence": _recurrence(context.get("recurrence")),
        "episode_lifecycle": _bounded(context.get("episode_lifecycle"), max_items=6),
        "alerts": alerts,
        "omitted_alerts": len(source_alerts) - len(alerts) if len(source_alerts) > len(alerts) else None,
        "impact": _bounded((context.get("impact") or [])[:8], max_items=5),
        "historical_candidates": _bounded((context.get("historical_candidates") or [])[:3], max_items=4),
        "priority_evidence_ids": [item["id"] for item in evidence if item.get("revision_priority")],
    }
    payload.update({key: value for key, value in optional_fields.items() if value not in (None, "", [], {})})
    protected_images = set([item.get("id") for item in evidence if item.get("visual_observation")][:2])

    def removable_evidence() -> bool:
        return len(payload["evidence"]) > 1 and payload["evidence"][-1].get("id") not in protected_images

    def refresh_visible_ids() -> list[str]:
        omitted_images = sum(item.get("domain") == "image_evidence" for item in source_evidence) - sum(
            item.get("visual_observation") is not None or item.get("domain") == "image_evidence"
            for item in payload["evidence"])
        if omitted_images:
            payload["omitted_images"] = omitted_images
        # Do not spend the remaining budget listing priorities already omitted.
        if "priority_evidence_ids" in payload:
            payload["priority_evidence_ids"] = [
                item["id"] for item in payload["evidence"] if item.get("revision_priority") and item.get("id")
            ]
        ids = [str(item["id"]) for item in payload["evidence"] if item.get("id")]
        ids.extend(str(item["id"]) for item in payload["prior_checks"]
                   if item.get("id") and item.get("status") == "completed")
        return list(dict.fromkeys(ids))

    visible_ids = refresh_visible_ids()
    # Tighten in a deterministic order until the payload meets its intended budget.
    while estimate_tokens(payload) > max_prompt_tokens:
        if len(payload["evidence"]) > 3 and removable_evidence():
            payload["evidence"].pop()
            visible_ids = refresh_visible_ids()
        elif removable_evidence() and len(payload["prior_checks"]) > 1 and all(
            item.get("required_observation") for item in payload["prior_checks"]
        ):
            # Source reads are fresher and more discriminating than lower-priority
            # retained summaries. Keep their structured form before trimming it.
            payload["evidence"].pop()
            visible_ids = refresh_visible_ids()
        elif len(payload["prior_checks"]) > 1 and any(
            not item.get("required_observation") for item in payload["prior_checks"]
        ):
            removable = next(index for index, item in enumerate(payload["prior_checks"])
                             if not item.get("required_observation"))
            payload["prior_checks"].pop(removable)
            visible_ids = refresh_visible_ids()
        elif payload.get("impact"):
            payload["impact"] = []
        elif payload.get("scope", {}).get("cluster"):
            payload["scope"].pop("cluster")
        elif (payload.get("scope", {}).get("pod")
              and str(payload["scope"].get("resource", {}).get("kind", "")).casefold() == "pod"
              and payload["scope"]["resource"].get("name") == payload["scope"]["pod"]):
            payload["scope"].pop("resource")
        elif payload.get("scope", {}).get("service") and (payload["scope"].get("pod") or payload["scope"].get("resource")):
            # Protect pod/namespace (or the only known non-pod identity), plus
            # capture time, even when repetitive alert details no longer fit.
            payload["scope"].pop("service")
        elif any(item.get("diagnostic_example") is not None for item in payload["evidence"]):
            for item in payload["evidence"]:
                item["diagnostic_example"] = None
        elif any(item.get("configuration") is not None for item in payload["evidence"]):
            for item in payload["evidence"]:
                item["configuration"] = None
        elif any(item.get("metric_observation") != compact_metric_observation(item.get("metric_observation"), minimal=True)
                 for item in payload["evidence"] if item.get("metric_observation")):
            for item in payload["evidence"]:
                if item.get("metric_observation"):
                    item["metric_observation"] = compact_metric_observation(item["metric_observation"], minimal=True)
        elif any(len(str(item.get("summary", ""))) > 140 for item in payload["evidence"]):
            for item in payload["evidence"]:
                item["summary"] = _short(item.get("summary"), 140)
                item["title"] = _short(item.get("title"), 100)
        elif any(item.get("question") or item.get("distinguishes") for item in payload["prior_checks"]):
            # The tool name and compact observation remain; trim metadata before
            # discarding a required observation's diagnostic signal.
            for item in payload["prior_checks"]:
                item.pop("question", None)
                item.pop("distinguishes", None)
        elif any(item.get("visual_observation") != _minimal_visual_observation(item["visual_observation"])
                 for item in payload["evidence"] if item.get("visual_observation")):
            for item in payload["evidence"]:
                if item.get("visual_observation"):
                    item["visual_observation"] = _minimal_visual_observation(item["visual_observation"])
        elif any(
            item.get("observation")
            and item.get("observation") != _minimal_check_observation(item)
            for item in payload["prior_checks"]
        ):
            for item in payload["prior_checks"]:
                item["observation"] = _minimal_check_observation(item)
        elif removable_evidence():
            payload["evidence"].pop()
            visible_ids = refresh_visible_ids()
        elif any(set(item) - {"incident_id", "alertname", "startsAt", "endsAt"} for item in payload.get("alerts", [])):
            payload["alerts"] = [{key: value for key, value in item.items()
                                  if key in {"incident_id", "alertname", "startsAt", "endsAt"}}
                                 for item in payload["alerts"]]
        elif len(payload.get("alerts", [])) > 2:
            payload["omitted_alerts"] = payload.get("omitted_alerts", 0) + len(payload["alerts"]) - 2
            payload["alerts"] = [payload["alerts"][0], payload["alerts"][-1]]
        elif len(payload["prior_checks"]) > 1:
            # Prior episodes are comparisons, not replacements for observations
            # of the current episode. Their later query time is not freshness.
            removable = next((index for index, item in enumerate(payload["prior_checks"])
                              if item.get("tool") == "historical_episode"), 0)
            payload["prior_checks"].pop(removable)
            visible_ids = refresh_visible_ids()
        elif payload.get("historical_candidates"):
            payload.pop("historical_candidates", None)
        elif payload.get("episode_lifecycle"):
            payload.pop("episode_lifecycle", None)
        elif payload.get("constraints") != "Evidence may be incomplete.":
            payload["constraints"] = "Evidence may be incomplete."
        elif payload["prior_checks"]:
            payload["prior_checks"] = []
            visible_ids = refresh_visible_ids()
        elif payload["evidence"] and len(str(payload["evidence"][0].get("summary") or "")) > 60:
            payload["evidence"][0]["summary"] = _short(payload["evidence"][0].get("summary"), 60)
        elif any(set(item) - {"id", "summary", "metric_observation", "visual_observation", "time_range", "limitation"}
                 for item in payload["evidence"]):
            payload["evidence"] = [{key: item[key] for key in
                                    ("id", "summary", "metric_observation", "visual_observation", "time_range", "limitation")
                                    if key in item} for item in payload["evidence"]]
            visible_ids = refresh_visible_ids()
        else:
            break
    return payload, list(dict.fromkeys(visible_ids))
