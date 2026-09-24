"""Privacy-preserving masking and relation tokens for capsule evidence."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,36}\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
TOKEN_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[=:]\s*[^\s,;]+")
QUOTED_SECRET_RE = re.compile(r'''(?i)(["'](?:api[_-]?key|token|secret|password|authorization)["']\s*:\s*)(["'])(?:\\.|(?!\2).)*?\2''')
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
URL_CREDENTIALS_RE = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s:]+:[^/@\s]+@")
HEX_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b")
NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?")
RELATION_TOKEN_RE = re.compile(r"^<REF:[A-Z2-7]{10}>$")

# Pseudonyms are consistent within one process so separate lines can refer to
# the same entity. A process-local HMAC key avoids making guessable IDs
# recoverable from the saved pseudonym. The key itself is never persisted.
_RELATION_KEY = secrets.token_bytes(32)

_DIAGNOSTIC_ALIASES = {
    "exitcode": "exit_code",
    "errno": "errno",
    "status": "status",
    "statuscode": "status_code",
    "upstreamstatus": "upstream_status",
    "upstreamstatuscode": "upstream_status",
    "consumerstatus": "consumer_status",
    "consumerstatuscode": "consumer_status",
    "observedstatus": "observed_status",
    "validationfailure": "validation_failure",
    "outcome": "outcome",
    "identityfield": "identity_field",
    "missingfields": "missing_fields",
    "observedfields": "observed_fields",
    "expectedfields": "expected_fields",
    "changedfields": "changed_fields",
    "httpstatus": "status_code",
    "httpstatuscode": "status_code",
    "httpresponsestatuscode": "status_code",
    "sqlstate": "sqlstate",
    "mysqlerrorcode": "mysql_error_code",
    "errorcode": "error_code",
    "error": "error_message",
    "errortype": "error_type",
    "exceptiontype": "error_type",
    "errormessage": "error_message",
    "exceptionmessage": "error_message",
    "reason": "reason",
    "disposition": "disposition",
    "payloadencoding": "payload_encoding",
    "maxconnections": "max_connections",
    "memorylimit": "memory_limit",
    "bufferedbytes": "buffered_bytes",
    "pagebytes": "page_bytes",
    "delivery": "delivery",
    "acknowledgement": "acknowledgement",
    "ack": "acknowledgement",
    "consumerdecision": "consumer_decision",
    "ownershipmatch": "ownership_match",
    "constraint": "constraint",
    "pairid": "pair_id",
    "lockorder": "lock_order",
    "sku": "sku",
    "firstsku": "first_sku",
    "secondsku": "second_sku",
    "requestkeyid": "request_key_id",
    "acceptedkeyid": "accepted_key_id",
    "signingkeyid": "signing_key_id",
    "ownerref": "owner_ref",
    "orderref": "order_ref",
    "existingorderref": "existing_order_ref",
    "rows": "rows",
    "kdf": "kdf",
    "rounds": "rounds",
    "mode": "mode",
    "timeoutseconds": "timeout_seconds",
    "timeoutms": "timeout_ms",
    "durationms": "duration_ms",
    "retrycount": "retry_count",
    "attempt": "attempt",
    "expectedschema": "expected_schema",
    "responseschema": "response_schema",
    "schemaversion": "schema_version",
    "queryrevision": "query_revision",
    "endpoint": "endpoint",
    "host": "host",
    "port": "port",
    "traceid": "trace_id",
    "requestid": "request_id",
    "correlationid": "correlation_id",
    "transactionid": "transaction_id",
    "spanid": "span_id",
    "orderid": "order_id",
    "eventid": "event_id",
    "messageid": "message_id",
    "jobid": "job_id",
    "sessionid": "session_id",
}
_CORRELATION_FIELDS = {
    "trace_id", "request_id", "correlation_id", "transaction_id", "span_id",
    "order_id", "event_id", "message_id", "job_id", "session_id", "pair_id",
    "sku", "first_sku", "second_sku", "owner_ref", "order_ref", "existing_order_ref",
}
_VERSION_ONLY_IDENTIFIER_FIELDS = {"request_key_id", "accepted_key_id", "signing_key_id"}
_GROUPING_DIAGNOSTIC_FIELDS = {
    "exit_code", "errno", "status", "status_code", "upstream_status", "consumer_status", "observed_status",
    "sqlstate", "mysql_error_code",
    "error_code", "error_type", "reason", "disposition", "payload_encoding",
    "delivery", "kdf", "rounds", "mode", "expected_schema", "response_schema",
    "schema_version", "query_revision", "endpoint", "host", "port", "validation_failure", "outcome",
    "missing_fields", "observed_fields", "expected_fields", "changed_fields",
    "acknowledgement", "consumer_decision", "ownership_match", "constraint", "lock_order",
}
_SENSITIVE_FIELD_PARTS = (
    "password", "secret", "token", "credential", "private", "certificate",
    "authorization", "cookie", "api_key", "apikey",
)
_MAX_DIAGNOSTIC_FIELDS = 12
_MAX_DIAGNOSTIC_CANDIDATES = 96
_FIELD_NAME_LISTS = {"missing_fields", "observed_fields", "expected_fields", "changed_fields"}
_DIAGNOSTIC_FIELD_PRIORITY = {
    name: index for index, name in enumerate((
        "outcome", "validation_failure", "observed_status", "upstream_status", "consumer_status",
        "status_code", "mysql_error_code", "error_code", "error_type", "sqlstate", "missing_fields",
        "observed_fields", "expected_fields", "changed_fields", "response_schema", "expected_schema",
        "schema_version", "ownership_match", "constraint", "lock_order", "acknowledgement",
        "consumer_decision", "request_key_id", "accepted_key_id", "signing_key_id", "pair_id",
        "first_sku", "second_sku", "owner_ref", "order_ref", "existing_order_ref",
        "reason", "disposition", "timeout_ms", "timeout_seconds", "duration_ms",
        "retry_count", "buffered_bytes", "max_connections", "exit_code", "status",
    ))
}


def anonymize_text(value: str, mask_numbers: bool = False, *, preserve_relations: bool = True) -> str:
    value = QUOTED_SECRET_RE.sub(lambda match: match.group(1) + '"<SECRET>"', value)
    value = BEARER_RE.sub("Bearer <SECRET>", value)
    value = URL_CREDENTIALS_RE.sub(r"\1<SECRET>@", value)
    value = TOKEN_RE.sub(lambda match: f"{match.group(1)}=<SECRET>", value)
    value = EMAIL_RE.sub("<EMAIL>", value)
    value = IP_RE.sub("<IP>", value)
    value = UUID_RE.sub(lambda match: _relation_token(match.group()) if preserve_relations else "<UUID>", value)
    value = HEX_RE.sub(lambda match: _relation_token(match.group()) if preserve_relations else "<ID>", value)
    if mask_numbers:
        value = NUMBER_RE.sub("<NUM>", value)
    return value


def template_for_message(value: str | dict[str, object], structured_fields: dict[str, object] | None = None) -> str:
    """Build a grouping template that does not split on per-event identifiers."""

    message = _message_text(value)
    template = " ".join(anonymize_text(message, mask_numbers=True, preserve_relations=False).split())
    diagnostic = {
        key: item for key, item in diagnostic_fields(message, structured_fields).items()
        if key in _GROUPING_DIAGNOSTIC_FIELDS and key not in _CORRELATION_FIELDS
    }
    return template + (" | diagnostic=" + json.dumps(diagnostic, sort_keys=True) if diagnostic else "")


def diagnostic_fields(
    value: str | dict[str, object] | None,
    structured_fields: dict[str, object] | None = None,
) -> dict[str, str]:
    """Extract bounded, allowlisted facts from JSON text or parsed log fields."""

    candidates: dict[str, object] = {}
    parsed: object = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            parsed = None
    if isinstance(parsed, dict):
        _collect_diagnostic_fields(parsed, candidates)
    if isinstance(structured_fields, dict):
        _collect_diagnostic_fields(structured_fields, candidates)
    if isinstance(value, str) and parsed is None:
        for key, canonical in _DIAGNOSTIC_ALIASES.items():
            for spelling in {key, canonical}:
                pattern = re.sub(r"[_-]+", r"[_-]?", re.escape(spelling))
                match = re.search(r"\b" + pattern + r'["\s]*[=:]\s*["\']?([\w.:-]+)', value, re.I)
                if match:
                    candidates.setdefault(canonical, match.group(1))
                    break

    result: dict[str, str] = {}
    ordered_candidates = sorted(enumerate(candidates.items()), key=lambda pair: (
        _DIAGNOSTIC_FIELD_PRIORITY.get(_canonical_diagnostic_key(pair[1][0]) or "", 100), pair[0]
    ))
    for _, (raw_key, item) in ordered_candidates:
        canonical = _canonical_diagnostic_key(raw_key)
        if not canonical or _is_sensitive_field(raw_key):
            continue
        if canonical in _FIELD_NAME_LISTS and isinstance(item, (list, tuple)):
            safe_names = []
            for name in item:
                if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", name):
                    continue
                if _is_sensitive_field(name):
                    continue
                if name not in safe_names:
                    safe_names.append(name)
                if len(safe_names) >= 8:
                    break
            if safe_names:
                result.setdefault(canonical, ",".join(safe_names))
            if len(result) >= _MAX_DIAGNOSTIC_FIELDS:
                break
            continue
        if not isinstance(item, (str, int, float, bool)):
            continue
        if isinstance(item, float) and not (float("-inf") < item < float("inf")):
            continue
        text = str(item).strip()
        if not text:
            continue
        if canonical in _VERSION_ONLY_IDENTIFIER_FIELDS:
            text = _key_version(text) or _relation_token(text)
        else:
            text = _relation_token(text) if _is_correlation_field(canonical) else anonymize_text(text)
        result.setdefault(canonical, text[:180 if canonical == "error_message" else 96])
        if len(result) >= _MAX_DIAGNOSTIC_FIELDS:
            break
    return result


def _collect_diagnostic_fields(
    value: dict[str, object], output: dict[str, object], prefix: str = "", depth: int = 0,
) -> None:
    if depth > 5:
        return
    for raw_key, item in value.items():
        key = str(raw_key)
        path = f"{prefix}.{key}" if prefix else key
        if _is_sensitive_field(path):
            continue
        if path.split(".", 1)[0].casefold() in {"kubernetes", "container", "host", "agent", "ecs"}:
            continue
        if isinstance(item, dict):
            _collect_diagnostic_fields(item, output, path, depth + 1)
            continue
        canonical = _canonical_diagnostic_key(path)
        if canonical in _FIELD_NAME_LISTS and isinstance(item, (list, tuple)):
            output.setdefault(canonical, item)
        elif canonical and isinstance(item, (str, int, float, bool)):
            output.setdefault(canonical, item)
        if len(output) >= _MAX_DIAGNOSTIC_CANDIDATES:
            return


def _canonical_diagnostic_key(value: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    leaf = re.sub(r"[^a-z0-9]", "", value.rsplit(".", 1)[-1].casefold())
    direct = _DIAGNOSTIC_ALIASES.get(normalized) or _DIAGNOSTIC_ALIASES.get(leaf)
    if direct:
        return direct
    parent = value.rsplit(".", 1)[0].rsplit(".", 1)[-1].casefold() if "." in value else ""
    if leaf in {"code", "type", "message"} and parent in {"error", "exception"}:
        return {"code": "error_code", "type": "error_type", "message": "error_message"}[leaf]
    # App-specific correlation names remain useful while their values are
    # pseudonymized. Restrict this inference to explicit ID suffixes.
    if leaf.endswith("id") and leaf not in {"kubernetesid", "containerid", "hostid", "agentid"}:
        return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")[:64]
    return None


def _is_correlation_field(value: str) -> bool:
    return value not in _VERSION_ONLY_IDENTIFIER_FIELDS and (
        value in _CORRELATION_FIELDS or value.endswith("_id") or value.endswith("id")
    )


def _key_version(value: str) -> str | None:
    """Retain only a public version suffix, never a configured key identifier."""
    match = re.search(r"(?:^|[-_.])v(?:ersion)?[-_.]?(\d{1,4})$", value, re.I)
    return "v" + match.group(1) if match else None


def _is_sensitive_field(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9_]", "_", value.casefold())
    return any(part in normalized for part in _SENSITIVE_FIELD_PARTS)


def _message_text(value: str | dict[str, object] | None) -> str:
    if isinstance(value, dict):
        for key in ("message", "log", "original", "content"):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return str(value or "")


def _relation_token(value: str) -> str:
    if RELATION_TOKEN_RE.fullmatch(value):
        return value
    digest = hmac.new(_RELATION_KEY, value.encode("utf-8"), hashlib.sha256).digest()
    token = base64.b32encode(digest[:7]).decode("ascii").rstrip("=")[:10]
    return f"<REF:{token}>"
