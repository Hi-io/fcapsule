"""Dependency-light schemas for normalized incident case validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CaseValidationError(ValueError):
    """Raised when an incident case does not satisfy the normalized contract."""


def parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CaseValidationError(f"{field} must be a non-empty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CaseValidationError(f"{field} has invalid timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        raise CaseValidationError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CaseValidationError(f"{field} must be an object")
    return value


def require_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise CaseValidationError(f"{field} must be a list")
    return value


@dataclass(frozen=True)
class CaseBundle:
    case_dir: Path
    metadata: dict[str, Any]
    alerts: list[dict[str, Any]]
    metrics: list[dict[str, Any]]
    logs: list[dict[str, Any]]
    expected_notes: str
    warnings: tuple[str, ...] = ()

    @property
    def case_id(self) -> str:
        return str(self.metadata["case_id"])

    @property
    def window_start(self) -> datetime:
        return parse_timestamp(self.metadata["window"]["start"], "metadata.window.start")

    @property
    def window_end(self) -> datetime:
        return parse_timestamp(self.metadata["window"]["end"], "metadata.window.end")

    @property
    def alert_time(self) -> datetime:
        if self.alerts:
            return parse_timestamp(self.alerts[0]["startsAt"], "alert.startsAt")
        return self.window_start + (self.window_end - self.window_start) / 2
