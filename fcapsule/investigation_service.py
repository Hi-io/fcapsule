"""Persist and schedule one investigation per correlated episode."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fcapsule.episode_investigation import now, run_investigation
from fcapsule.investigation_tools import InvestigationTools, episode_context
from fcapsule.io.archive_writer import create_archive


class InvestigationService:
    def __init__(self, plane):
        self.plane = plane
        self.jobs: set[str] = set()
        self.stopping = False

    def path(self, episode_id: str) -> Path:
        return self.plane.state_dir / "investigations" / (hashlib.sha256(episode_id.encode()).hexdigest() + ".json")

    def read(self, episode_id: str) -> dict[str, Any]:
        path = self.path(episode_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
            "episode_id": episode_id, "status": "not_started", "checks": [], "assessment": None}

    def for_incident(self, incident_id: str) -> dict[str, Any] | None:
        episode = self.plane.store.episode_for_incident(incident_id)
        return self.read(episode["episode_id"]) if episode else None

    def entries(self, episode) -> list[dict[str, Any]]:
        entries = []
        for signal in episode["signals"][-12:]:
            record = self.plane.store.get_capsule_for_incident(signal["incident_id"])
            if not record:
                continue
            root = Path(record["output_dir"])
            if not (root / "incident_report.json").is_file() or not (root / "capsule.json").is_file():
                continue
            entries.append({"incident": self.plane.store.get_incident(signal["incident_id"]), "record": record,
                "capsule": json.loads((root / "capsule.json").read_text(encoding="utf-8")),
                "report": json.loads((root / "incident_report.json").read_text(encoding="utf-8"))})
        return entries

    @staticmethod
    def fingerprint(entries) -> str:
        return hashlib.sha256(json.dumps([[item["incident"]["incident_id"], item["report"]] for item in entries],
                                          sort_keys=True).encode()).hexdigest()

    def start_by_incident(self, incident_id: str) -> dict[str, Any]:
        episode = self.plane.store.episode_for_incident(incident_id)
        return self.start(episode["episode_id"]) if episode else {}

    def start(self, episode_id: str, retry: bool = False) -> dict[str, Any]:
        with self.plane.briefing_lock:
            episode = self.plane.store.get_episode(episode_id)
            if not episode:
                raise KeyError("Episode not found")
            if episode_id in self.jobs or self.stopping:
                return self.read(episode_id)
            entries = self.entries(episode)
            if not entries:
                raise ValueError("Build at least one report before starting an investigation")
            fingerprint = self.fingerprint(entries)
            previous = self.read(episode_id)
            if not retry and previous.get("input_fingerprint") == fingerprint and previous.get("status") in {"ready", "incomplete"}:
                return previous
            state = {"version": "1", "episode_id": episode_id, "status": "queued", "queued_at": now(),
                     "input_fingerprint": fingerprint, "checks": [], "assessment": None,
                     "attempt": previous.get("attempt", 0) + 1}
            history = list(previous.get("previous_runs", []))
            if previous.get("started_at"):
                history.append({key: previous.get(key) for key in ("attempt", "started_at", "finished_at", "status", "usage", "assessment", "checks")})
            state["previous_runs"] = history[-3:]
            if previous.get("usage"):
                prior = previous.get("lifetime_usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "complete": True})
                state["lifetime_usage"] = {key: prior.get(key, 0) + previous["usage"].get(key, 0)
                                           for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
                state["lifetime_usage"]["complete"] = prior.get("complete", True) and previous["usage"].get("complete", False)
            elif previous.get("lifetime_usage"):
                state["lifetime_usage"] = previous["lifetime_usage"]
            if not self.plane.ai_configuration()["api_key_configured"]:
                state.update(status="not_configured", message="Add a provider key in Settings to enable episode investigation.")
            elif not retry and len(entries) < min(12, len(episode["signals"])):
                state.update(status="waiting", message="Waiting for the episode's reports to finish.")
            self.plane._write_briefing_state(self.path(episode_id), state)
            if state["status"] == "queued":
                self.jobs.add(episode_id)
                self.plane.briefing_executor.submit(self._run, episode_id, state)
            return state

    def resume(self) -> None:
        for episode in self.plane.store.list_episodes(limit=10000):
            if any(signal.get("report_ready") for signal in episode["signals"]):
                self.start(episode["episode_id"])

    def invalidate(self, episode_id: str) -> None:
        """Do not retain deleted member evidence in a surviving episode assessment."""
        self.path(episode_id).unlink(missing_ok=True)
        episode = self.plane.store.get_episode(episode_id)
        if episode:
            for signal in episode["signals"]:
                record = self.plane.store.get_capsule_for_incident(signal["incident_id"])
                if record:
                    root = Path(record["output_dir"])
                    (root / "episode_investigation.json").unlink(missing_ok=True)
                    create_archive(root, signal["incident_id"])

    def _run(self, episode_id: str, queued: dict[str, Any]) -> None:
        input_fingerprint = queued["input_fingerprint"]
        original_ids: set[str] = set()
        try:
            episode = self.plane.store.get_episode(episode_id)
            if not episode:
                return
            entries = self.entries(episode)
            original_ids = {item["incident"]["incident_id"] for item in entries}
            input_fingerprint = self.fingerprint(entries)
            context = episode_context(episode, entries)
            context["capture_limit"] = "At most 12 latest member reports and 80 initial evidence items; additional members remain individually accessible."
            application = self.plane.store.get_application(episode["app_id"])
            kit = InvestigationTools(entries, application or {}, self.plane.live_sources)
            config = self.plane.ai_configuration()

            def publish(state):
                with self.plane.briefing_lock:
                    current = self.plane.store.get_episode(episode_id)
                    if self.stopping or not current or not original_ids.issubset({item["incident_id"] for item in current["signals"]}):
                        raise RuntimeError("Investigation cancelled after shutdown or membership deletion")
                    state.update(input_fingerprint=input_fingerprint, attempt=queued["attempt"],
                                 lifetime_usage=queued.get("lifetime_usage", {}), previous_runs=queued.get("previous_runs", []))
                    self.plane._write_briefing_state(self.path(episode_id), state)
                    if state["status"] in {"ready", "incomplete"}:
                        for entry in entries:
                            root = Path(entry["record"]["output_dir"])
                            self.plane._write_briefing_state(root / "episode_investigation.json", state)
                            create_archive(root, entry["incident"]["incident_id"])

            run_investigation(context, kit, config["model"], config["max_tokens"], publish)
        except Exception as error:
            with self.plane.briefing_lock:
                current = self.plane.store.get_episode(episode_id)
                if not self.stopping and current and original_ids.issubset({item["incident_id"] for item in current["signals"]}):
                    state = self.read(episode_id)
                    state.update(status="incomplete", finished_at=now(), error_type=type(error).__name__,
                                 message="Investigation interrupted. Retained evidence is available; retry is explicit.")
                    self.plane._write_briefing_state(self.path(episode_id), state)
        finally:
            with self.plane.briefing_lock:
                self.jobs.discard(episode_id)
                current = self.plane.store.get_episode(episode_id)
                if current and not self.stopping and original_ids.issubset({item["incident_id"] for item in current["signals"]}):
                    fresh = self.entries(current)
                    if fresh and self.fingerprint(fresh) != input_fingerprint:
                        self.start(episode_id)
