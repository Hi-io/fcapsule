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

from fcapsule.processing.anonymizer import anonymize_text, diagnostic_fields


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


def _visual_observation(
    observations: list[Any], visible_text: list[Any] | None = None, budget: int = 1000,
) -> dict[str, Any]:
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
    visible_rows = []
    raw_visible_text = visible_text if isinstance(visible_text, list) else []
    for item in raw_visible_text[:6]:
        raw_text = item.get("text") if isinstance(item, dict) else item
        text = anonymize_text(str(raw_text or "")).strip()
        if text:
            visible_rows.append({"text": _short(text, 120), "source": "Vision-model text extraction"})
    return {"facts": [row for _, row in sorted(kept)],
            "visible_text": visible_rows,
            "provenance": {
                "facts": "Vision-model observations from the uploaded image; not independently verified.",
                "visible_text": "OCR-style text extracted from the uploaded image by the configured vision model; transcription may be imperfect.",
            },
            "omitted_facts": len(observations) - len(kept),
            "omitted_visible_text": max(0, len(raw_visible_text) - len(visible_rows))}


def _minimal_visual_observation(value: dict[str, Any]) -> dict[str, Any]:
    facts = value.get("facts") if isinstance(value.get("facts"), list) else []
    visible_text = value.get("visible_text") if isinstance(value.get("visible_text"), list) else []
    reduced = _visual_observation(facts, visible_text, budget=600)
    # Count only newly removed rows; this must be idempotent because the budget
    # loop can re-check an already-minimal image observation.
    reduced["omitted_facts"] = int(value.get("omitted_facts", 0)) + max(0, len(facts) - len(reduced["facts"]))
    reduced["omitted_visible_text"] = int(value.get("omitted_visible_text", 0)) + max(
        0, len(visible_text) - len(reduced["visible_text"])
    )
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
        "diagnostic_examples": [
            _paired_log_example(example) for example in examples[:2]
        ] if examples else None,
        "diagnostic_fields": diagnostic_fields(None, item.get("diagnostic_fields"))
        if isinstance(item.get("diagnostic_fields"), dict) else None,
        "configuration": _bounded(item.get("configuration"), max_items=3) if item.get("configuration") else None,
        "operator_context": _bounded(item.get("operator_context"), max_items=3) if item.get("operator_context") else None,
        "limitation": _short(item.get("limitation"), 180),
        "revision_priority": True if item.get("revision_priority") else None,
        "metric_observation": compact_metric_observation(item.get("metric_observation")),
    }
    if item.get("domain") == "image_evidence" and item.get("visual_observations"):
        values["visual_observation"] = _visual_observation(
            item["visual_observations"], item.get("visible_text", [])
        )
        values.pop("summary", None)
        values.pop("diagnostic_examples", None)
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
        source = fields(value.get("source"), (("adapter", 40), ("endpoint", 80), ("captured_at", 40),
                                               ("capture_mode", 40), ("capture_note", 180)),
                        ("rule_qualifier_count",))
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
        "ownership_match", "mysql_error_code", "upstream_status", "duration_ms",
        "query_revision", "timeout_seconds",
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
        "ownership_match", "mysql_error_code", "upstream_status", "duration_ms",
        "query_revision", "timeout_seconds",
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
    example = item if "message" in item else (examples[0] if examples else None)
    if not isinstance(example, dict):
        return _short(example, 220) if example else None
    raw_message = example.get("message", "")
    message = json.dumps(raw_message, ensure_ascii=True, sort_keys=True, separators=(",", ":")) \
        if isinstance(raw_message, dict) else str(raw_message)
    try:
        structured = json.loads(message)
    except (TypeError, ValueError):
        structured = None
    fields = diagnostic_fields(message, example.get("diagnostic_fields")
                               if isinstance(example.get("diagnostic_fields"), dict) else None)
    if isinstance(structured, dict):
        values = {key: structured.get(key) for key in (
            "level", "message", "error", "error_type", "reason", "exit_code", "errno",
            "sqlstate", "mysql_error_code", "status_code", "disposition", "payload_encoding",
            "buffered_bytes", "page_bytes", "delivery", "rows", "kdf", "rounds", "mode",
            "timeout_seconds", "expected_schema", "response_schema", "query_revision", "endpoint",
        )}
        result = {key: _short(anonymize_text(str(value)), 180) for key, value in values.items()
                  if value not in (None, "")}
    else:
        result = {
            "level": example.get("level"),
            "message": _short(message, 220),
        }
    result.update({key: _short(value, 180) for key, value in fields.items()
                   if key not in result and value not in (None, "")})
    return {
        "timestamp": example.get("timestamp"),
        **{key: value for key, value in result.items() if value not in (None, "")},
    }


def _paired_log_example(example: dict[str, Any]) -> dict[str, Any] | str | None:
    """Keep event-local diagnostics attached to the timestamped representative that supplied them."""
    if isinstance(example, str):
        return _short(example, 220)
    normalized = _log_example(example)
    if not isinstance(normalized, dict):
        return normalized
    fields = {key: value for key, value in normalized.items()
              if key not in {"timestamp", "level", "message"}}
    result = {key: normalized[key] for key in ("timestamp", "level", "message") if normalized.get(key) is not None}
    if fields:
        result["diagnostic_fields"] = fields
    return result


