"""Application service coordinating incident capsules and stored metadata."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from fcapsule.env import load_env_file, write_env_value
from fcapsule.incident_report import build_incident_report
from fcapsule.io.archive_writer import create_archive
from fcapsule.io.case_loader import load_case
from fcapsule.io.output_writer import write_json
from fcapsule.pipeline import investigate_case
from fcapsule.reasoning.incident_briefing import generate_incident_briefing
from fcapsule.store import FCAPSuleStore
from fcapsule.ui.dashboard import render_dashboard


class ControlPlane:
    """Thread-safe coordinator shared by the API, Operations, and AI settings."""

    def __init__(self, state_dir: str | Path = ".fcapsule") -> None:
        self.state_dir = Path(state_dir).resolve()
        self.output_root = self.state_dir / "capsules"
        self.ai_config_path = self.state_dir / "ai-settings.json"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.store = FCAPSuleStore(self.state_dir / "fcapsule.db")
        self.lock = threading.Lock()
        self.running = False
        self.active_job: str | None = None
        self.current_incident_id: str | None = None
        self.current_capsule_id: str | None = None
        self.error: str | None = None
        self.events: list[dict[str, Any]] = []
        self.phases = self._empty_phases()
        self.live: dict[str, Any] = {}
        load_env_file()
        self._persist_ai_settings()
        existing = self.store.overview()
        if existing["incidents"]:
            incident = existing["incidents"][0]
            self.current_incident_id = incident["incident_id"]
        if existing["capsules"]:
            capsule_record = existing["capsules"][0]
            self.current_capsule_id = capsule_record["capsule_id"]
            self.phases["capsule"] = {
                "status": "done",
                "message": "Capsule ready",
                "details": {},
                "updated_at": time.time(),
            }
            output_dir = Path(capsule_record["output_dir"])
            evaluation_path = output_dir / "evaluation.json"
            if evaluation_path.is_file():
                self.live["evaluation"] = json.loads(evaluation_path.read_text(encoding="utf-8"))
                self.live["selected_evidence"] = capsule_record["selected_evidence"]

    @staticmethod
    def _empty_phases() -> dict[str, dict[str, Any]]:
        return {"capsule": {"status": "waiting", "message": "Waiting"}}

    def ai_configuration(self) -> dict[str, Any]:
        """Return local AI settings without ever returning the credential."""

        profiles = self.store.list_model_profiles()
        default = next((item for item in profiles if item["model_id"] == "deepseek-v4-pro"), profiles[0])
        model = self.store.get_setting("ai_active_model", str(default["model_id"])) or str(default["model_id"])
        try:
            max_tokens = int(self.store.get_setting("ai_max_tokens", str(default["max_tokens"])) or default["max_tokens"])
        except ValueError:
            max_tokens = int(default["max_tokens"])
        return {
            "provider": "deepseek",
            "model": model,
            "max_tokens": max_tokens,
            "api_key_configured": bool(os.environ.get("DEEPSEEK_API_KEY")),
            "models": profiles,
            "config_path": str(self.ai_config_path),
        }

    def _persist_ai_settings(self) -> None:
        config = self.ai_configuration()
        # The local config makes the selected runtime explicit; the secret stays in .env only.
        write_json(
            self.ai_config_path,
            {
                "provider": config["provider"],
                "model": config["model"],
                "max_tokens": config["max_tokens"],
            },
        )

    def update_ai_configuration(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Update a supported model selection and optionally save a local API key."""

        model = str(payload.get("model", "")).strip()
        if not model or any(character.isspace() for character in model):
            raise ValueError("Model ID must be a non-empty identifier without spaces")
        max_tokens = int(payload.get("max_tokens", 1500))
        api_key = str(payload.get("api_key", "")).strip()
        if api_key and len(api_key) < 12:
            raise ValueError("API key appears too short")
        self.store.upsert_model_profile(model, "deepseek", True, max_tokens)
        self.store.set_setting("ai_active_model", model)
        self.store.set_setting("ai_max_tokens", str(max_tokens))
        if api_key:
            write_env_value(Path.cwd() / ".env", "DEEPSEEK_API_KEY", api_key)
        self._persist_ai_settings()
        return self.ai_configuration()

    def ingest_case(
        self,
        case_dir: str | Path,
        app_id: str,
        app_name: str | None = None,
        environment: str = "development",
    ) -> dict[str, Any]:
        """Register an externally captured normalized incident without copying raw telemetry."""

        bundle = load_case(case_dir)
        metadata = bundle.metadata
        if not self.store.get_application(app_id):
            self.store.upsert_application(
                app_id,
                app_name or str(metadata["service"]),
                str(metadata["namespace"]),
                str(metadata["cluster"]),
                environment,
                source_config={
                    "faults": {"adapter": "external", "status": "connected"},
                    "metrics": {"adapter": "external", "status": "connected"},
                    "logs": {"adapter": "external", "status": "connected"},
                    "traces": {"adapter": "on_demand", "status": "available", "retain_raw_spans": False},
                },
            )
        first_alert = bundle.alerts[0]
        annotation = first_alert.get("annotations", {}) if isinstance(first_alert.get("annotations"), dict) else {}
        raw_bytes = sum(
            path.stat().st_size
            for path in bundle.case_dir.iterdir()
            if path.is_file() and path.name in {"alert.json", "prometheus_metrics.json", "opensearch_logs.json"}
        )
        incident = self.store.record_incident(
            {
                "incident_id": bundle.case_id,
                "app_id": app_id,
                "scenario": str(metadata.get("case_title", bundle.case_id)),
                "status": str(first_alert.get("status", "firing")),
                "severity": str(first_alert.get("severity", "warning")),
                "started_at": str(first_alert.get("startsAt", metadata["window"]["start"])),
                "ended_at": first_alert.get("endsAt") or metadata["window"]["end"],
                "case_dir": bundle.case_dir,
                "alert_count": len(bundle.alerts),
                "log_count": len(bundle.logs),
                "metric_series_count": len(bundle.metrics),
                "raw_bytes": raw_bytes,
                "trace_access": metadata.get("trace_access", {"available": False, "raw_spans_retained": False}),
                "summary": str(annotation.get("summary") or metadata["case_title"]),
            }
        )
        with self.lock:
            self.current_incident_id = incident["incident_id"]
        self._event("capsule", "waiting", "Incident captured; report not built", {"incident_id": incident["incident_id"]}, update_live=False)
        return incident

    def _begin(self, job: str) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.active_job = job
            self.error = None
            self.events.append({"time": time.time(), "phase": job, "status": "running", "message": f"{job.title()} started", "details": {}})
            return True

    def _finish(self, error: Exception | None = None) -> None:
        with self.lock:
            self.running = False
            self.active_job = None
            if error:
                self.error = str(error)
                self.events.append({"time": time.time(), "phase": "system", "status": "error", "message": str(error), "details": {}})
            self.events = self.events[-160:]

    def _event(
        self,
        phase: str,
        status: str,
        message: str,
        details: dict[str, Any] | None = None,
        update_live: bool = True,
    ) -> None:
        details = details or {}
        with self.lock:
            self.phases[phase] = {"status": status, "message": message, "details": details, "updated_at": time.time()}
            self.events.append({"time": time.time(), "phase": phase, "status": status, "message": message, "details": details})
            self.events = self.events[-160:]
            if update_live:
                self.live.update(details)

    def _pipeline_progress(self, status: str, message: str, details: dict[str, Any]) -> None:
        self._event("capsule", "done" if status == "done" else "running", message, details, update_live=False)

    def start_capsule(self, incident_id: str | None = None) -> bool:
        incident_id = incident_id or self.current_incident_id
        if not incident_id:
            raise ValueError("No incident is selected")
        if not self._begin("capsule"):
            return False
        with self.lock:
            self.phases["capsule"] = {"status": "running", "message": "Loading incident evidence"}
        thread = threading.Thread(target=self._run_capsule, args=(incident_id,), daemon=True)
        thread.start()
        return True

    def _run_capsule(self, incident_id: str) -> None:
        try:
            incident = self.store.get_incident(incident_id)
            if not incident:
                raise ValueError(f"Unknown incident: {incident_id}")
            output_dir = self.output_root / incident_id
            result = investigate_case(incident["case_dir"], output_dir, self._pipeline_progress)
            render_dashboard(output_dir)
            evaluation = result["evaluation"]
            capsule_id = f"capsule-{incident_id}"
            capsule_path = output_dir / "capsule.json"
            capsule_data = json.loads(capsule_path.read_text(encoding="utf-8"))
            source_metrics = load_case(incident["case_dir"]).metrics
            write_json(output_dir / "incident_report.json", build_incident_report(capsule_data, incident, source_metrics))
            archive = create_archive(output_dir, incident_id)
            capsule = self.store.record_capsule(
                {
                    "capsule_id": capsule_id,
                    "incident_id": incident_id,
                    "app_id": incident["app_id"],
                    "output_dir": output_dir,
                    "archive_path": archive,
                    "size_bytes": capsule_path.stat().st_size,
                    "selected_evidence": result["selected_evidence"],
                    "compression": evaluation["log_compression_ratio"],
                    "signal_preservation": evaluation["important_signal_preservation"],
                    "grounding": evaluation["hypothesis_grounding_score"],
                    "runtime_seconds": evaluation["runtime_seconds"],
                    "model_winner": None,
                }
            )
            with self.lock:
                self.current_capsule_id = capsule_id
                self.live.update(
                    {
                        "capsule": capsule,
                        "evaluation": evaluation,
                        "selected_evidence": result["selected_evidence"],
                    }
                )
            self._event("capsule", "done", "Capsule ready", {"selected_evidence": result["selected_evidence"], "compression": evaluation["log_compression_ratio"], "signal_preservation": evaluation["important_signal_preservation"]})
            self._finish()
        except Exception as exc:  # pragma: no cover - surfaced through API and UI
            self._finish(exc)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            state = {
                "running": self.running,
                "active_job": self.active_job,
                "current_incident_id": self.current_incident_id,
                "current_capsule_id": self.current_capsule_id,
                "error": self.error,
                "events": json.loads(json.dumps(self.events)),
                "phases": json.loads(json.dumps(self.phases)),
                "live": json.loads(json.dumps(self.live)),
                "ai": self.ai_configuration(),
            }
        state["overview"] = self.store.overview()
        return state

    def capsule_payload(self, capsule_id: str) -> dict[str, Any] | None:
        record = self.store.get_capsule(capsule_id)
        if not record:
            return None
        path = Path(record["output_dir"]) / "capsule.json"
        if not path.is_file():
            return None
        comparison_path = Path(record["output_dir"]) / "llm_comparison.json"
        comparison = json.loads(comparison_path.read_text(encoding="utf-8")) if comparison_path.is_file() else None
        return {
            "record": record,
            "capsule": json.loads(path.read_text(encoding="utf-8")),
            "comparison": self._compact_comparison(comparison),
        }

    def incident_report_payload(self, incident_id: str) -> dict[str, Any] | None:
        """Return the responder-facing report for an incident when a capsule exists."""

        incident = self.store.get_incident(incident_id)
        if not incident:
            return None
        capsule_record = next(
            (item for item in self.store.list_capsules() if item["incident_id"] == incident_id),
            None,
        )
        if not capsule_record:
            return {"incident": incident, "report": None}
        capsule_path = Path(capsule_record["output_dir"]) / "capsule.json"
        if not capsule_path.is_file():
            return {"incident": incident, "report": None}
        report_path = Path(capsule_record["output_dir"]) / "incident_report.json"
        capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
        source_metrics = load_case(incident["case_dir"]).metrics
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None
        if not report or report.get("report_version") != "1.2":
            report = build_incident_report(capsule, incident, source_metrics)
            write_json(report_path, report)
            create_archive(Path(capsule_record["output_dir"]), incident_id)
        briefing_path = Path(capsule_record["output_dir"]) / "ai_briefing.json"
        return {
            "incident": incident,
            "record": capsule_record,
            "report": report,
            "ai_briefing": json.loads(briefing_path.read_text(encoding="utf-8")) if briefing_path.is_file() else None,
        }

    def generate_ai_briefing(self, incident_id: str) -> dict[str, Any]:
        """Generate an optional LLM briefing without delaying evidence capture."""

        payload = self.incident_report_payload(incident_id)
        if not payload or not payload.get("report") or not payload.get("record"):
            raise ValueError("Build an incident report before requesting an AI briefing")
        config = self.ai_configuration()
        result = generate_incident_briefing(payload["report"], model=config["model"], max_tokens=config["max_tokens"])
        if result.get("status") == "ready":
            output_dir = Path(payload["record"]["output_dir"])
            write_json(output_dir / "ai_briefing.json", result)
            create_archive(output_dir, incident_id)
        return result

    @staticmethod
    def _compact_comparison(comparison: dict[str, Any] | None) -> dict[str, Any] | None:
        if not comparison:
            return None
        return {
            "winner": comparison.get("winner"),
            "score_delta": comparison.get("score_delta"),
            "interpretation": comparison.get("interpretation"),
            "results": [
                {
                    "model": item.get("model"),
                    "status": item.get("status"),
                    "latency_seconds": item.get("latency_seconds"),
                    "total_tokens": item.get("usage", {}).get("total_tokens"),
                    "total_score": item.get("score", {}).get("total_score"),
                    "signal_score": item.get("score", {}).get("expected_signal_score"),
                    "signal_depth_score": item.get("score", {}).get("signal_depth_score"),
                    "citation_score": item.get("score", {}).get("citation_score"),
                    "domain_score": item.get("score", {}).get("domain_score"),
                }
                for item in comparison.get("results", [])
            ],
        }
