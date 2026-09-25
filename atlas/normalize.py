from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from .models import CaseEnvelope


SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|authorization)\b\s*[:=]\s*\S+", re.I),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.I),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
SENSITIVE_KEY = re.compile(r"(?:password|passwd|secret|token|credential|api[_-]?key|access[_-]?key|authorization)", re.I)
KEY_CHARS = re.compile(r"[^a-z0-9]+")


class UnsafeCase(ValueError):
    pass


def _safe_text(value: str, field: str, limit: int) -> str:
    text = unicodedata.normalize("NFKC", value)
    text = " ".join(text.split())
    if not text or len(text) > limit:
        raise UnsafeCase(f"{field} must contain 1 to {limit} characters")
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        raise UnsafeCase(f"{field} appears to contain a secret")
    return text


def normalize_key(value: str, *, field: str = "key") -> str:
    text = unicodedata.normalize("NFKC", value).strip().casefold()
    normalized = KEY_CHARS.sub("_", text).strip("_")
    if not normalized or len(normalized) > 120:
        raise UnsafeCase(f"{field} is not a valid short key")
    if SENSITIVE_KEY.search(normalized):
        raise UnsafeCase(f"{field} must not identify a secret or credential")
    return normalized


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _value(value: Any, *, field: str) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > 2**63 - 1:
            raise UnsafeCase(f"{field} integer is out of range")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UnsafeCase(f"{field} must be finite")
        return value
    if isinstance(value, str):
        return _safe_text(value, field, 256)
    raise UnsafeCase(f"{field} must be a scalar, not raw telemetry or a nested object")


def normalize_case(payload: CaseEnvelope | dict[str, Any]) -> dict[str, Any]:
    try:
        envelope = payload if isinstance(payload, CaseEnvelope) else CaseEnvelope.model_validate(payload)
    except ValidationError:
        raise

    instance_id = _safe_text(envelope.instance_id, "instance_id", 128)
    episode_id = _safe_text(envelope.episode_id, "episode_id", 128)
    scope: dict[str, str | None] = {}
    for key, value in envelope.scope.model_dump().items():
        scope[key] = None if value is None else _safe_text(value, f"scope.{key}", 200)

    summary = _safe_text(envelope.summary, "summary", 2000)
    observations: list[dict[str, Any]] = []
    for item in envelope.observations:
        observation: dict[str, Any] = {
            "kind": normalize_key(item.kind, field="observation.kind"),
            "key": normalize_key(item.key, field="observation.key"),
            "value": _value(item.value, field="observation.value"),
            "unit": None if item.unit is None else _safe_text(item.unit, "observation.unit", 40),
            "source": None if item.source is None else _safe_text(item.source, "observation.source", 120),
            "observed_at": _timestamp(item.observed_at),
            "reference": None if item.reference is None else _safe_text(item.reference, "observation.reference", 500),
        }
        observations.append(observation)

    hypotheses: list[dict[str, Any]] = []
    for item in envelope.hypotheses:
        hypotheses.append({
            "statement": _safe_text(item.statement, "hypothesis.statement", 1000),
            "confidence": item.confidence,
            "supporting_refs": [_safe_text(ref, "hypothesis.supporting_ref", 500) for ref in item.supporting_refs],
        })

    fingerprint = None
    if envelope.fingerprint:
        fingerprint = _safe_text(envelope.fingerprint, "fingerprint", 256).casefold()
    case = {
        "schema_version": 1,
        "normalization_version": None if envelope.normalization_version is None else _safe_text(
            envelope.normalization_version, "normalization_version", 80
        ),
        "instance_id": instance_id,
        "episode_id": episode_id,
        "revision": envelope.revision,
        "observed_at": _timestamp(envelope.observed_at),
        "scope": scope,
        "summary": summary,
        "observations": observations,
        "hypotheses": hypotheses,
        "fingerprint": fingerprint,
    }
    return case


def normalize_scope_filter(scope: dict[str, Any] | None) -> dict[str, str] | None:
    if not scope:
        return None
    result: dict[str, str] = {}
    for key in ("environment", "cluster", "namespace", "service", "workload", "cnfc_id", "vnfc_id"):
        value = scope.get(key)
        if value is not None:
            result[key] = _safe_text(value, f"scope.{key}", 200)
    return result


def normalize_fingerprint(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return _safe_text(value, "fingerprint", 256).casefold()


def search_document(case: dict[str, Any]) -> str:
    parts = [case["summary"]]
    parts.extend(value for value in case["scope"].values() if value)
    for item in case["observations"]:
        parts.extend((item["kind"], item["key"], str(item["value"]), item["unit"] or ""))
    return " ".join(parts).casefold()


def observation_pattern_id(observation: dict[str, Any]) -> str:
    signature = json.dumps(
        [observation["kind"], observation["key"], observation["value"], observation["unit"]],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(signature).hexdigest()[:24]
