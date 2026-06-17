"""Deterministic masking for sample and capsule evidence."""

from __future__ import annotations

import re

IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,36}\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
TOKEN_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[=:]\s*[^\s,;]+")
HEX_RE = re.compile(r"\b[0-9a-fA-F]{16,}\b")
NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?")


def anonymize_text(value: str, mask_numbers: bool = False) -> str:
    value = TOKEN_RE.sub(lambda match: f"{match.group(1)}=<SECRET>", value)
    value = EMAIL_RE.sub("<EMAIL>", value)
    value = IP_RE.sub("<IP>", value)
    value = UUID_RE.sub("<UUID>", value)
    value = HEX_RE.sub("<ID>", value)
    if mask_numbers:
        value = NUMBER_RE.sub("<NUM>", value)
    return value


def template_for_message(value: str) -> str:
    return " ".join(anonymize_text(value, mask_numbers=True).split())
