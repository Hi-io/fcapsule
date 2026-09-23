"""Load and validate a normalized incident case."""

from __future__ import annotations

import json
import math
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


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CaseValidationError(f"{path.name} is not valid JSON: {exc}") from exc


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
    path = Path(case_dir).resolve()
    if not path.is_dir():
        raise CaseValidationError(f"Case directory does not exist: {path}")
    missing = [name for name in REQUIRED_FILES if not (path / name).is_file()]
    if missing:
        raise CaseValidationError(f"Missing required case files: {', '.join(missing)}")

    try:
        metadata_raw = yaml.safe_load((path / "metadata.yaml").read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CaseValidationError(f"metadata.yaml is invalid: {exc}") from exc

    metadata = _validate_metadata(metadata_raw)
    alerts = _validate_alerts(_read_json(path / "alert.json"))
    metrics = _validate_metrics(_read_json(path / "prometheus_metrics.json"))
    logs = _validate_logs(_read_json(path / "opensearch_logs.json"), metadata)
    notes_path = path / "expected_notes.md"
    configuration_path = path / "kubernetes_config.json"
    warnings: list[str] = []
    configurations = _validate_configurations(_read_json(configuration_path)) if configuration_path.is_file() else []
    if notes_path.is_file():
        expected_notes = notes_path.read_text(encoding="utf-8")
    else:
        expected_notes = ""
        warnings.append("expected_notes.md is missing; signal preservation uses automatic criteria only")

    return CaseBundle(
        case_dir=path,
        metadata=metadata,
        alerts=alerts,
        metrics=metrics,
        logs=logs,
        configurations=configurations,
        expected_notes=expected_notes,
        warnings=tuple(warnings),
    )
