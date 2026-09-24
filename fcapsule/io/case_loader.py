"""Load and validate a normalized incident case."""

from __future__ import annotations

import json
import math
import os
import threading
from pathlib import Path
from typing import Any

import yaml

from fcapsule.models.schemas import (
    CaseBundle,
    CaseValidationError,
    parse_timestamp,
    require_list,
    require_mapping,
)

REQUIRED_FILES = (
    "metadata.yaml",
    "alert.json",
    "prometheus_metrics.json",
    "opensearch_logs.json",
)


def _environment_limit(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return min(maximum, max(minimum, value))


MAX_CASE_INPUT_BYTES = _environment_limit("FCAPSULE_CASE_MAX_INPUT_BYTES", 16 * 1024 * 1024, 1024 * 1024, 64 * 1024 * 1024)
MAX_CASE_CACHE_INPUT_BYTES = 2 * 1024 * 1024
_CASE_CACHE_LOCK = threading.RLock()
_CASE_CACHE: tuple[Path, tuple[tuple[str, int, int], ...], CaseBundle] | None = None


def _read_text(path: Path, remaining_bytes: int) -> tuple[str, int]:
    with path.open("rb") as handle:
        content = handle.read(remaining_bytes + 1)
    if len(content) > remaining_bytes:
        raise CaseValidationError(
            f"{path.name} exceeds the remaining case input limit of {remaining_bytes} bytes"
        )
    try:
        return content.decode("utf-8"), len(content)
    except UnicodeDecodeError as exc:
        raise CaseValidationError(f"{path.name} is not valid UTF-8: {exc}") from exc


def _read_json(path: Path, remaining_bytes: int) -> tuple[Any, int]:
    text, consumed = _read_text(path, remaining_bytes)
    try:
        return json.loads(text), consumed
    except json.JSONDecodeError as exc:
        raise CaseValidationError(f"{path.name} is not valid JSON: {exc}") from exc


def read_case_json(path: str | Path, max_bytes: int = MAX_CASE_INPUT_BYTES) -> Any:
    """Read one JSON case file without exceeding the configured case input cap."""

    limit = min(MAX_CASE_INPUT_BYTES, max(1, int(max_bytes)))
    parsed, _ = _read_json(Path(path), limit)
    return parsed


def _validate_metadata(raw: Any) -> dict[str, Any]:
    metadata = require_mapping(raw, "metadata")
    for field in ("case_id", "case_title", "service", "cluster", "namespace", "window"):
        if field not in metadata:
            raise CaseValidationError(f"metadata.{field} is required")
    window = require_mapping(metadata["window"], "metadata.window")
    start = parse_timestamp(window.get("start"), "metadata.window.start")
    end = parse_timestamp(window.get("end"), "metadata.window.end")
    if end <= start:
        raise CaseValidationError("metadata.window.end must be after metadata.window.start")
    return metadata


def _validate_alerts(raw: Any) -> list[dict[str, Any]]:
    values = raw if isinstance(raw, list) else [raw]
    alerts: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        alert = require_mapping(value, f"alert[{index}]")
        for field in ("alertname", "status", "severity", "startsAt", "labels"):
            if field not in alert:
                raise CaseValidationError(f"alert[{index}].{field} is required")
        parse_timestamp(alert["startsAt"], f"alert[{index}].startsAt")
        if alert.get("endsAt"):
            parse_timestamp(alert["endsAt"], f"alert[{index}].endsAt")
        require_mapping(alert["labels"], f"alert[{index}].labels")
        alerts.append(alert)
    return alerts


def _validate_metrics(raw: Any) -> list[dict[str, Any]]:
    root = require_mapping(raw, "prometheus_metrics")
    series = require_list(root.get("series"), "prometheus_metrics.series")
    for index, item in enumerate(series):
        metric = require_mapping(item, f"series[{index}]")
        if not isinstance(metric.get("metric"), str):
            raise CaseValidationError(f"series[{index}].metric is required")
        require_mapping(metric.get("labels", {}), f"series[{index}].labels")
        values = require_list(metric.get("values"), f"series[{index}].values")
        alert_series = metric.get("signal_origin") == "alert_rule"
        if len(values) < (1 if alert_series else 2):
            raise CaseValidationError(f"series[{index}].values requires at least two points")
        for point_index, point in enumerate(values):
            if not isinstance(point, list) or len(point) != 2:
                raise CaseValidationError(f"series[{index}].values[{point_index}] must be [timestamp, value]")
            parse_timestamp(point[0], f"series[{index}].values[{point_index}][0]")
            if point[1] is None and alert_series:
                continue
            if isinstance(point[1], bool) or not isinstance(point[1], (int, float)) or not math.isfinite(point[1]):
                raise CaseValidationError(f"series[{index}].values[{point_index}][1] must be numeric")
    return series


def _validate_logs(raw: Any, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    root = require_mapping(raw, "opensearch_logs")
    logs = require_list(root.get("hits"), "opensearch_logs.hits")
    fields = metadata.get("fields", {})
    time_field = fields.get("log_time_field", "@timestamp")
    message_field = fields.get("log_message_field", "message")
    for index, value in enumerate(logs):
        event = require_mapping(value, f"hits[{index}]")
        parse_timestamp(event.get(time_field), f"hits[{index}].{time_field}")
        if not isinstance(event.get(message_field), str):
            raise CaseValidationError(f"hits[{index}].{message_field} must be a string")
    return logs


def _validate_configurations(raw: Any) -> list[dict[str, Any]]:
    root = require_mapping(raw, "kubernetes_config")
    items = require_list(root.get("items"), "kubernetes_config.items")
    for index, value in enumerate(items):
        item = require_mapping(value, f"configuration[{index}]")
        for field in ("kind", "name", "namespace"):
            if not isinstance(item.get(field), str) or not item[field]:
                raise CaseValidationError(f"configuration[{index}].{field} is required")
    return items


def load_case(case_dir: str | Path) -> CaseBundle:
    global _CASE_CACHE
    path = Path(case_dir).resolve()
    if not path.is_dir():
        raise CaseValidationError(f"Case directory does not exist: {path}")
    missing = [name for name in REQUIRED_FILES if not (path / name).is_file()]
    if missing:
        raise CaseValidationError(f"Missing required case files: {', '.join(missing)}")

    file_names = [*REQUIRED_FILES, "kubernetes_config.json", "expected_notes.md"]
    fingerprint, input_bytes = _case_fingerprint(path, file_names)
    if input_bytes > MAX_CASE_INPUT_BYTES:
        raise CaseValidationError(
            f"Case input is {input_bytes} bytes; the total limit is {MAX_CASE_INPUT_BYTES} bytes"
        )
    with _CASE_CACHE_LOCK:
        if _CASE_CACHE and _CASE_CACHE[:2] == (path, fingerprint):
            # CaseBundle is frozen and the application treats nested evidence as read-only.
            return _CASE_CACHE[2]

        remaining_bytes = MAX_CASE_INPUT_BYTES
        metadata_text, consumed = _read_text(path / "metadata.yaml", remaining_bytes)
        remaining_bytes -= consumed
        try:
            metadata_raw = yaml.safe_load(metadata_text)
        except yaml.YAMLError as exc:
            raise CaseValidationError(f"metadata.yaml is invalid: {exc}") from exc
        del metadata_text
        metadata = _validate_metadata(metadata_raw)

        alerts_raw, consumed = _read_json(path / "alert.json", remaining_bytes)
        remaining_bytes -= consumed
        alerts = _validate_alerts(alerts_raw)
        metrics_raw, consumed = _read_json(path / "prometheus_metrics.json", remaining_bytes)
        remaining_bytes -= consumed
        metrics = _validate_metrics(metrics_raw)
        logs_raw, consumed = _read_json(path / "opensearch_logs.json", remaining_bytes)
        remaining_bytes -= consumed
        logs = _validate_logs(logs_raw, metadata)
        capture = logs_raw.get("capture") if isinstance(logs_raw, dict) else None
        if capture is not None and not isinstance(capture, dict):
            raise CaseValidationError("opensearch_logs.capture must be an object")
        del logs_raw
        notes_path = path / "expected_notes.md"
        configuration_path = path / "kubernetes_config.json"
        warnings: list[str] = []
        if configuration_path.is_file():
            configurations_raw, consumed = _read_json(configuration_path, remaining_bytes)
            remaining_bytes -= consumed
            configurations = _validate_configurations(configurations_raw)
        else:
            configurations = []
        if notes_path.is_file():
            expected_notes, consumed = _read_text(notes_path, remaining_bytes)
            remaining_bytes -= consumed
        else:
            expected_notes = ""
            warnings.append("expected_notes.md is missing; signal preservation uses automatic criteria only")

        truncated_messages = sum(bool(item.get("message_truncated")) for item in logs)
        if truncated_messages:
            warnings.append(f"{truncated_messages} captured log message(s) were truncated by configured byte limits")
        if capture and capture.get("status") in {"partial", "unavailable", "unreported"}:
            unavailable = capture.get("unavailable_segments", [])
            segments = sorted({str(item.get("segment", "unknown")) for item in unavailable if isinstance(item, dict)})
            detail = f"; unavailable segments: {', '.join(segments)}" if segments else ""
            if capture["status"] == "unreported":
                warnings.append("OpenSearch log capture coverage metadata was not reported")
            else:
                warnings.append(f"OpenSearch log capture is {capture['status']}{detail}")

        bundle = CaseBundle(
            case_dir=path,
            metadata=metadata,
            alerts=alerts,
            metrics=metrics,
            logs=logs,
            configurations=configurations,
            expected_notes=expected_notes,
            warnings=tuple(warnings),
        )
        current_fingerprint, current_input_bytes = _case_fingerprint(path, file_names)
        if current_fingerprint == fingerprint and current_input_bytes <= MAX_CASE_CACHE_INPUT_BYTES:
            _CASE_CACHE = (path, fingerprint, bundle)
        return bundle


def _case_fingerprint(path: Path, file_names: list[str]) -> tuple[tuple[tuple[str, int, int], ...], int]:
    fingerprint: list[tuple[str, int, int]] = []
    total_bytes = 0
    for name in file_names:
        candidate = path / name
        if not candidate.is_file():
            continue
        stat = candidate.stat()
        fingerprint.append((name, stat.st_size, stat.st_mtime_ns))
        total_bytes += stat.st_size
    return tuple(fingerprint), total_bytes
