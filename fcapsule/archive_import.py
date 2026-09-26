"""Import a verified capsule as a locally retained, read-only record."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fcapsule.io.archive_restore import restore_archive_files
from fcapsule.store import FCAPSuleStore


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Imported archive contains invalid JSON in {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Imported archive {path.name} must contain a JSON object")
    return value


def _timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Imported archive is missing {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Imported archive has an invalid {field}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"Imported archive {field} must include a timezone")
    return parsed.isoformat().replace("+00:00", "Z")


def import_capsule_archive(archive_path: str | Path, state_dir: str | Path = ".fcapsule") -> dict[str, Any]:
    """Restore and register retained evidence without rebuilding source data or publishing it."""
    archive = Path(archive_path).resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"Capsule archive not found: {archive}")

    state = Path(state_dir).resolve()
    store = FCAPSuleStore(state / "fcapsule.db")
    imported_root = state / "imported-capsules"
    imported_root.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    app_id = f"import-app-{token}"
    incident_id = f"import-incident-{token}"
    capsule_id = f"import-capsule-{token}"
    output_dir = imported_root / token

    try:
        restore_archive_files(archive, output_dir)
        required = ("capsule.json", "incident_report.json", "evidence.json")
        if any(not (output_dir / name).is_file() for name in required):
            raise ValueError("Archive must retain capsule.json, incident_report.json, and evidence.json")

        capsule_data = _read_json(output_dir / "capsule.json")
        report = _read_json(output_dir / "incident_report.json")
        if report.get("report_version") != "1.3":
            raise ValueError("Only retained incident reports at version 1.3 can be imported")
        source_incident = report.get("incident")
        case = capsule_data.get("case")
        if not isinstance(source_incident, dict) or not isinstance(case, dict):
            raise ValueError("Archive is missing its retained incident or case metadata")
        source_id = source_incident.get("incident_id")
        if not isinstance(source_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", source_id):
            raise ValueError("Archive has an unsupported source incident ID")
        window = case.get("window") if isinstance(case.get("window"), dict) else {}
        started_at = _timestamp(source_incident.get("started_at") or window.get("start"), "incident start time")
        ended_at = window.get("end")
        if ended_at:
            ended_at = _timestamp(ended_at, "incident end time")
        service = str(source_incident.get("service") or case.get("service") or "Imported service")
        namespace = str(source_incident.get("namespace") or case.get("namespace") or "unknown")
        cluster = str(source_incident.get("cluster") or case.get("cluster") or "unknown")
        selected = capsule_data.get("selected_evidence")
        if not isinstance(selected, list):
            raise ValueError("Archive capsule is missing its retained evidence references")
        evaluation = capsule_data.get("evaluation")
        if not isinstance(evaluation, dict):
            evaluation = _read_json(output_dir / "evaluation.json") if (output_dir / "evaluation.json").is_file() else {}
        log_summary = capsule_data.get("log_summary")
        log_count = int(log_summary.get("raw_lines", 0)) if isinstance(log_summary, dict) else 0
        archive_copy = output_dir / f"fcapsule_{source_id}.zip"
        shutil.copy2(archive, archive_copy)

        record = store.register_imported_capsule(
            {
                "app_id": app_id,
                "name": service,
                "namespace": namespace,
                "cluster": cluster,
            },
            {
                "incident_id": incident_id,
                "scenario": str(case.get("case_title") or "Imported capsule"),
                "severity": str(source_incident.get("severity") or "warning"),
                "started_at": started_at,
                "ended_at": ended_at,
                "case_dir": state / "source-unavailable" / incident_id,
                "alert_count": len(capsule_data.get("alerts", [])) if isinstance(capsule_data.get("alerts"), list) else 0,
                "log_count": log_count,
                "metric_series_count": len(capsule_data.get("metric_anomalies", [])) if isinstance(capsule_data.get("metric_anomalies"), list) else 0,
                "trace_access": case.get("trace_access") if isinstance(case.get("trace_access"), dict) else {},
                "summary": str(source_incident.get("summary") or source_incident.get("title") or "Imported retained capsule"),
                "resource_kind": "application",
                "resource_name": service,
            },
            {
                "capsule_id": capsule_id,
                "output_dir": output_dir,
                "archive_path": archive_copy,
                "size_bytes": (output_dir / "capsule.json").stat().st_size,
                "selected_evidence": len(selected),
                "compression": evaluation.get("log_compression_ratio", 0),
                "signal_preservation": evaluation.get("important_signal_preservation", 0),
                "grounding": evaluation.get("hypothesis_grounding_score", 0),
                "runtime_seconds": evaluation.get("runtime_seconds", 0),
            },
        )
        return record
    except sqlite3.IntegrityError as exc:
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        raise ValueError("Local import ID collision; no existing records were changed. Retry the import.") from exc
    except Exception:
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
        raise
