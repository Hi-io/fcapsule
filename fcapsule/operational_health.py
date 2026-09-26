"""Read-only, bounded operational metrics for Prometheus scraping."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SOURCE_NAMES = ("prometheus", "opensearch", "kubernetes")
SOURCE_STATES = ("disabled", "healthy", "unavailable", "unknown")
SYNC_STATES = ("disabled", "idle", "running", "succeeded", "failed", "unknown")
PROVIDER_NAMES = ("deepseek", "openrouter")
PROVIDER_STATES = (
    "not_selected", "ready", "not_configured", "not_validated", "invalid_credentials",
    "insufficient_credit", "unsupported_model", "temporarily_unavailable", "unknown",
)
PUBLICATION_STATES = ("pending", "failed", "sent", "other")
CLEANUP_INTERVAL_SECONDS = 60.0

_METRIC_HELP = {
    "fcapsule_source_connection_state": "One-hot state of the last observed source connection check.",
    "fcapsule_source_sync_state": "One-hot state of the most recent FCAPSule source synchronization.",
    "fcapsule_source_sync_last_attempt_timestamp_seconds": "Unix timestamp of the most recent source synchronization attempt, or zero if none is known.",
    "fcapsule_source_work_items": "Current source synchronization work items by bounded state.",
    "fcapsule_provider_selected": "Whether the bounded provider is selected for the core investigator.",
    "fcapsule_provider_capability_state": "One-hot validation state for the selected core provider.",
    "fcapsule_provider_work_items": "Briefing jobs queued or running across all providers.",
    "fcapsule_shared_publication_enabled": "Whether Collective shared publication is enabled.",
    "fcapsule_shared_publication_backlog": "Retained Collective outbox records by bounded delivery state.",
    "fcapsule_retention_policy_days": "Configured incident retention period in days.",
    "fcapsule_retention_cleanup_seconds_since_check": "Seconds since the last incident-retention cleanup check, or zero if none is known.",
    "fcapsule_retention_cleanup_due": "Whether the periodic incident-retention cleanup check is due.",
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if result < 0 or result != result or result == float("inf"):
        return default
    return result


def _count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _timestamp(value: Any) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, OverflowError):
        return 0.0


def _one_hot(state: str, states: tuple[str, ...]) -> list[tuple[str, float]]:
    safe_state = state if state in states else "unknown"
    return [(item, 1.0 if item == safe_state else 0.0) for item in states]


def _boolean_setting(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _publication_enabled(control_plane: Any) -> bool:
    """Read only the publish flag without invoking settings migration or exposing secrets."""

    enabled = False
    for name in ("FCAPSULE_COLLECTIVE_PUBLISH", "FCAPSULE_ESTIMA_PUBLISH", "FCAPSULE_ATLAS_PUBLISH"):
        if name in os.environ:
            enabled = _boolean_setting(os.environ[name]) or False
            break
    settings_path = getattr(control_plane, "estima_settings_path", None)
    if settings_path:
        try:
            settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
            if isinstance(settings, dict) and "publish_enabled" in settings:
                stored = _boolean_setting(settings["publish_enabled"])
                enabled = stored if stored is not None else bool(settings["publish_enabled"])
        except (OSError, ValueError, TypeError):
            pass
    return bool(enabled)


def _collect(control_plane: Any, *, monotonic_now: float) -> dict[str, list[tuple[dict[str, str], float]]]:
    metrics: dict[str, list[tuple[dict[str, str], float]]] = {name: [] for name in _METRIC_HELP}

    source_state = getattr(control_plane, "source_state", {})
    source_state = source_state if isinstance(source_state, dict) else {}
    config = source_state.get("configuration")
    config = config if isinstance(config, dict) else {}
    enabled = config.get("enabled") is not False
    targets = source_state.get("targets")
    targets = targets if isinstance(targets, dict) else {}
    for source in SOURCE_NAMES:
        target = targets.get(source)
        if not enabled:
            state = "disabled"
        elif not isinstance(target, dict) or not isinstance(target.get("ok"), bool):
            state = "unknown"
        else:
            state = "healthy" if target["ok"] else "unavailable"
        for value, sample in _one_hot(state, SOURCE_STATES):
            metrics["fcapsule_source_connection_state"].append(({"source": source, "state": value}, sample))

    active_job = getattr(control_plane, "active_job", None)
    phase = getattr(control_plane, "phases", {})
    phase = phase.get("sources", {}) if isinstance(phase, dict) else {}
    phase_status = phase.get("status") if isinstance(phase, dict) else None
    if not enabled:
        sync_state = "disabled"
    elif active_job == "sources":
        sync_state = "running"
    elif phase_status == "done":
        sync_state = "succeeded"
    elif source_state.get("error"):
        sync_state = "failed"
    elif source_state.get("last_sync_at"):
        sync_state = "succeeded"
    else:
        sync_state = "idle"
    for value, sample in _one_hot(sync_state, SYNC_STATES):
        metrics["fcapsule_source_sync_state"].append(({"state": value}, sample))
    metrics["fcapsule_source_sync_last_attempt_timestamp_seconds"].append(({}, _timestamp(source_state.get("last_sync_at"))))
    metrics["fcapsule_source_work_items"].append(({"state": "sync_running"}, 1.0 if sync_state == "running" else 0.0))
    metrics["fcapsule_source_work_items"].append((
        {"state": "webhook_pending"}, 1.0 if bool(getattr(control_plane, "pending_webhook_sync", False)) else 0.0,
    ))

    try:
        ai = control_plane.ai_configuration()
    except Exception:
        ai = {}
    selected_provider = ai.get("provider") if isinstance(ai, dict) else None
    selected_provider = selected_provider if selected_provider in PROVIDER_NAMES else None
    capability = ai.get("capability", {}) if isinstance(ai, dict) else {}
    capability_status = capability.get("status") if isinstance(capability, dict) else None
    safe_capability = capability_status if capability_status in PROVIDER_STATES else "unknown"
    for provider in PROVIDER_NAMES:
        selected = provider == selected_provider
        metrics["fcapsule_provider_selected"].append(({"provider": provider}, 1.0 if selected else 0.0))
        if selected:
            for value, sample in _one_hot(safe_capability, PROVIDER_STATES):
                metrics["fcapsule_provider_capability_state"].append(({"provider": provider, "state": value}, sample))
        else:
            metrics["fcapsule_provider_capability_state"].append(({"provider": provider, "state": "not_selected"}, 1.0))
    jobs = getattr(control_plane, "briefing_jobs", set())
    try:
        job_count = len(jobs)
    except TypeError:
        job_count = 0
    metrics["fcapsule_provider_work_items"].append(({}, float(_count(job_count))))

    try:
        status = control_plane.store.atlas_outbox_status()
    except Exception:
        status = {}
    status = status if isinstance(status, dict) else {}
    counts = status.get("counts", {})
    counts = counts if isinstance(counts, dict) else {}
    fixed_counts = {state: _count(counts.get(state)) for state in PUBLICATION_STATES if state != "other"}
    other = sum(_count(value) for key, value in counts.items() if key not in fixed_counts)
    fixed_counts["other"] = other
    if not any(fixed_counts.values()):
        fixed_counts["pending"] = _count(status.get("pending_count"))
        fixed_counts["failed"] = _count(status.get("failed_count"))
    for state in PUBLICATION_STATES:
        metrics["fcapsule_shared_publication_backlog"].append(({"state": state}, float(fixed_counts[state])))
    metrics["fcapsule_shared_publication_enabled"].append(({}, 1.0 if _publication_enabled(control_plane) else 0.0))

    try:
        general = control_plane.general_configuration()
    except Exception:
        general = {}
    general = general if isinstance(general, dict) else {}
    retention_days = min(3650, max(1, _count(general.get("incident_retention_days", 30))))
    metrics["fcapsule_retention_policy_days"].append(({}, float(retention_days)))
    last_check = _number(getattr(control_plane, "_last_retention_check", 0.0))
    age = max(0.0, monotonic_now - last_check) if last_check else 0.0
    metrics["fcapsule_retention_cleanup_seconds_since_check"].append(({}, age))
    metrics["fcapsule_retention_cleanup_due"].append(({}, 1.0 if not last_check or age >= CLEANUP_INTERVAL_SECONDS else 0.0))

    return metrics


def render_operational_metrics(
    control_plane: Any,
    *,
    monotonic_now: float | None = None,
) -> str:
    """Render Prometheus text exposition with a fixed label vocabulary."""

    samples = _collect(
        control_plane,
        monotonic_now=time.monotonic() if monotonic_now is None else monotonic_now,
    )
    lines: list[str] = []
    for name, description in _METRIC_HELP.items():
        lines.append(f"# HELP {name} {description}")
        lines.append(f"# TYPE {name} gauge")
        for labels, value in samples[name]:
            label_text = ""
            if labels:
                encoded = ",".join(f'{key}="{item}"' for key, item in labels.items())
                label_text = "{" + encoded + "}"
            lines.append(f"{name}{label_text} {value:.15g}")
    return "\n".join(lines) + "\n"
