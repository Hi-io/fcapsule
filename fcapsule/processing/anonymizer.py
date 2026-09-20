"""Deterministic masking for sample and capsule evidence."""

from __future__ import annotations

import re
import json

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,36}\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
TOKEN_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[=:]\s*[^\s,;]+")
QUOTED_SECRET_RE = re.compile(r'''(?i)(["'](?:api[_-]?key|token|secret|password|authorization)["']\s*:\s*)(["'])(?:\\.|(?!\2).)*?\2''')
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
HEX_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b")
NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?")


def anonymize_text(value: str, mask_numbers: bool = False) -> str:
    value = QUOTED_SECRET_RE.sub(lambda match: match.group(1) + '"<SECRET>"', value)
    value = BEARER_RE.sub("Bearer <SECRET>", value)
    value = TOKEN_RE.sub(lambda match: f"{match.group(1)}=<SECRET>", value)
    value = EMAIL_RE.sub("<EMAIL>", value)
    value = IP_RE.sub("<IP>", value)
    value = UUID_RE.sub("<UUID>", value)
    value = HEX_RE.sub("<ID>", value)
    if mask_numbers:
        value = NUMBER_RE.sub("<NUM>", value)
    return value


def template_for_message(value: str) -> str:
    template = " ".join(anonymize_text(value, mask_numbers=True).split())
    diagnostic = diagnostic_fields(value)
    return template + (" | diagnostic=" + json.dumps(diagnostic, sort_keys=True) if diagnostic else "")


DIAGNOSTIC_KEYS = ("exit_code", "exitCode", "errno", "status_code", "sqlstate", "reason",
                   "disposition", "payload_encoding", "max_connections", "memory_limit")


def diagnostic_fields(value: str) -> dict[str, str]:
    """Keep categorical failures and configured limits distinct, not every variable number."""
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        parsed = {}
    fields = {}
    for key in DIAGNOSTIC_KEYS:
        item = parsed.get(key) if isinstance(parsed, dict) else None
        if item is None:
            match = re.search(r'\b' + re.escape(key) + r'["\s]*[=:]\s*["\x27]?([\w.:-]+)', value)
            item = match.group(1) if match else None
        if isinstance(item, (str, int, float, bool)):
            fields[key] = anonymize_text(str(item))[:80]
    return fields
