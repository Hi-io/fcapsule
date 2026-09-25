"""Privacy-bounded projection of retained FCAPSule artifacts into Estima records."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from fcapsule.processing.anonymizer import anonymize_text


NORMALIZATION_VERSION = "fcapsule-atlas-normalization-v1"
MAX_OBSERVATIONS = 24
MAX_CASE_BYTES = 24 * 1024
_SECRET_KEY = re.compile(r"(?i)(password|passwd|secret|token|credential|api.?key|private.?key|authorization|connection.?string)")
_SAFE_CONFIG_KEY = re.compile(r"(?i)^(?:[a-z][a-z0-9_.-]{0,63})$")
_SAFE_CONFIG_VALUE = re.compile(r"^(?:-?\d+(?:\.\d+)?|true|false|enabled|disabled|on|off|v?\d+(?:\.\d+){0,3})$", re.I)
_SAFE_INSTANCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
_SAFE_DIAGNOSTICS = {
    "status", "status_code", "upstream_status", "consumer_status", "error_type", "error_code", "errno",
    "sqlstate", "failure_kind", "retryable", "retry_count", "attempt", "attempt_limit", "timeout_ms",
    "timeout_seconds", "duration_ms", "dependency_duration_ms", "upstream_duration_ms", "max_connections",
    "threads_connected", "pool_checked_out", "utilization", "memory_limit", "buffered_bytes",
    "maximum_buffered_bytes", "buffered_pages", "page_bytes", "exit_code", "delivery", "acknowledgement",
}


def _text(value: Any, limit: int = 160) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    text = " ".join(anonymize_text(str(value), preserve_relations=False).split())
    if _SECRET_KEY.search(text):
        return ""
    return text[:limit]


def _instance_identity(value: str) -> str:
    """Keep trusted routing identity stable without treating it as log text."""
    if _SAFE_INSTANCE_ID.fullmatch(value):
        return value
    return "fcapsule-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: Any) -> int | float | None:
    if type(value) not in (int, float) or not (-1e300 < float(value) < 1e300):
        return None
    return value


def _observation(kind: str, key: str, value: Any, source: str, observed_at: Any,
                 reference: str, unit: str = "") -> dict[str, Any] | None:
    safe_key = _text(key, 96)
    safe_source = _text(source, 96)
    safe_ref = _text(reference, 64)
    when = _timestamp(observed_at)
    if not safe_key or not safe_ref:
        return None
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        safe_value: str | int | float = _text(value, 180) if isinstance(value, str) else value
        if safe_value == "":
            return None
    elif isinstance(value, bool):
        safe_value = value
    else:
        return None
    return {"kind": kind, "key": safe_key, "value": safe_value,
            "unit": _text(unit, 32) or None, "source": safe_source,
            "observed_at": when, "reference": safe_ref}


def _scope(primary_capsule: dict[str, Any], app: dict[str, Any] | None, episode: dict[str, Any]) -> dict[str, str]:
    case = primary_capsule.get("case") if isinstance(primary_capsule.get("case"), dict) else {}
    app = app if isinstance(app, dict) else {}
    labels: dict[str, Any] = {}
    alerts = primary_capsule.get("alerts") if isinstance(primary_capsule.get("alerts"), list) else []
    if alerts and isinstance(alerts[0], dict):
        labels = alerts[0].get("labels") if isinstance(alerts[0].get("labels"), dict) else {}
    scope = {
        "cluster": app.get("cluster") or case.get("cluster"),
        "namespace": app.get("namespace") or case.get("namespace"),
        "service": case.get("service") or app.get("name"),
        "workload": app.get("name") or (episode.get("resource") or {}).get("name"),
        "environment": app.get("environment") or case.get("environment"),
        "cnfc_id": labels.get("cnfc") or labels.get("cnfc_id") or labels.get("telecom.example.com/cnfc") or case.get("cnfc_id"),
        "vnfc_id": labels.get("vnfc") or labels.get("vnfc_id") or labels.get("telecom.example.com/vnfc") or case.get("vnfc_id"),
    }
    return {key: _text(value, 120) for key, value in scope.items() if _text(value, 120)}


def _fingerprint(episode: dict[str, Any], findings: list[dict[str, Any]], observations: list[dict[str, Any]]) -> str:
    recurrence = str(episode.get("recurrence_key") or "")
    family = recurrence.rsplit("|", 1)[-1] if recurrence else ""
    family = " ".join(re.findall(r"[a-z0-9]+", family.lower()))[:96]
    resource_kind = str(episode.get("resource_kind") or (episode.get("resource") or {}).get("kind") or "unknown").lower()
    resource_kind = resource_kind if resource_kind in {"pod", "workload", "service", "node", "cnfc", "vnfc", "application"} else "other"
    categories = sorted({
        str(item.get("category"))[:48] for item in findings
        if isinstance(item, dict) and re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", str(item.get("category") or ""))
    })[:8]
    diagnostic_keys = sorted({
        str(item["key"]).removeprefix("diagnostic.") for item in observations
        if item.get("kind") == "FM" and str(item.get("key", "")).startswith("diagnostic.")
    })[:12]
    signature = {"normalization": NORMALIZATION_VERSION, "alert_family": family,
                 "resource_kind": resource_kind, "finding_categories": categories,
                 "diagnostic_keys": diagnostic_keys}
    digest = hashlib.sha256(json.dumps(signature, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"{NORMALIZATION_VERSION}:{digest}"


def project_estima_record(
    instance_id: str, episode: dict[str, Any], investigation: dict[str, Any],
    retained: list[dict[str, Any]], app: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build a compact, self-contained envelope without copying prose or log bodies."""
    if not isinstance(episode, dict) or not isinstance(investigation, dict) or not retained:
        return None
    retained = retained[-12:]
    primary = retained[-1]
    capsule = primary.get("capsule") if isinstance(primary.get("capsule"), dict) else {}
    scope = _scope(capsule, app, episode)
    observations: list[dict[str, Any]] = []
    references: list[str] = []
    categories: dict[str, list[str]] = {}

    for item in retained:
        source_capsule = item.get("capsule") if isinstance(item.get("capsule"), dict) else {}
        selected = source_capsule.get("selected_evidence") if isinstance(source_capsule.get("selected_evidence"), list) else []
        case = source_capsule.get("case") if isinstance(source_capsule.get("case"), dict) else {}
        alert_time = case.get("window", {}).get("end") if isinstance(case.get("window"), dict) else None
        for evidence in selected[:24]:
            if not isinstance(evidence, dict):
                continue
            evidence_ref = _text(evidence.get("evidence_id") or evidence.get("source_id"), 64)
            evidence_type = str(evidence.get("type") or "")
            time_range = evidence.get("time_range") if isinstance(evidence.get("time_range"), dict) else {}
            when = time_range.get("end") or time_range.get("start") or alert_time
            if evidence_type == "alert":
                # Alert annotations/descriptions are free-form and intentionally omitted.
                name = _text(evidence.get("title"), 96)
                observation = _observation("FM", "alert_family", name or "alert", "Alertmanager", when,
                                           evidence_ref, "")
                if observation:
                    observations.append(observation)
            elif evidence_type == "log_template":
                fields = evidence.get("diagnostic_fields") if isinstance(evidence.get("diagnostic_fields"), dict) else {}
                for key, value in fields.items():
                    canonical = str(key).lower()
                    if canonical not in _SAFE_DIAGNOSTICS:
                        continue
                    if isinstance(value, (str, int, float, bool)):
                        observation = _observation("FM", f"diagnostic.{canonical}", value, "OpenSearch diagnostic fields",
                                                   when, evidence_ref, "")
                        if observation:
                            observations.append(observation)
            elif evidence_type == "metric_anomaly":
                metric = _text(evidence.get("metric"), 96)
                metric_observation = evidence.get("metric_observation") if isinstance(evidence.get("metric_observation"), dict) else {}
                condition = metric_observation.get("condition") if isinstance(metric_observation.get("condition"), dict) else {}
                latest = condition.get("latest") if isinstance(condition.get("latest"), dict) else {}
                value = _number(latest.get("value"))
                if value is None:
                    value = _number(condition.get("max"))
                unit = metric_observation.get("unit") or evidence.get("unit") or ""
                metric_time = latest.get("timestamp") or condition.get("last_match") or when
                if metric and value is not None:
                    observation = _observation("PM", metric, value, "Prometheus retained metric", metric_time,
                                               evidence_ref, str(unit))
                    if observation:
                        observations.append(observation)
            elif evidence_type == "configuration":
                config = evidence.get("configuration") if isinstance(evidence.get("configuration"), dict) else {}
                data = config.get("data") if isinstance(config.get("data"), dict) else {}
                config_source = f"{config.get('kind', 'ConfigMap')}/{config.get('name', 'retained')}"
                version = _text(config.get("resource_version"), 48)
                if version:
                    config_source += f"@{version}"
                for key, value in data.items():
                    key_text = str(key)
                    if _SECRET_KEY.search(key_text) or not _SAFE_CONFIG_KEY.fullmatch(key_text):
                        continue
                    value_text = str(value).strip() if isinstance(value, (str, int, float, bool)) else ""
                    if not value_text or value_text.lower() in {"<redacted>", "<secret>"} or not _SAFE_CONFIG_VALUE.fullmatch(value_text):
                        continue
                    observation = _observation("CM", key_text, value_text, config_source, when, evidence_ref, "")
                    if observation:
                        observations.append(observation)
            if evidence_ref and evidence_ref not in references:
                references.append(evidence_ref)

        report = item.get("report") if isinstance(item.get("report"), dict) else {}
        for signal in (report.get("pm_signals") if isinstance(report.get("pm_signals"), list) else [])[:12]:
            if not isinstance(signal, dict):
                continue
            metric = _text(signal.get("metric"), 96)
            value = _number(signal.get("peak_value"))
            if not metric or value is None:
                continue
            reference = _text(signal.get("evidence_id"), 64)
            observation = _observation("PM", metric, value, "Prometheus retained metric",
                                       signal.get("alert_timestamp") or alert_time, reference,
                                       str(signal.get("unit") or ""))
            if observation:
                observations.append(observation)

    findings = [item for item in investigation.get("findings", [])
                if isinstance(item, dict) and item.get("state") == "observed"][:8]
    for finding in findings:
        category = str(finding.get("category") or "")
        if category and re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", category):
            refs = [str(value) for value in finding.get("evidence_ids", []) if isinstance(value, str)]
            categories[category] = list(dict.fromkeys([*categories.get(category, []), *refs]))[:8]
            for reference in refs[:4]:
                observation = _observation("FM", "finding.category", category, "FCAPSule retained finding",
                                           investigation.get("finished_at"), reference)
                if observation:
                    observations.append(observation)
    # Keep one representative of each key and cap total payload size.
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for observation in observations:
        key = (observation["kind"], observation["key"], observation["reference"])
        unique.setdefault(key, observation)
    observations = list(unique.values())[:MAX_OBSERVATIONS]
    fingerprint = _fingerprint(episode, findings, observations)
    local_episode_id = str(episode.get("episode_id") or "")
    remote_episode_id = "ep-" + hashlib.sha256(f"{instance_id}\0{local_episode_id}".encode()).hexdigest()[:24]
    observed_at = _timestamp(episode.get("last_activity_at") or investigation.get("finished_at") or episode.get("started_at"))
    if not observed_at:
        return None
    references = list(dict.fromkeys(item["reference"] for item in observations))
    recurrence = str(episode.get("recurrence_key") or "")
    alert_family = _text(recurrence.rsplit("|", 1)[-1] if recurrence else "alert", 64) or "alert"
    salient = []
    for item in sorted(observations, key=lambda value: {"PM": 0, "FM": 1, "CM": 2}.get(value["kind"], 3)):
        if item["kind"] in {"PM", "FM", "CM"}:
            value = item["value"]
            if isinstance(value, (int, float)):
                value_text = f"{value:g}" if isinstance(value, float) else str(value)
            else:
                value_text = str(value)
            suffix = f" {item['unit']}" if item.get("unit") else ""
            phrase = f"{item['key']}={value_text}{suffix}"
            if phrase not in salient:
                salient.append(phrase)
        if len(salient) == 2:
            break
    workload = scope.get("workload") or scope.get("service") or "retained workload"
    signals = ", ".join(salient) if salient else f"{len(observations)} retained observation(s)"
    summary = f"{alert_family} on {workload}: {signals}. Cause is unverified."[:300]
    assessment = investigation.get("assessment") if isinstance(investigation.get("assessment"), dict) else {}
    mechanism = assessment.get("likely_mechanism") or assessment.get("summary")
    hypotheses = []
    if isinstance(mechanism, str):
        candidate = " ".join(anonymize_text(mechanism, preserve_relations=False).split())
        suspicious = (
            len(candidate) > 420 or "\n" in mechanism or "{" in mechanism or "}" in mechanism
            or re.search(r"(?i)\b(password|passwd|secret|token|credential|authorization|bearer)\b", candidate)
            or re.search(r"(?:\b\w{1,40}\s*[=:]\s*\S+)", candidate)
        )
        supporting = [str(value) for value in assessment.get("evidence_ids", [])
                      if isinstance(value, str) and value in references]
        if candidate and not suspicious and supporting:
            hypotheses.append({"statement": f"Unverified assessment: {candidate[:360]}",
                               "confidence": None, "supporting_refs": supporting[:8]})
    payload = {
        "schema_version": 1,
        "instance_id": _instance_identity(instance_id or "fcapsule-default"),
        "episode_id": remote_episode_id,
        "observed_at": observed_at,
        "scope": scope,
        "summary": summary,
        "observations": observations,
        "hypotheses": hypotheses,
        "fingerprint": fingerprint,
        "normalization_version": NORMALIZATION_VERSION,
    }
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    if len(encoded) > MAX_CASE_BYTES:
        payload["observations"] = observations[:12]
        payload["summary"] = "Retained evidence profile; cause is not verified by Estima."
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        if len(encoded) > MAX_CASE_BYTES:
            return None
    return payload


# The v1 record shape and fingerprint prefix are retained to keep old outbox data valid.
project_atlas_case = project_estima_record
