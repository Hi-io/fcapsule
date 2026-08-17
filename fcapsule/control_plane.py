"""Application service coordinating simulations, capsules, and stored metadata."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from demo.incident_lab import SimulationConfig, run_simulation
from fcapsule.env import load_env_file
from fcapsule.pipeline import investigate_case
from fcapsule.reasoning.llm_client import LLMUnavailableError
from fcapsule.reasoning.model_comparator import compare_models
from fcapsule.store import FCAPSuleStore
from fcapsule.ui.dashboard import render_dashboard


class ControlPlane:
    """Thread-safe coordinator shared by the API and both web views."""

    def __init__(self, state_dir: str | Path = ".fcapsule") -> None:
        self.state_dir = Path(state_dir).resolve()
        self.case_root = self.state_dir / "cases"
        self.output_root = self.state_dir / "capsules"
        self.case_root.mkdir(parents=True, exist_ok=True)
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

    @staticmethod
    def _empty_phases() -> dict[str, dict[str, Any]]:
        return {
            "services": {"status": "waiting", "message": "Waiting"},
            "baseline": {"status": "waiting", "message": "Waiting"},
            "injection": {"status": "waiting", "message": "Waiting"},
            "alerts": {"status": "waiting", "message": "Waiting"},
            "capsule": {"status": "waiting", "message": "Waiting"},
            "models": {"status": "waiting", "message": "Waiting"},
        }

    def reset_live(self) -> None:
        with self.lock:
            if self.running:
                raise RuntimeError("A job is running")
            self.current_incident_id = None
            self.current_capsule_id = None
            self.error = None
            self.events = []
            self.phases = self._empty_phases()
            self.live = {}

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

    def _event(self, phase: str, status: str, message: str, details: dict[str, Any] | None = None) -> None:
        details = details or {}
        with self.lock:
            self.phases[phase] = {"status": status, "message": message, "details": details, "updated_at": time.time()}
            self.events.append({"time": time.time(), "phase": phase, "status": status, "message": message, "details": details})
            self.events = self.events[-160:]
            self.live.update(details)

    def _simulation_progress(self, status: str, message: str, details: dict[str, Any]) -> None:
        lower = message.lower()
        if "starting" in lower or "healthy" in lower:
            phase = "services"
        elif "baseline" in lower:
            phase = "baseline"
        elif "injected" in lower:
            phase = "injection"
        elif "incident traffic" in lower or "trace access" in lower or "case captured" in lower:
            phase = "alerts"
        else:
            phase = "services"
        phase_status = "done" if status == "done" or "captured" in lower or "injected" in lower else "running"
        if phase == "baseline" and details.get("completed") == details.get("total"):
            phase_status = "done"
        if phase == "services" and "running baseline" in lower:
            self._event("services", "done", "Services healthy", details)
            phase = "baseline"
        self._event(phase, phase_status, message, details)

    def _pipeline_progress(self, status: str, message: str, details: dict[str, Any]) -> None:
        self._event("capsule", "done" if status == "done" else "running", message, details)

    def start_simulation(self, payload: dict[str, Any]) -> bool:
        if not self._begin("simulation"):
            return False
        with self.lock:
            self.phases = self._empty_phases()
            self.current_incident_id = None
            self.current_capsule_id = None
            self.live = {}
        thread = threading.Thread(target=self._run_simulation, args=(payload,), daemon=True)
        thread.start()
        return True

    def _run_simulation(self, payload: dict[str, Any]) -> None:
        try:
            config = SimulationConfig(
                app_id=str(payload.get("app_id", "checkout-platform")).strip(),
                app_name=str(payload.get("app_name", "Checkout Platform")).strip(),
                baseline_requests=int(payload.get("baseline_requests", 180)),
                incident_requests=int(payload.get("incident_requests", 240)),
                concurrency=int(payload.get("concurrency", 24)),
                scenario=str(payload.get("scenario", "inventory-lock-contention")),
            )
            config.validate()
            self.store.upsert_application(
                config.app_id,
                config.app_name,
                "commerce",
                "local-lab",
                environment="simulation",
                status="healthy",
                source_config={
                    "faults": {"adapter": "alertmanager-compatible", "status": "connected"},
                    "metrics": {"adapter": "prometheus-compatible", "status": "connected"},
                    "logs": {"adapter": "opensearch-compatible", "status": "connected"},
                    "traces": {"adapter": "on-demand", "status": "available", "retain_raw_spans": False},
                },
            )
            pending_dir = self.case_root / f"pending-{int(time.time())}"
            result = run_simulation(pending_dir, config, self._simulation_progress)
            final_dir = self.case_root / result["incident_id"]
            pending_dir.rename(final_dir)
            result["case_dir"] = str(final_dir)
            self.store.record_incident(result)
            with self.lock:
                self.current_incident_id = result["incident_id"]
                self.live.update(result)
            self._event("alerts", "done", "Fault sequence captured", {"alert_count": result["alert_count"], "log_count": result["log_count"], "metric_series_count": result["metric_series_count"]})
            self._finish()
        except Exception as exc:  # pragma: no cover - surfaced through API and UI
            self._finish(exc)

    def start_capsule(self, incident_id: str | None = None) -> bool:
        incident_id = incident_id or self.current_incident_id
        if not incident_id:
            raise ValueError("No incident is selected")
        if not self._begin("capsule"):
            return False
        with self.lock:
            self.phases["capsule"] = {"status": "running", "message": "Loading incident evidence"}
            self.phases["models"] = {"status": "waiting", "message": "Waiting for capsule"}
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
            comparison = None
            enabled_models = [item for item in self.store.list_model_profiles() if item["enabled"]]
            if enabled_models and os.environ.get("DEEPSEEK_API_KEY"):
                models = [item["model_id"] for item in enabled_models]
                max_tokens = max(int(item["max_tokens"]) for item in enabled_models)

                def model_progress(status: str, message: str, details: dict[str, Any]) -> None:
                    self._event("models", "done" if status == "done" and "comparison" in message.lower() else "running", message, details)

                try:
                    comparison = compare_models(
                        output_dir / "capsule.json",
                        output_dir,
                        models,
                        max_tokens=max_tokens,
                        progress=model_progress,
                    )
                except LLMUnavailableError as exc:
                    self._event("models", "error", str(exc), {})
            else:
                reason = "No enabled model" if not enabled_models else "API key not configured"
                self._event("models", "skipped", reason, {})

            evaluation = result["evaluation"]
            capsule_id = f"capsule-{incident_id}"
            capsule_path = output_dir / "capsule.json"
            capsule = self.store.record_capsule(
                {
                    "capsule_id": capsule_id,
                    "incident_id": incident_id,
                    "app_id": incident["app_id"],
                    "output_dir": output_dir,
                    "archive_path": result["archive"],
                    "size_bytes": capsule_path.stat().st_size,
                    "selected_evidence": result["selected_evidence"],
                    "compression": evaluation["log_compression_ratio"],
                    "signal_preservation": evaluation["important_signal_preservation"],
                    "grounding": evaluation["hypothesis_grounding_score"],
                    "runtime_seconds": evaluation["runtime_seconds"],
                    "model_winner": comparison.get("winner") if comparison else None,
                }
            )
            with self.lock:
                self.current_capsule_id = capsule_id
                self.live.update({"capsule": capsule, "evaluation": evaluation, "selected_evidence": result["selected_evidence"]})
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
                "api_key_available": bool(os.environ.get("DEEPSEEK_API_KEY")),
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
        return {"record": record, "capsule": json.loads(path.read_text(encoding="utf-8"))}
