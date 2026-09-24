"""Persist and schedule one investigation per correlated episode."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from fcapsule.episode_investigation import now, run_investigation
from fcapsule.investigation_tools import InvestigationTools, episode_context, historical_episode_result
from fcapsule.io.archive_writer import create_archive
from fcapsule.reasoning.findings import derive_findings
from fcapsule.reasoning.source_review import run_source_disconnected_review


class InvestigationService:
    def __init__(self, plane):
        self.plane = plane
        self.jobs: set[str] = set()
        self.source_review_jobs: set[str] = set()
        self.stopping = False

    def path(self, episode_id: str) -> Path:
        return self.plane.state_dir / "investigations" / (hashlib.sha256(episode_id.encode()).hexdigest() + ".json")

    def revision_path(self, episode_id: str, revision_id: str) -> Path:
        directory = self.plane.state_dir / "investigations" / hashlib.sha256(episode_id.encode()).hexdigest()
        return directory / (hashlib.sha256(revision_id.encode()).hexdigest() + ".json")

    def source_review_path(self, episode_id: str, review_id: str) -> Path:
        directory = self.plane.state_dir / "source-disconnected-reviews" / hashlib.sha256(episode_id.encode()).hexdigest()
        return directory / (hashlib.sha256(review_id.encode()).hexdigest() + ".json")

    def read(self, episode_id: str) -> dict[str, Any]:
        path = self.path(episode_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
            "episode_id": episode_id, "status": "not_started", "checks": [], "assessment": None}

    def revisions(self, episode_id: str) -> list[dict[str, Any]]:
        return self.plane.store.list_investigation_revisions(episode_id)

    def source_disconnected_reviews(self, episode_id: str) -> list[dict[str, Any]]:
        reviews = self.plane.store.list_source_disconnected_reviews(episode_id)
        for review in reviews:
            path = self.source_review_path(episode_id, str(review["review_id"]))
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(state, dict):
                review.update({key: state[key] for key in
                               ("retained_context", "retained_checks", "model_context", "available_evidence_ids") if key in state})
        return reviews

    def _record_revision(self, state: dict[str, Any]) -> None:
        revision_id = str(state.get("revision_id") or "")
        episode_id = str(state.get("episode_id") or "")
        if not revision_id or not episode_id:
            return
        path = self.revision_path(episode_id, revision_id)
        self.plane._write_briefing_state(path, state)
        assessment = state.get("assessment") if isinstance(state.get("assessment"), dict) else {}
        self.plane.store.record_investigation_revision(
            {
                "revision_id": revision_id,
                "episode_id": episode_id,
                "parent_revision_id": state.get("parent_revision_id"),
                "reason": state.get("revision_reason", "initial_capture"),
                "source_mode": state.get("source_mode", "live_sources"),
                "input_fingerprint": state.get("input_fingerprint", ""),
                "status": state.get("status", "queued"),
                "evidence_manifest": state.get("evidence_manifest", []),
                "state_path": str(path),
                "summary": {key: assessment.get(key) for key in ("summary", "likely_mechanism", "uncertainty") if assessment.get(key)},
                "created_at": state.get("queued_at") or state.get("started_at") or now(),
                "completed_at": state.get("finished_at") if state.get("status") in {"ready", "incomplete", "inconclusive", "not_configured"} else None,
            }
        )

    def _write_revision_exports(self, episode_id: str) -> None:
        episode = self.plane.store.get_episode(episode_id)
        if not episode:
            return
        history = {
            "episode_id": episode_id,
            "generated_at": now(),
            "revisions": self.revisions(episode_id),
            "source_disconnected_reviews": self.source_disconnected_reviews(episode_id),
        }
        revision_details = []
        exported_keys = (
            "revision_id", "parent_revision_id", "revision_reason", "source_mode", "status", "queued_at",
            "started_at", "finished_at", "model", "policy_version", "message", "validation_error",
            "assessment", "checks", "calls", "review", "findings", "usage", "token_budget",
            "investigation_contract", "evidence_manifest", "input_fingerprint",
        )
        for revision in history["revisions"]:
            path = Path(str(revision.get("state_path") or ""))
            if not path.is_file():
                continue
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            revision_details.append({key: saved.get(key) for key in exported_keys if key in saved})
        revisions_export = {
            "episode_id": episode_id,
            "generated_at": history["generated_at"],
            "revisions": revision_details,
            "source_disconnected_reviews": history["source_disconnected_reviews"],
            "raw_media_included": False,
        }
        for signal in episode["signals"]:
            record = self.plane.store.get_capsule_for_incident(str(signal["incident_id"]))
            if not record:
                continue
            root = Path(record["output_dir"])
            self.plane._write_briefing_state(root / "investigation_history.json", history)
            self.plane._write_briefing_state(root / "investigation_revisions.json", revisions_export)
            create_archive(root, str(signal["incident_id"]))

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
            try:
                capsule = json.loads((root / "capsule.json").read_text(encoding="utf-8"))
                report = json.loads((root / "incident_report.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            incident = self.plane.store.get_incident(signal["incident_id"])
            if incident and isinstance(capsule, dict) and isinstance(report, dict):
                entries.append({"incident": incident, "record": record, "capsule": capsule, "report": report})
        return entries

    def historical_candidates(self, episode: dict[str, Any], primary_incident_id: str | None = None) -> list[dict[str, Any]]:
        """Bound prior recurrence evidence before exposing it to the investigator."""

        current_id = primary_incident_id or episode.get("primary_incident_id")
        current = next((item for item in episode.get("signals", []) if item["incident_id"] == current_id), {})
        if not current:
            return []
        identity = current.get("recurrence_key")
        summaries = self.plane.store.recurrence_candidates_for_incident(episode["episode_id"], current_id)
        candidates = []
        for summary in summaries[:3]:
            prior = self.plane.store.get_episode(str(summary["episode_id"]))
            if not prior or prior.get("app_id") != episode.get("app_id"):
                continue
            # Select from stored identities before loading bounded artifacts. A
            # matching older member must not disappear behind newer other alerts.
            signals = sorted(prior.get("signals", []),
                             key=lambda item: (item.get("started_at", ""), item["incident_id"]), reverse=True)
            matches = {item["incident_id"] for item in signals if identity and item.get("recurrence_key") == identity}
            signals.sort(key=lambda item: item["incident_id"] not in matches)
            selected = signals[:4]
            prior_entries = self.entries({**prior, "signals": selected})
            loaded = {item["incident"]["incident_id"] for item in prior_entries}
            matching_entries = [item for item in prior_entries if item["incident"]["incident_id"] in matches]
            selection = {
                "policy": "exact_stored_scope_alert_identity_then_recency",
                "current_incident_id": current_id, "identity_available": bool(identity),
                "matching_member_count": len(matches), "retained_matching_member_count": len(matching_entries),
                "omitted_member_count": max(0, len(signals) - len(selected)),
                "selected_members": [{"incident_id": item["incident_id"],
                    "matches_current_alert_identity": item["incident_id"] in matches,
                    "capture_available": item["incident_id"] in loaded} for item in selected],
                "limitation": "Identity match is not a cause match. Missing matching captures cannot be replaced by other members or prior diagnoses.",
            }
            # Keep the missing selection explicit, not a comparison of unrelated
            # members or episode-level checks presented as a matching capture.
            if not matching_entries:
                prior_entries = []
            evidence = []
            for entry in prior_entries:
                report = entry["report"]
                evidence.append(
                    {
                        "incident_id": entry["incident"]["incident_id"],
                        "reference": entry["incident"].get("reference"),
                        "alerts": report.get("fault_alerts", [])[:3],
                        "impact": report.get("impact", [])[:3],
                        "log_patterns": report.get("log_patterns", [])[:3],
                        "configuration": report.get("configuration_evidence", [])[:3],
                    }
                )
            try:
                prior_run = self.read(str(prior["episode_id"]))
            except (OSError, ValueError):
                prior_run = {}
            # Use the same retained observation projection as the current episode:
            # display-only impact/configuration summaries omit measured rule values.
            observations = episode_context(prior, prior_entries,
                matching_entries[0]["incident"]["incident_id"])["evidence"] if matching_entries else []
            retained_checks = [item for item in (prior_run.get("checks", []) if isinstance(prior_run, dict) else [])
                               if isinstance(item, dict) and item.get("status") == "completed"
                               and item.get("tool") in InvestigationTools.CATALOG
                               and item.get("tool") != "historical_episode"][-4:] if matching_entries else []
            assessment = prior_run.get("assessment") if isinstance(prior_run, dict) else None
            assessment = assessment if isinstance(assessment, dict) else {}
            prior_hypothesis = {
                key: assessment.get(key)
                for key in ("summary", "likely_mechanism", "uncertainty")
                if assessment.get(key)
            }
            candidates.append(
                {
                    "episode_id": prior["episode_id"],
                    "reference": prior.get("reference"),
                    "title": prior["title"],
                    "started_at": prior["started_at"],
                    "ended_at": prior.get("ended_at"),
                    "status": prior["status"],
                    "resource": prior.get("resource"),
                    "member_selection": selection,
                    "observations": observations,
                    "retained_checks": retained_checks,
                    "availability": "retained" if observations or retained_checks else "unavailable",
                    "capture_limit": "At most four selected member reports, exact stored scope/alert identity first, then recent other members; 80 observations and four episode-level source checks. Missing matching captures remain unavailable, not substituted by other members.",
                    "prior_hypothesis": (
                        {"provenance": "Earlier model output; not independent evidence and not citable.", **prior_hypothesis}
                        if prior_hypothesis and matching_entries else {}
                    ),
                    "captured_evidence": evidence,
                }
            )
        return candidates

    @staticmethod
    def fingerprint(entries, evidence_manifest: list[dict[str, Any]] | None = None,
                    primary_incident_id: str | None = None) -> str:
        payload = {
            "reports": [[item["incident"]["incident_id"], item["report"]] for item in entries],
            "attachments": evidence_manifest or [],
            "primary_incident_id": primary_incident_id or "",
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def start_by_incident(self, incident_id: str) -> dict[str, Any]:
        episode = self.plane.store.episode_for_incident(incident_id)
        return self.start(episode["episode_id"], primary_incident_id=incident_id) if episode else {}

    def start_source_disconnected_review(self, episode_id: str, question: str) -> dict[str, Any]:
        """Ask one bounded question without passing a live-source adapter to the worker."""

        with self.plane.briefing_lock:
            episode = self.plane.store.get_episode(episode_id)
            if not episode:
                raise KeyError("Episode not found")
            clean_question = str(question or "").strip()
            if not 1 <= len(clean_question) <= 500:
                raise ValueError("Question must contain between 1 and 500 characters")
            entries = self.entries(episode)
            if not entries:
                raise ValueError("Build at least one report before asking a retained-capsule question")
            if not self.plane.ai_configuration()["api_key_configured"]:
                raise ValueError("Add a provider key in Settings to review a retained capsule")
            context, checks = self._retained_review_context(episode_id)
            fingerprint = hashlib.sha256(json.dumps({
                "base": self.fingerprint(entries, self.plane.evidence.manifest(episode_id)),
                "question": clean_question,
                "context": context,
                "checks": checks,
            }, sort_keys=True).encode()).hexdigest()
            for existing in self.source_disconnected_reviews(episode_id):
                if existing.get("input_fingerprint") == fingerprint and existing.get("status") in {"queued", "running", "ready"}:
                    return existing
            review_id = f"review-{uuid.uuid4().hex}"
            config = self.plane.ai_configuration()
            state = {
                "review_id": review_id, "episode_id": episode_id, "question": clean_question,
                "input_fingerprint": fingerprint, "source_mode": "retained_only", "status": "queued",
                "model": config["model"], "created_at": now(), "result": None,
            }
            self._write_source_review(state)
            self.source_review_jobs.add(review_id)
            self.plane.briefing_executor.submit(self._run_source_disconnected_review, episode_id, state, context, checks)
            return state

    def _write_source_review(self, state: dict[str, Any]) -> None:
        path = self.source_review_path(str(state["episode_id"]), str(state["review_id"]))
        self.plane._write_briefing_state(path, state)
        self.plane.store.record_source_disconnected_review(
            {
                "review_id": state["review_id"], "episode_id": state["episode_id"],
                "question": state["question"], "input_fingerprint": state["input_fingerprint"],
                "model": state["model"], "status": state["status"], "state_path": str(path),
                "result": state.get("result") or {}, "usage": state.get("usage") or {},
                "created_at": state.get("created_at") or state.get("started_at") or now(),
                "completed_at": state.get("finished_at") if state.get("status") in {"ready", "incomplete"} else None,
            }
        )

    def _retained_review_context(self, episode_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        episode = self.plane.store.get_episode(episode_id)
        if not episode:
            raise KeyError("Episode not found")
        entries = self.entries(episode)
        if not entries:
            raise ValueError("No retained reports are available for this episode")
        context = episode_context(episode, entries)
        context["evidence"].extend(self.plane.evidence.model_evidence(episode_id))
        latest = self.read(episode_id)
        checks = [item for item in latest.get("checks", []) if isinstance(item, dict)
                  and item.get("status") == "completed" and item.get("tool") != "historical_episode"]
        # Resolve candidates again from retained artifacts, not stale historical
        # checks or an earlier assessment. No live-source object is involved.
        used_ids = {str(item.get("id")) for item in checks}
        for candidate in self.historical_candidates(episode):
            number = 1
            while f"Q{number:03d}" in used_ids:
                number += 1
            check_id = f"Q{number:03d}"
            used_ids.add(check_id)
            checks.append({
                "id": check_id, "tool": "historical_episode", "status": "completed",
                "required_observation": True,
                "arguments": {"episode_id": candidate["episode_id"]},
                "result": historical_episode_result(candidate, include_hypothesis=False),
            })
        context["capture_limit"] = "Retained records only. No live telemetry, source API, or prior assessment is available to this review."
        return context, checks

    def _run_source_disconnected_review(self, episode_id: str, queued: dict[str, Any],
                                        context: dict[str, Any] | None = None,
                                        checks: list[dict[str, Any]] | None = None) -> None:
        review_id = str(queued["review_id"])
        original_ids: set[str] = set()
        try:
            if context is None or checks is None:
                context, checks = self._retained_review_context(episode_id)
            original_ids = {str(item["incident_id"]) for item in context.get("alerts", [])}
            config = self.plane.ai_configuration()

            def publish(state: dict[str, Any]) -> None:
                with self.plane.briefing_lock:
                    current = self.plane.store.get_episode(episode_id)
                    if self.stopping or not current or not original_ids.issubset({item["incident_id"] for item in current["signals"]}):
                        raise RuntimeError("Source-disconnected review cancelled after shutdown or deletion")
                    state.update(review_id=review_id, episode_id=episode_id, question=queued["question"],
                                 input_fingerprint=queued["input_fingerprint"], model=config["model"],
                                 created_at=queued["created_at"], source_mode="retained_only")
                    self._write_source_review(state)
                    if state.get("status") in {"ready", "incomplete"}:
                        self._write_revision_exports(episode_id)

            run_source_disconnected_review(
                context, checks, str(queued["question"]), config["model"],
                min(config["max_tokens"], 700), publish,
                max_prompt_tokens=min(config["max_prompt_tokens"], 2200),
                max_total_tokens=min(config["max_total_tokens"], 3500),
            )
        except Exception as error:
            with self.plane.briefing_lock:
                current = self.plane.store.get_episode(episode_id)
                if self.stopping or not current or not original_ids.issubset({item["incident_id"] for item in current["signals"]}):
                    return
                state = dict(queued)
                state.update(status="incomplete", finished_at=now(), error_type=type(error).__name__,
                             message="The retained-capsule review could not complete. Try again explicitly.")
                self._write_source_review(state)
                self._write_revision_exports(episode_id)
        finally:
            with self.plane.briefing_lock:
                self.source_review_jobs.discard(review_id)

    def start(self, episode_id: str, retry: bool = False, reason: str = "initial_capture",
              source_mode: str = "live_sources", primary_incident_id: str | None = None) -> dict[str, Any]:
        with self.plane.briefing_lock:
            episode = self.plane.store.get_episode(episode_id)
            if not episode:
                raise KeyError("Episode not found")
            if episode_id in self.jobs or self.stopping:
                return self.read(episode_id)
            entries = self.entries(episode)
            if not entries:
                raise ValueError("Build at least one report before starting an investigation")
            if primary_incident_id is None and episode.get("primary_incident_id") in {
                item["incident"]["incident_id"] for item in entries
            }:
                # Manual media updates and startup discovery refer to the same
                # primary report; do not bill a fresh run solely on restart.
                primary_incident_id = episode["primary_incident_id"]
            if primary_incident_id and primary_incident_id not in {item["incident"]["incident_id"] for item in entries}:
                raise ValueError("Primary incident is not a member of this episode")
            evidence_manifest = self.plane.evidence.manifest(episode_id)
            fingerprint = self.fingerprint(entries, evidence_manifest, primary_incident_id)
            previous = self.read(episode_id)
            if any(call.get("status") == "running" for call in previous.get("calls", [])):
                previous.setdefault("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})["complete"] = False
            if not retry and previous.get("input_fingerprint") == fingerprint and previous.get("status") in {"ready", "incomplete", "inconclusive"}:
                return previous
            revision_id = f"revision-{uuid.uuid4().hex}"
            state = {"version": "1", "episode_id": episode_id, "revision_id": revision_id,
                     "parent_revision_id": previous.get("revision_id"), "revision_reason": reason,
                     "source_mode": source_mode, "status": "queued", "queued_at": now(),
                     "input_fingerprint": fingerprint, "checks": [], "assessment": None,
                     "attempt": previous.get("attempt", 0) + 1, "evidence_manifest": evidence_manifest,
                     "primary_incident_id": primary_incident_id}
            history = list(previous.get("previous_runs", []))
            if previous.get("started_at"):
                history.append({key: previous.get(key) for key in ("attempt", "started_at", "finished_at", "status", "usage", "assessment", "checks", "calls", "draft_assessment", "review", "policy_version")})
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
            self._record_revision(state)
            if state["status"] == "queued":
                self.jobs.add(episode_id)
                self.plane.briefing_executor.submit(self._run, episode_id, state)
            return state

    def resume(self) -> None:
        for episode in self.plane.store.list_episodes(limit=10000):
            if any(signal.get("report_ready") for signal in episode["signals"]):
                previous = self.read(episode["episode_id"])
                if previous.get("status") in {"ready", "incomplete", "inconclusive"}:
                    entries = self.entries(episode)
                    manifest = self.plane.evidence.manifest(episode["episode_id"])
                    # Older manual revisions fingerprinted an implicit primary as
                    # None. Keep completed work when its inputs are unchanged.
                    if previous.get("input_fingerprint") == self.fingerprint(
                        entries, manifest, previous.get("primary_incident_id")
                    ):
                        continue
                self.start(episode["episode_id"], primary_incident_id=episode.get("primary_incident_id"))

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
                    (root / "investigation_history.json").unlink(missing_ok=True)
                    (root / "investigation_revisions.json").unlink(missing_ok=True)
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
            evidence_manifest = self.plane.evidence.manifest(episode_id)
            primary_incident_id = queued.get("primary_incident_id")
            input_fingerprint = self.fingerprint(entries, evidence_manifest, primary_incident_id)
            context = episode_context(episode, entries, primary_incident_id)
            media_evidence = self.plane.evidence.model_evidence(episode_id)
            if queued.get("revision_reason") == "evidence_added":
                # Revisions must see the operator's addition before older member
                # priorities. This is a selection preference, not a truth label.
                media_evidence = [dict(item, revision_addition=True) for item in media_evidence]
                media_evidence.sort(key=lambda item: str((item.get("time_range") or {}).get("uploaded_at") or ""), reverse=True)
            context["evidence"].extend(media_evidence)
            if queued.get("revision_reason") == "evidence_added":
                context["priority_evidence_ids"] = list(dict.fromkeys([
                    *(context.get("priority_evidence_ids") or []),
                    *(item["id"] for item in media_evidence),
                ]))
            historical = self.historical_candidates(episode, primary_incident_id)
            context["historical_candidates"] = [
                {key: item.get(key) for key in ("episode_id", "reference", "title", "started_at", "ended_at", "status", "resource")}
                for item in historical
            ]
            context["capture_limit"] = "At most 12 latest member reports and 80 initial evidence items; additional members remain individually accessible."
            application = self.plane.store.get_application(episode["app_id"])
            kit = InvestigationTools(
                entries, application or {}, self.plane.live_sources, historical,
                primary_incident_id=primary_incident_id,
            )
            config = self.plane.ai_configuration()
            context["investigation_limits"] = {
                "max_checks": config["max_checks"],
                "max_total_tokens": config["max_total_tokens"],
                "max_prompt_tokens": config["max_prompt_tokens"],
            }

            def publish(state):
                with self.plane.briefing_lock:
                    current = self.plane.store.get_episode(episode_id)
                    if self.stopping or not current or not original_ids.issubset({item["incident_id"] for item in current["signals"]}):
                        raise RuntimeError("Investigation cancelled after shutdown or membership deletion")
                    state.update(input_fingerprint=input_fingerprint, attempt=queued["attempt"],
                                 lifetime_usage=queued.get("lifetime_usage", {}), previous_runs=queued.get("previous_runs", []),
                                 revision_id=queued.get("revision_id"), parent_revision_id=queued.get("parent_revision_id"),
                                 primary_incident_id=primary_incident_id,
                                 revision_reason=queued.get("revision_reason"), source_mode=queued.get("source_mode"),
                                 evidence_manifest=evidence_manifest)
                    if state.get("status") in {"ready", "incomplete", "inconclusive"}:
                        state["findings"] = derive_findings(state)
                    self.plane._write_briefing_state(self.path(episode_id), state)
                    self._record_revision(state)
                    if state["status"] in {"ready", "incomplete", "inconclusive"}:
                        for entry in entries:
                            root = Path(entry["record"]["output_dir"])
                            self.plane._write_briefing_state(root / "episode_investigation.json", state)
                            create_archive(root, entry["incident"]["incident_id"])
                        self._write_revision_exports(episode_id)

            run_investigation(context, kit, config["model"], config["max_tokens"], publish)
        except Exception as error:
            with self.plane.briefing_lock:
                current = self.plane.store.get_episode(episode_id)
                if not self.stopping and current and original_ids.issubset({item["incident_id"] for item in current["signals"]}):
                    state = self.read(episode_id)
                    state.update(status="incomplete", finished_at=now(), error_type=type(error).__name__,
                                 message="Investigation interrupted. Retained evidence is available; retry is explicit.")
                    self.plane._write_briefing_state(self.path(episode_id), state)
                    self._record_revision(state)
                    self._write_revision_exports(episode_id)
        finally:
            with self.plane.briefing_lock:
                self.jobs.discard(episode_id)
                current = self.plane.store.get_episode(episode_id)
                if current and not self.stopping and original_ids.issubset({item["incident_id"] for item in current["signals"]}):
                    fresh = self.entries(current)
                    fresh_manifest = self.plane.evidence.manifest(episode_id)
                    if fresh and self.fingerprint(fresh, fresh_manifest, queued.get("primary_incident_id")) != input_fingerprint:
                        self.start(episode_id, primary_incident_id=queued.get("primary_incident_id"))