def _log_observation(result: dict[str, Any]) -> dict[str, Any]:
    """Keep a small, failure-first ledger from a bounded log query."""

    patterns = [item for item in result.get("patterns", []) if isinstance(item, dict)]
    patterns.sort(key=_pattern_priority, reverse=True)
    primary = patterns[0] if patterns else {}
    values = {
        "matching_patterns": result.get("matching_patterns"),
        "top_signal": _log_example(primary) if primary else None,
        "fields": diagnostic_fields(None, primary.get("fields")) if primary.get("fields") else None,
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
    dependencies = []
    for item in result.get("declared_dependencies", []):
        if not isinstance(item, dict) or not item.get("service"):
            continue
        endpoint = item.get("configured_endpoint") if isinstance(item.get("configured_endpoint"), dict) else {}
        dependencies.append({
            "service": item.get("service"),
            "configured_via": _short(item.get("configured_via"), 180),
            "configured_endpoint": {
                key: endpoint[key] for key in ("host", "scheme", "port", "port_source") if endpoint.get(key) is not None
            },
            "port_configured_via": _short(item.get("port_configured_via"), 180),
            "observed_at": item.get("observed_at"),
        })
    values = {
        "declared_dependencies": dependencies[:4],
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
    """Keep bounded target, selector, endpoint, Service-port and pod-health facts together."""

    pods = set((result.get("scope") or {}).get("pods") or [])
    targets = [item for key in ("active_targets", "dropped_targets") for item in result.get(key, [])
               if isinstance(item, dict)]
    target_pods = {str(item.get("pod")) for item in targets if item.get("pod")}
    sensitive = ("password", "secret", "token", "credential", "private", "certificate", "apikey", "api_key", "authorization")

    def labels(value):
        return {str(key): _short(anonymize_text(str(item)), 120) for key, item in value.items()
                if not any(word in str(key).casefold() for word in sensitive)}

    discovery_targets = result.get("discovery_targets") if isinstance(result.get("discovery_targets"), dict) else {}
    target_service = _short(anonymize_text(str(discovery_targets.get("target_service") or "")), 120)
    target_workload = _short(anonymize_text(str(discovery_targets.get("target_workload") or "")), 120)

    def pod_health(value):
        if not isinstance(value, dict):
            return None
        ready_status = value.get("ready_status")
        if not isinstance(ready_status, str) or ready_status not in {"true", "false", "unknown"}:
            ready_status = "unknown"
        health = {
            "pod": _short(anonymize_text(str(value.get("pod") or "")), 120),
            "namespace": _short(anonymize_text(str(value.get("namespace") or "")), 120),
            "workload": _short(anonymize_text(str(value.get("workload") or "")), 120),
            "phase": _short(anonymize_text(str(value.get("phase") or "Unknown")), 32),
            "ready": value.get("ready") if type(value.get("ready")) is bool else None,
            "ready_status": ready_status,
            "container_health": [],
        }
        containers = value.get("container_health") if isinstance(value.get("container_health"), list) else []
        for container in containers[:4]:
            if not isinstance(container, dict):
                continue
            item = {"name": _short(anonymize_text(str(container.get("name") or "")), 120),
                    "ready": container.get("ready") if type(container.get("ready")) is bool else None,
                    "restart_count": container.get("restart_count") if type(container.get("restart_count")) is int else None}
            for state_key in ("state", "laststate"):
                raw_state = container.get(state_key)
                if not isinstance(raw_state, dict):
                    continue
                state = {"kind": raw_state["kind"]} if raw_state.get("kind") in {"waiting", "running", "terminated"} else {}
                for key in ("reason", "exitCode", "signal", "startedAt", "finishedAt"):
                    raw = raw_state.get(key)
                    if isinstance(raw, (str, int, float)):
                        state[key] = _short(anonymize_text(str(raw)), 80) if isinstance(raw, str) else raw
                item[state_key] = state
            health["container_health"].append(item)
        return health

    def endpoint_slices(value, *, limit=2):
        slices = []
        raw_slices = value if isinstance(value, list) else []
        for raw_slice in raw_slices[:limit]:
            if not isinstance(raw_slice, dict):
                continue
            row = {key: _short(anonymize_text(str(raw_slice.get(key) or "")), 120)
                   for key in ("name", "namespace", "service", "address_type") if raw_slice.get(key)}
            row["ports"] = []
            raw_ports = raw_slice.get("ports") if isinstance(raw_slice.get("ports"), list) else []
            for port in raw_ports[:4]:
                if not isinstance(port, dict):
                    continue
                row["ports"].append({"name": _short(anonymize_text(str(port.get("name") or "")), 63) or None,
                                     "port": port.get("port") if type(port.get("port")) is int else None,
                                     "protocol": _short(str(port.get("protocol") or "TCP"), 16)})
            row["endpoints"] = []
            raw_endpoints = raw_slice.get("endpoints") if isinstance(raw_slice.get("endpoints"), list) else []
            for endpoint in raw_endpoints[:4]:
                if not isinstance(endpoint, dict):
                    continue
                target_ref = endpoint.get("target_ref") if isinstance(endpoint.get("target_ref"), dict) else {}
                target = {key: _short(anonymize_text(str(target_ref.get(key))), 120)
                          for key in ("kind", "name", "namespace") if target_ref.get(key)}
                row["endpoints"].append({
                    "target_ref": target,
                    **{key: endpoint.get(key) if type(endpoint.get(key)) is bool else None
                       for key in ("ready", "serving", "terminating")},
                    "address_count": endpoint.get("address_count") if type(endpoint.get("address_count")) is int else None,
                })
            if type(raw_slice.get("omitted_endpoints")) is int and raw_slice["omitted_endpoints"]:
                row["omitted_endpoints"] = raw_slice["omitted_endpoints"]
            slices.append(row)
        return slices

    def service_ports(value):
        result_ports = []
        raw_ports = value if isinstance(value, list) else []
        for port in raw_ports[:6]:
            if not isinstance(port, dict):
                continue
            row = {"name": _short(anonymize_text(str(port.get("name") or "")), 63) or None,
                   "port": port.get("port") if type(port.get("port")) is int else None}
            target_port = port.get("target_port")
            if isinstance(target_port, (str, int)):
                row["target_port"] = _short(anonymize_text(str(target_port)), 63) if isinstance(target_port, str) else target_port
            result_ports.append(row)
        return result_ports

    def monitor_endpoints(value):
        result_endpoints = []
        raw_endpoints = value if isinstance(value, list) else []
        for endpoint in raw_endpoints[:3]:
            if not isinstance(endpoint, dict):
                continue
            row = {}
            for key in ("port", "portNumber", "targetPort", "path", "interval", "scheme"):
                raw = endpoint.get(key)
                if isinstance(raw, (str, int, float)):
                    row[key] = _short(anonymize_text(str(raw)), 120) if isinstance(raw, str) else raw
            result_endpoints.append(row)
        return result_endpoints

    def port_checks(value):
        checks = []
        raw_checks = value if isinstance(value, list) else []
        for check in raw_checks[:3]:
            if not isinstance(check, dict):
                continue
            status = check.get("status")
            if not isinstance(status, str) or status not in {
                    "matches_service_port_name", "does_not_match_service_port_name", "unknown"}:
                status = "unknown"
            names = check.get("service_port_names") if isinstance(check.get("service_port_names"), list) else []
            row = {"status": status,
                   "service_port_names": [_short(anonymize_text(str(name)), 63) for name in names[:6]],
                   "omitted_service_ports": check.get("omitted_service_ports")
                       if type(check.get("omitted_service_ports")) is int else None}
            for key in ("configured_port_name", "configured_target_port"):
                raw = check.get(key)
                if isinstance(raw, (str, int, float)):
                    row[key] = _short(anonymize_text(str(raw)), 80) if isinstance(raw, str) else raw
            if check.get("omitted_monitor_endpoints"):
                row["omitted_monitor_endpoints"] = check["omitted_monitor_endpoints"]
            row["comparison_basis"] = _short(check.get("comparison_basis"), 200)
            checks.append(row)
        return checks

    def pod_port_checks(value):
        checks = []
        raw_checks = value if isinstance(value, list) else []
        for check in raw_checks[:3]:
            if not isinstance(check, dict):
                continue
            status = check.get("status")
            if not isinstance(status, str) or status not in {
                    "matches_pod_container_port", "does_not_match_pod_container_port", "unknown"}:
                status = "unknown"
            row = {"status": status,
                   "container_ports": service_ports(check.get("container_ports") or []),
                   "container_ports_complete": check.get("container_ports_complete")
                       if type(check.get("container_ports_complete")) is bool else None}
            for key in ("configured_port_name", "configured_port_number", "configured_target_port"):
                raw = check.get(key)
                if isinstance(raw, (str, int, float)):
                    row[key] = _short(anonymize_text(str(raw)), 80) if isinstance(raw, str) else raw
            row["comparison_basis"] = _short(check.get("comparison_basis"), 200)
            checks.append(row)
        return checks

    selections = []
    represented_pools = set()
    for selection in result.get("monitor_selection", []):
        if not isinstance(selection, dict):
            continue
        monitor = selection.get("monitor") or {}
        kind = monitor.get("kind")
        if kind not in {"ServiceMonitor", "PodMonitor"}:
            continue
        selector = labels(monitor.get("match_labels") or {})
        expressions = []
        selector_keys = list(selector)
        for expression in (monitor.get("match_expressions") or [])[:8]:
            if not isinstance(expression, dict):
                continue
            key = str(expression.get("key") or "")
            if not key:
                continue
            selector_keys.append(key)
            row = {"key": _short(key, 253), "operator": _short(expression.get("operator"), 32)}
            if not any(word in key.casefold() for word in sensitive):
                row["values"] = [_short(anonymize_text(str(value)), 120) for value in (expression.get("values") or [])[:8]]
            else:
                row["redacted"] = True
            expressions.append(row)
        prefix = "__meta_kubernetes_" + ("service" if kind == "ServiceMonitor" else "pod") + "_label_"
        pool = f"{kind[0].lower() + kind[1:]}/{monitor.get('namespace')}/{monitor.get('name')}/"
        represented_pools.add(pool)
        matched = []
        for target in targets:
            if not str(target.get("scrape_pool") or "").startswith(pool):
                continue
            discovered = target.get("labels") or {}
            observed = {key: discovered[prefix + re.sub(r"[^a-zA-Z0-9_]", "_", key)] for key in selector_keys
                        if prefix + re.sub(r"[^a-zA-Z0-9_]", "_", key) in discovered}
            matched.append({**{key: _short(anonymize_text(str(target[key])), 180) for key in
                              ("pod", "service", "state", "health", "last_error", "scrape_pool",
                               "scrape_endpoint", "scrape_path") if target.get(key)},
                            "selector_labels": labels(observed)})
        matched.sort(key=lambda item: (item.get("pod") not in pods, not bool(item.get("last_error")),
                                       not bool(item["selector_labels"])))
        selected_kind = "evaluated_services" if kind == "ServiceMonitor" else "evaluated_pods"
        evaluated = []
        raw_resources = [item for item in (selection.get(selected_kind) or []) if isinstance(item, dict)]
        raw_resources.sort(key=lambda item: (
            item.get("name") != target_service if kind == "ServiceMonitor" and target_service else False,
            item.get("target_relevance") not in {"alert_target_service", "alert_target_workload", "prometheus_target_pod"},
            item.get("workload_selector_match") is not True,
            item.get("namespace_selected") is False,
            item.get("name") not in target_pods if kind == "PodMonitor" else False,
            item.get("selector_evaluation", {}).get("status") != "matched",
            str(item.get("name") or ""),
        ))
        for resource in raw_resources[:12]:
            raw_labels = resource.get("labels") if isinstance(resource.get("labels"), dict) else {}
            safe_evaluation = resource.get("selector_evaluation") if isinstance(resource.get("selector_evaluation"), dict) else {}
            requirements = []
            for requirement in (safe_evaluation.get("requirements") or [])[:8]:
                if not isinstance(requirement, dict):
                    continue
                key = str(requirement.get("key") or "")
                row = {"key": _short(key, 253), "operator": _short(requirement.get("operator"), 32)}
                if requirement.get("redacted") or any(word in key.casefold() for word in sensitive):
                    row["redacted"] = True
                else:
                    for field in ("expected", "observed"):
                        if field in requirement:
                            raw = requirement[field]
                            row[field] = ([_short(anonymize_text(str(value)), 120) for value in raw[:8]]
                                          if isinstance(raw, list) else _short(anonymize_text(str(raw)), 120))
                if type(requirement.get("matches")) is bool:
                    row["matches"] = requirement["matches"]
                requirements.append(row)
            evaluated.append({
                "name": _short(anonymize_text(str(resource.get("name") or "")), 120),
                "namespace": _short(anonymize_text(str(resource.get("namespace") or "")), 120),
                "labels": labels({key: raw_labels[key] for key in selector_keys if key in raw_labels}),
                "selector_status": safe_evaluation.get("status", "unknown"),
                "requirements": requirements,
                **({"service_selector": labels(resource.get("service_selector"))}
                   if isinstance(resource.get("service_selector"), dict) else {}),
                **({"namespace_selected": resource.get("namespace_selected")}
                   if type(resource.get("namespace_selected")) is bool else {}),
                **({"workload_selector_match": resource.get("workload_selector_match")}
                   if type(resource.get("workload_selector_match")) is bool else {}),
                **({"target_relevance": resource.get("target_relevance")}
                   if resource.get("target_relevance") in {"alert_target_service", "alert_target_workload", "prometheus_target_pod"} else {}),
            })
            if kind == "ServiceMonitor":
                evaluated[-1].update({
                    "service_selector": labels(resource.get("selector") or {}),
                    "service_ports": service_ports(resource.get("ports") or []),
                    "omitted_service_ports": resource.get("omitted_ports")
                        if type(resource.get("omitted_ports")) is int else None,
                    "service_ports_complete": resource.get("ports_complete")
                        if type(resource.get("ports_complete")) is bool else None,
                    "endpoint_port_checks": port_checks(resource.get("endpoint_port_checks") or []),
                    "endpoint_slices": endpoint_slices(resource.get("endpoint_slices") or []),
                    "endpoint_pod_health": [pod_health(item) for item in
                                             (resource.get("endpoint_pod_health") or [])[:3]
                                             if isinstance(item, dict)],
                })
            elif kind == "PodMonitor":
                evaluated[-1]["health"] = pod_health(resource.get("health"))
                evaluated[-1]["container_ports"] = service_ports(resource.get("container_ports") or [])
                evaluated[-1]["container_ports_complete"] = resource.get("container_ports_complete") \
                    if type(resource.get("container_ports_complete")) is bool else None
                evaluated[-1]["endpoint_port_checks"] = pod_port_checks(resource.get("endpoint_port_checks") or [])
        namespace_scope = selection.get("namespace_scope") if isinstance(selection.get("namespace_scope"), dict) else {}
        selection_row = {
            "kind": kind, "name": _short(anonymize_text(str(monitor.get("name") or "")), 120),
            "match_labels": dict(list(selector.items())[:8]),
            "match_expressions": expressions,
            "selector_complete": monitor.get("selector_complete") is not False,
            "namespace_scope": {
                "status": namespace_scope.get("status", "unknown"),
                "effective_namespaces": [_short(anonymize_text(str(value)), 120)
                                          for value in (namespace_scope.get("effective_namespaces") or [])[:3]],
            },
            "evaluated_resources": evaluated[:3],
            "omitted_resources": max(0, len(selection.get(selected_kind) or []) - min(3, len(evaluated))),
            "configured_endpoints": monitor_endpoints(selection.get("configured_endpoints") or monitor.get("endpoints") or []),
            **{key: _bounded(selection[key], max_items=3) for key in ("matched_services", "matched_pods") if key in selection},
            "targets": matched[:2], "target_count": len(matched),
        }
        selections.append(selection_row)
        if kind == "PodMonitor":
            pod_labels = [item for item in result.get("current_pod_labels", []) if isinstance(item, dict)]
            pod_labels.sort(key=lambda item: item.get("pod") not in pods)
            selections[-1]["current_pod_labels"] = [
                {"pod": _short(anonymize_text(str(item.get("pod") or "")), 120),
                 "labels": labels({key: value for key, value in (item.get("labels") or {}).items() if key in selector_keys})}
                for item in pod_labels[:2]]
    selections.sort(key=lambda item: (
        not any(row.get("target_relevance") == "alert_target_service" or
                (target_service and row.get("name") == target_service) for row in item["evaluated_resources"]),
        not any(row.get("target_relevance") in {"alert_target_workload", "prometheus_target_pod"} or
                row.get("workload_selector_match") is True for row in item["evaluated_resources"]),
        not any(target.get("pod") in pods for target in item["targets"] + item.get("current_pod_labels", [])),
        not any(target.get("selector_labels") for target in item["targets"]),
    ))
    other_targets = [target for target in targets if not any(
        str(target.get("scrape_pool") or "").startswith(pool) for pool in represented_pools)]
    other_targets.sort(key=lambda item: (item.get("pod") not in pods, not bool(item.get("last_error"))))
    return {
        "compacted_discovery": True,
        "monitor_selection": selections[:3], "monitor_count": len(selections),
        **({"target_inventory": {key: result["target_inventory"][key] for key in (
            "status", "complete", "scope_complete", "scope", "reason", "requested_scrape_pools",
            "omitted_scrape_pools", "response_limited_pools", "omitted_active_targets",
            "omitted_dropped_targets",
        ) if key in result["target_inventory"]}}
           if isinstance(result.get("target_inventory"), dict) else {}),
        **({"discovery_targets": {key: value for key, value in (
            ("target_service", target_service), ("target_workload", target_workload)) if value}}
           if target_service or target_workload else {}),
        **({"pod_inventory": {key: result["pod_inventory"][key] for key in
                               ("status", "complete", "has_more", "omitted_pods")
                               if key in result["pod_inventory"]}}
           if isinstance(result.get("pod_inventory"), dict) else {}),
        "observed_at": result.get("observed_at"),
        "provenance": _bounded(result.get("provenance"), max_items=3) if result.get("provenance") else [],
        "current_service_labels": [
            {"service": _short(anonymize_text(str(item.get("service") or "")), 120),
             "namespace": _short(anonymize_text(str(item.get("namespace") or "")), 120),
             "labels": labels(item.get("labels") or {}),
             "selector": labels(item.get("selector") or {}),
             "ports": service_ports(item.get("ports") or []),
             "ports_complete": item.get("ports_complete") if type(item.get("ports_complete")) is bool else None,
             "endpoint_slices": endpoint_slices(item.get("endpoint_slices") or [], limit=1)}
            for item in sorted(
                (item for item in (result.get("current_service_labels") or []) if isinstance(item, dict)),
                key=lambda item: (str(item.get("service") or "") != target_service,
                                  str(item.get("namespace") or ""), str(item.get("service") or "")))[:3]
        ],
        "current_pod_health": [pod_health(item) for item in
                               (result.get("current_pod_health") or [])[:4] if isinstance(item, dict)],
        "endpoint_slice_inventory": {
            "status": (result.get("endpoint_slice_inventory") or {}).get("status", "unknown"),
            "omitted_slices": (result.get("endpoint_slice_inventory") or {}).get("omitted_slices", 0),
        },
        "other_targets": [{key: _short(anonymize_text(str(target[key])), 120) for key in
                           ("pod", "service", "state", "health", "last_error") if target.get(key)}
                          for target in other_targets[:2]],
        "target_count": len(targets),
        "limitation": "Current bounded discovery, not incident-time state. Missing labels/targets may be omitted or filtered; absence is not proof of a cause.",
    }


def _dependency_observation(result: dict[str, Any]) -> dict[str, Any]:
    """Keep endpoint and Service-port facts explicit within the model's check budget."""
    service = result.get("service_observation") if isinstance(result.get("service_observation"), dict) else {}
    declarations = []
    for item in (result.get("declared_endpoints") or [])[:4]:
        if not isinstance(item, dict):
            continue
        endpoint = item.get("configured_endpoint") if isinstance(item.get("configured_endpoint"), dict) else {}
        declarations.append({
            "service": _short(anonymize_text(str(item.get("service") or "")), 120),
            "configured_via": _short(anonymize_text(str(item.get("configured_via") or "")), 160),
            "configured_endpoint": {
                key: (_short(anonymize_text(str(endpoint[key])), 120) if isinstance(endpoint.get(key), str) else endpoint.get(key))
                for key in ("host", "scheme", "port", "port_source") if endpoint.get(key) is not None
            },
            "port_configured_via": _short(anonymize_text(str(item.get("port_configured_via"))), 180)
            if item.get("port_configured_via") else None,
            "observed_at": item.get("observed_at"),
        })
    safe_ports = []
    for item in (service.get("ports") or [])[:8]:
        if not isinstance(item, dict):
            continue
        safe_ports.append({key: item.get(key) for key in ("name", "port", "target_port", "protocol") if item.get(key) is not None})
    service_record = {
        key: _short(anonymize_text(str(service[key])), 160) if isinstance(service.get(key), str) else service.get(key)
        for key in ("name", "namespace", "type", "source", "observed_at", "resource_version")
        if service.get(key) is not None
    }
    if isinstance(service.get("selector"), dict):
        sensitive = ("password", "secret", "token", "credential", "private", "certificate", "apikey", "api_key", "authorization")
        service_record["selector"] = {
            str(key): ("<redacted>" if any(word in str(key).casefold() for word in sensitive)
                      else _short(anonymize_text(str(value)), 120))
            for key, value in list(service["selector"].items())[:16]
        }
    service_record["ports"] = safe_ports
    comparisons = []
    for item in (result.get("port_comparisons") or [])[:4]:
        if not isinstance(item, dict):
            continue
        comparisons.append({
            key: (_short(anonymize_text(str(item[key])), 160) if isinstance(item.get(key), str) else item.get(key))
            for key in ("configured_host", "configured_port", "configured_port_source", "status", "comparison_basis")
            if item.get(key) is not None
        } | {"service_ports": safe_ports})
    observations = _bounded((result.get("observations") or [])[:5], max_items=5)
    return {
        "compacted_dependency": True,
        "service": _short(anonymize_text(str(result.get("service") or "")), 120),
        "pod": _short(anonymize_text(str(result.get("pod") or "")), 120) if result.get("pod") else None,
        "matching_pods": result.get("matching_pods"),
        "declared_endpoints": declarations,
        "service_observation": service_record,
        "port_comparisons": comparisons,
        "latest_alert_at": result.get("latest_alert_at"),
        "window": result.get("window"),
        "observed_at": result.get("observed_at"),
        "provenance": _bounded(result.get("provenance") or [], max_items=4),
        "observations": observations,
        "unavailable_sources": (result.get("unavailable_sources") or [])[:3],
        "not_collected_sources": (result.get("not_collected_sources") or [])[:3],
        "limitation": _short(result.get("limitation"), 360),
    }


def _resource_history_observation(result: dict[str, Any], *, minimal: bool = False) -> dict[str, Any]:
    """Preserve a few timestamped alert-phase samples and report data freshness without guessing a TTL."""
    observations = []
    for item in result.get("observations", []) if isinstance(result.get("observations"), list) else []:
        if not isinstance(item, dict):
            continue
        row = {"metric": _short(item.get("metric") or "unknown", 120),
               "samples": item.get("samples", 0)}
        if isinstance(item.get("labels"), dict) and item["labels"]:
            row["labels"] = _bounded(item["labels"], max_items=4)
        freshness = item.get("freshness") if isinstance(item.get("freshness"), dict) else {}
        row["freshness"] = {key: _bounded(freshness[key], max_items=2)
                             for key in ("status", "latest_sample_at", "age_seconds", "captured_at", "assessment")
                             if key in freshness}
        for key in ("before_alert", "nearest_alert", "after_alert", "sampled_peak"):
            anchor = item.get(key)
            if isinstance(anchor, dict):
                row[key] = {name: anchor[name] for name in ("timestamp", "value", "offset_seconds") if name in anchor}
            elif key in item:
                row[key] = None
        for key in (("max",) if minimal else ("start", "end", "min", "max", "median", "first", "last")):
            if key in item:
                row[key] = item[key]
        if row:
            observations.append(row)
        if len(observations) >= (2 if minimal else 8):
            break
    no_data = not observations or all(
        item.get("samples") == 0 or
        (isinstance(item.get("freshness"), dict) and item["freshness"].get("status") == "no_data")
        for item in observations
    )
    return {
        "compacted_resource_history": True,
        "captured_at": result.get("captured_at") or result.get("observed_at"),
        "latest_alert_at": result.get("latest_alert_at"),
        "data_status": "no_data" if no_data else "sampled",
        "observations": observations,
        **({"metric_semantics": _bounded(result.get("metric_semantics"), max_items=2)}
           if not minimal and result.get("metric_semantics") else {}),
        "limitation": _short(result.get("limitation") or
            "Prometheus samples are bounded; capture age is reported without assuming a scrape interval or retention policy.", 220),
    }


def _check_item(check: dict[str, Any], latest: bool) -> dict[str, Any]:
    raw_result = check.get("result") if isinstance(check.get("result"), dict) else {}
    if check.get("tool") == "search_logs":
        result = _log_observation(raw_result)
    elif check.get("tool") == "workload_state":
        result = _workload_observation(raw_result)
    elif check.get("tool") == "scrape_discovery" and check.get("status") == "completed":
        result = _discovery_observation(raw_result)
    elif check.get("tool") == "dependency_evidence" and check.get("status") == "completed":
        result = _dependency_observation(raw_result)
    elif check.get("tool") == "resource_history" and check.get("status") == "completed":
        result = _resource_history_observation(raw_result)
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


def _tiny_discovery_observation(observation: dict[str, Any]) -> dict[str, Any]:
    def pick(source, keys):
        if not isinstance(source, dict):
            return {}
        return {key: source[key] for key in keys if key in source and source[key] not in (None, "", [], {})}

    minimal_selections = []
    for selection in observation.get("monitor_selection", [])[:1]:
        if not isinstance(selection, dict):
            continue
        kind = selection.get("kind")
        resources = []
        for resource in (selection.get("evaluated_resources") or [])[:1]:
            if not isinstance(resource, dict):
                continue
            common = ("name", "namespace", "labels", "selector_status", "requirements", "target_relevance")
            if kind == "ServiceMonitor":
                fields = common + ("service_selector", "namespace_selected", "workload_selector_match",
                                   "service_ports", "service_ports_complete", "endpoint_port_checks",
                                   "endpoint_slices", "endpoint_pod_health")
                row = pick(resource, fields)
                row["requirements"] = (row.get("requirements") or [])[:2]
                row["service_ports"] = (row.get("service_ports") or [])[:2]
                row["endpoint_port_checks"] = (row.get("endpoint_port_checks") or [])[:1]
                row["endpoint_slices"] = [
                    {**endpoint_slice, "ports": endpoint_slice.get("ports", [])[:2],
                     "endpoints": endpoint_slice.get("endpoints", [])[:1]}
                    for endpoint_slice in (row.get("endpoint_slices") or [])[:1]
                    if isinstance(endpoint_slice, dict)]
                row["endpoint_pod_health"] = (row.get("endpoint_pod_health") or [])[:1]
            else:
                fields = common + ("health", "container_ports", "container_ports_complete", "endpoint_port_checks")
                row = pick(resource, fields)
                row["requirements"] = (row.get("requirements") or [])[:2]
                row["container_ports"] = (row.get("container_ports") or [])[:2]
                row["endpoint_port_checks"] = (row.get("endpoint_port_checks") or [])[:1]
            resources.append(row)
        namespace = selection.get("namespace_scope") if isinstance(selection.get("namespace_scope"), dict) else {}
        minimal_selections.append({
            **pick(selection, ("kind", "name", "match_labels", "match_expressions", "selector_complete",
                               "configured_endpoints", "targets", "target_count")),
            "namespace_scope": {**pick(namespace, ("status",)),
                                "effective_namespaces": (namespace.get("effective_namespaces") or [])[:1]},
            "evaluated_resources": resources,
        })
        minimal_selections[-1]["match_expressions"] = minimal_selections[-1].get("match_expressions", [])[:2]
        minimal_selections[-1]["configured_endpoints"] = minimal_selections[-1].get("configured_endpoints", [])[:1]
        minimal_selections[-1]["targets"] = minimal_selections[-1].get("targets", [])[:1]
    inventory = observation.get("endpoint_slice_inventory")
    return {
        "compacted_discovery": True,
        "minimal_discovery": True,
        "tiny_discovery": True,
        **pick(observation, ("discovery_targets", "observed_at")),
        **({"target_inventory": pick(observation.get("target_inventory"), (
            "status", "complete", "scope_complete", "scope", "reason", "requested_scrape_pools",
            "omitted_scrape_pools", "response_limited_pools", "omitted_active_targets",
            "omitted_dropped_targets",
        ))} if isinstance(observation.get("target_inventory"), dict) else {}),
        **({"pod_inventory": pick(observation.get("pod_inventory"),
                                   ("status", "complete", "has_more", "omitted_pods"))}
           if isinstance(observation.get("pod_inventory"), dict) else {}),
        "monitor_selection": minimal_selections,
        **({"endpoint_slice_inventory": pick(inventory, ("status", "omitted_slices"))}
           if isinstance(inventory, dict) else {}),
        "limitation": observation.get("limitation"),
    }


def _minimal_check_observation(check: dict[str, Any]) -> Any:
    observation = check.get("observation")
    if isinstance(observation, str) and len(observation) <= 180:
        return observation
    if check.get("tool") == "search_logs" and isinstance(observation, dict):
        return {key: observation[key] for key in
                ("top_signal", "fields", "first_seen", "last_seen", "occurrences", "sampled") if key in observation}
    if check.get("tool") == "resource_history" and isinstance(observation, dict):
        if observation.get("minimal_resource_history"):
            return observation
        minimal = _resource_history_observation(observation, minimal=True)
        minimal["minimal_resource_history"] = True
        for item in minimal.get("observations", []):
            item.pop("min", None)
            item.pop("median", None)
            item.pop("first", None)
            item.pop("last", None)
            item.pop("start", None)
            item.pop("end", None)
        minimal.pop("metric_semantics", None)
        return minimal
    if check.get("tool") == "dependency_evidence" and isinstance(observation, dict):
        if observation.get("minimal_dependency"):
            return observation
        compact = observation if observation.get("compacted_dependency") else _dependency_observation(observation)
        return {
            **compact,
            "minimal_dependency": True,
            "declared_endpoints": compact.get("declared_endpoints", [])[:1],
            "port_comparisons": compact.get("port_comparisons", [])[:1],
            "provenance": compact.get("provenance", [])[:2],
            "observations": compact.get("observations", [])[:2],
        }
    if check.get("tool") == "scrape_discovery" and isinstance(observation, dict) and "monitor_selection" in observation:
        if observation.get("minimal_discovery"):
            return observation
        selections = [{**item, "targets": item.get("targets", [])[:1],
                       "evaluated_resources": item.get("evaluated_resources", [])[:1],
                       "match_expressions": item.get("match_expressions", [])[:4]}
                      for item in observation["monitor_selection"][:1]]
        return {**observation, "compacted_discovery": True, "minimal_discovery": True,
                "monitor_selection": selections, "current_service_labels": observation.get("current_service_labels", [])[:1],
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
    primary_incident_id = context.get("primary_incident_id")
    for item in source_evidence[:28]:
        compact_item = _evidence_item(item)
        provenance = item.get("provenance") if isinstance(item.get("provenance"), list) else []
        incident_ids = list(dict.fromkeys(
            row.get("incident_id") for row in provenance
            if isinstance(row, dict) and isinstance(row.get("incident_id"), str) and row.get("incident_id")
        ))
        if primary_incident_id and incident_ids and primary_incident_id not in incident_ids:
            compact_item["incident_ids"] = incident_ids[:2]
        if compact_item.get("revision_priority") or str(item.get("id")) in priority_ids:
            compact_item["revision_priority"] = True
        evidence.append(compact_item)
    visible_ids = [str(item["id"]) for item in evidence if item.get("id")]
    retained_checks = checks[-4:]
    recent = [_check_item(item, index == len(retained_checks) - 1) for index, item in enumerate(retained_checks)]
    visible_ids.extend(str(item["id"]) for item in recent if item.get("id") and item.get("status") == "completed")
    alerts = []
    source_alerts = context.get("alerts") or []
    primary_incident_id = context.get("primary_incident_id")
    chronological_alerts = sorted(source_alerts, key=_alert_time)
    primary_alert = next((item for item in chronological_alerts
                          if item.get("incident_id") == primary_incident_id), None)
    sibling_alerts = [item for item in chronological_alerts if item is not primary_alert]
    if len(source_alerts) <= 12:
        selected_alerts = ([primary_alert] if primary_alert else []) + sibling_alerts
    else:
        sibling_limit = 11 if primary_alert else 12
        sampled_siblings = (sibling_alerts if len(sibling_alerts) <= sibling_limit else
                            [sibling_alerts[round(index * (len(sibling_alerts) - 1) / (sibling_limit - 1))]
                             for index in range(sibling_limit)] if sibling_limit > 1 else [sibling_alerts[-1]])
        selected_alerts = ([primary_alert] if primary_alert else []) + sampled_siblings
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
        "constraints": "Evidence may be incomplete. Current state is not incident-time state. Time correlation is not causation.",
    }
    if primary_incident_id:
        payload["primary_incident_id"] = primary_incident_id
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
        elif any(len(item.get("diagnostic_examples") or []) > 1 for item in payload["evidence"]):
            for item in payload["evidence"]:
                if item.get("diagnostic_examples"):
                    item["diagnostic_examples"] = item["diagnostic_examples"][:1]
        elif any(item.get("diagnostic_examples") for item in payload["evidence"]):
            for item in payload["evidence"]:
                item.pop("diagnostic_examples", None)
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
            and (item.get("observation") != _minimal_check_observation(item)
                 or (item.get("tool") == "scrape_discovery"
                     and isinstance(item.get("observation"), dict)
                     and item["observation"].get("minimal_discovery")
                     and not item["observation"].get("tiny_discovery")))
            for item in payload["prior_checks"]
        ):
            for item in payload["prior_checks"]:
                observation = item.get("observation")
                if (item.get("tool") == "scrape_discovery" and isinstance(observation, dict)
                        and observation.get("minimal_discovery") and not observation.get("tiny_discovery")):
                    item["observation"] = _tiny_discovery_observation(observation)
                else:
                    item["observation"] = _minimal_check_observation(item)
        elif len(payload["prior_checks"]) > 1 and any(
            not item.get("required_observation") for item in payload["prior_checks"]
        ):
            # A model-selected source read should survive at least its specialized
            # compact representation. Drop optional checks only after reducing the
            # observation, not before the current result reaches the next turn.
            removable = next(index for index, item in enumerate(payload["prior_checks"])
                             if not item.get("required_observation"))
            payload["prior_checks"].pop(removable)
            visible_ids = refresh_visible_ids()
        elif removable_evidence():
            payload["evidence"].pop()
            visible_ids = refresh_visible_ids()
        elif any(set(item) - {"incident_id", "alertname", "startsAt", "endsAt"} for item in payload.get("alerts", [])):
            payload["alerts"] = [{key: value for key, value in item.items()
                                  if key in {"incident_id", "alertname", "startsAt", "endsAt"}}
                                 for item in payload["alerts"]]
        elif len(payload.get("alerts", [])) > 2:
            payload["omitted_alerts"] = payload.get("omitted_alerts", 0) + len(payload["alerts"]) - 2
            primary = next((item for item in payload["alerts"]
                            if item.get("incident_id") == primary_incident_id), None)
            other = next((item for item in reversed(payload["alerts"]) if item is not primary), None)
            payload["alerts"] = ([primary] if primary else [payload["alerts"][0]]) + ([other] if other else [])
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
        elif any(set(item) - {"id", "incident_ids", "summary", "metric_observation", "visual_observation", "diagnostic_fields", "diagnostic_examples",
                              "time_range", "limitation"}
                 for item in payload["evidence"]):
            payload["evidence"] = [{key: item[key] for key in
                                    ("id", "incident_ids", "summary", "metric_observation", "visual_observation", "diagnostic_fields", "diagnostic_examples",
                                     "time_range", "limitation")
                                    if key in item} for item in payload["evidence"]]
            visible_ids = refresh_visible_ids()
        else:
            break
    return payload, list(dict.fromkeys(visible_ids))
