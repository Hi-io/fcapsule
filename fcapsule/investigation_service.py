"""Persist and schedule one investigation per correlated episode."""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fcapsule.episode_investigation import now, run_investigation
from fcapsule.investigation_tools import InvestigationTools, episode_context, historical_episode_result, scrub
from fcapsule.io.archive_writer import create_archive
from fcapsule.reasoning.findings import derive_findings
from fcapsule.reasoning.source_review import run_source_disconnected_review
from fcapsule.store import _alert_family

MAX_EPISODE_MEMBERS = 12
MAX_PRIMARY_CAPSULE_BYTES = 8 * 1024 * 1024
MAX_ATLAS_CASES = 3
ATLAS_SEARCH_LIMIT = 10
ATLAS_MIN_SCORE = 0.65
_SAFE_ATLAS_REFERENCE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")


def _capture_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _aware_capture_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _atlas_reference(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    reference = value.strip()
    return reference if _SAFE_ATLAS_REFERENCE.fullmatch(reference) else None


def _atlas_normalize_term(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return "_".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _atlas_observation(value: Any, case_time: str, known_references: set[str]) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(value, dict):
        return None, None
    row: dict[str, Any] = {}
    for key in ("kind", "key", "unit", "source"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            row[key] = scrub(item.strip(), reference_ids=known_references)[:160]
    raw_value = value.get("value")
    if isinstance(raw_value, str):
        row["value"] = scrub(raw_value, reference_ids=known_references)[:320]
    elif raw_value is None or isinstance(raw_value, (bool, int)):
        row["value"] = raw_value
    elif isinstance(raw_value, float) and math.isfinite(raw_value):
        row["value"] = raw_value
    observed_at = value.get("observed_at")
    if isinstance(observed_at, str) and _aware_capture_time(observed_at):
        row["observed_at"] = observed_at[:40]
    else:
        row["observed_at"] = case_time
    reference = _atlas_reference(value.get("reference"))
    if reference:
        row["reference"] = reference
    return (row or None), reference


def _atlas_hypothesis(value: Any, known_references: set[str]) -> dict[str, Any] | None:
    if isinstance(value, str):
        statement = value
        confidence = None
        supporting_refs = []
    elif isinstance(value, dict):
        statement = value.get("statement")
        confidence = value.get("confidence")
        supporting_refs = value.get("supporting_refs") if isinstance(value.get("supporting_refs"), list) else []
    else:
        return None
    if not isinstance(statement, str) or not statement.strip():
        return None
    refs = [reference for item in supporting_refs
            if (reference := _atlas_reference(item)) is not None][:8]
    result = {
        "statement": scrub(statement.strip(), reference_ids=known_references)[:320],
        "provenance": "Prior unverified model hypothesis; not an observed fact or root-cause finding.",
    }
    if isinstance(confidence, str) and confidence.casefold() in {"low", "medium", "high"}:
        result["confidence"] = confidence.casefold()
    elif type(confidence) in {int, float} and math.isfinite(confidence):
        result["confidence"] = max(0.0, min(1.0, float(confidence)))
    if refs:
        result["supporting_reference_ids"] = refs
    return result


def _atlas_case(value: Any, before: datetime) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    observed_at = value.get("observed_at")
    observed_time = _aware_capture_time(observed_at)
    atlas_case_id = _atlas_reference(value.get("id"))
    if not atlas_case_id or observed_time is None or observed_time > before:
        return None
    instance_id = _atlas_reference(value.get("instance_id"))
    references = {atlas_case_id}
    if instance_id:
        references.add(instance_id)
    observations = []
    observation_references = []
    for item in value.get("observations", [])[:8] if isinstance(value.get("observations"), list) else []:
        row, reference = _atlas_observation(item, observed_at[:40], references)
        if row:
            observations.append(row)
        if reference and reference not in observation_references:
            observation_references.append(reference)
            references.add(reference)
    hypotheses = []
    hypothesis_references = []
    for item in value.get("hypotheses", [])[:4] if isinstance(value.get("hypotheses"), list) else []:
        hypothesis = _atlas_hypothesis(item, references)
        if hypothesis:
            hypotheses.append(hypothesis)
            for reference in hypothesis.get("supporting_reference_ids", []):
                if reference not in hypothesis_references:
                    hypothesis_references.append(reference)
    scope = value.get("scope") if isinstance(value.get("scope"), dict) else {}
    safe_scope = {
        str(key)[:60]: scrub(item, reference_ids=references)[:160]
        for key, item in list(scope.items())[:8]
        if isinstance(key, str) and isinstance(item, str) and item.strip()
    }
    result = {
        "atlas_case_id": atlas_case_id,
        "citation": f"Estima record {atlas_case_id}",
        "observed_at": observed_at[:40],
        "relation": scrub(value.get("relation"), reference_ids=references)[:80]
        if isinstance(value.get("relation"), str) else "historical_analog",
        "scope": safe_scope,
        "summary": scrub(value.get("summary"), reference_ids=references)[:420]
        if isinstance(value.get("summary"), str) else "",
        "observations": observations,
        "prior_hypotheses": hypotheses,
        "factual_reference_ids": observation_references,
        "hypothesis_reference_ids": hypothesis_references,
        "limitation": "Prior cross-instance case only. Its captured observations are not evidence for this incident; prior hypotheses are unverified model output, never an RCA. Verify any lead with current scoped observations.",
    }
    if instance_id:
        result["instance_id"] = instance_id
    score = value.get("score")
    if type(score) in {int, float} and math.isfinite(score):
        result["score"] = max(0.0, min(1.0, float(score)))
    return result


class InvestigationService:
    def __init__(self, plane):
        self.plane = plane
        self.jobs: set[str] = set()
        self.pending_targets: dict[str, str] = {}
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
                               ("provider", "model", "retained_context", "retained_checks", "model_context",
                                "available_evidence_ids") if key in state})
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
            "revision_id", "parent_revision_id", "revision_reason", "source_mode", "status", "provider", "queued_at",
            "started_at", "finished_at", "model", "policy_version", "message", "validation_error",
            "assessment", "checks", "calls", "review", "findings", "usage", "token_budget",
            "investigation_contract", "evidence_manifest", "input_fingerprint", "report_fingerprint",
            "recovery_attempt",
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

    def _notify_estima_publisher(self, episode_id: str) -> None:
        try:
            publisher = getattr(self.plane, "estima_publisher", None) or getattr(self.plane, "atlas_publisher", None)
            notify = getattr(publisher, "notify_episode", None)
            if callable(notify):
                notify(episode_id)
        except Exception:
            # Estima publication is best-effort and never holds up local results.
            pass

    def _notify_atlas_publisher(self, episode_id: str) -> None:
        """Compatibility alias for the old publisher hook name."""
        self._notify_estima_publisher(episode_id)

    def for_incident(self, incident_id: str) -> dict[str, Any] | None:
        episode = self.plane.store.episode_for_incident(incident_id)
        return self.read(episode["episode_id"]) if episode else None

    def entries(
        self,
        episode,
        primary_incident_id: str | None = None,
        *,
        load_primary_capsule: bool = True,
    ) -> list[dict[str, Any]]:
        entries = []
        signals = list(episode.get("signals", []))
        focus_id = str(primary_incident_id or episode.get("primary_incident_id") or "")
        selected = signals[-MAX_EPISODE_MEMBERS:]
        if focus_id and not any(item.get("incident_id") == focus_id for item in selected):
            focus = next((item for item in signals if item.get("incident_id") == focus_id), None)
            if focus:
                selected = [focus, *selected[-(MAX_EPISODE_MEMBERS - 1):]]
        for signal in selected:
            record = self.plane.store.get_capsule_for_incident(signal["incident_id"])
            if not record:
                continue
            root = Path(record["output_dir"])
            report_path = root / "incident_report.json"
            if not report_path.is_file():
                continue
            capsule = {}
            capsule_load_status = "not_loaded"
            capsule_path = root / "capsule.json"
            if load_primary_capsule and signal["incident_id"] == focus_id and capsule_path.is_file():
                try:
                    if capsule_path.stat().st_size <= MAX_PRIMARY_CAPSULE_BYTES:
                        capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
                        capsule_load_status = "loaded"
                    else:
                        capsule_load_status = "size_limit"
                except (OSError, ValueError):
                    capsule_load_status = "unavailable"
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            incident = self.plane.store.get_incident(signal["incident_id"])
            if incident and isinstance(capsule, dict) and isinstance(report, dict):
                entries.append({"incident": incident, "record": record, "capsule": capsule, "report": report,
                                "capsule_load_status": capsule_load_status})
        return entries

    def historical_candidates(self, episode: dict[str, Any], primary_incident_id: str | None = None) -> list[dict[str, Any]]:
        """Bound prior recurrence evidence before exposing it to the investigator."""

        current_id = primary_incident_id or episode.get("primary_incident_id")
        current = next((item for item in episode.get("signals", []) if item["incident_id"] == current_id), {})
        if not current:
            return []
        current_time = _capture_time(current.get("started_at"))
        if current_time is None:
            return []
        identity = current.get("recurrence_key")
        summaries = self.plane.store.recurrence_candidates_for_incident(episode["episode_id"], current_id)
        candidates = []
        for summary in summaries[:3]:
            prior = self.plane.store.get_episode(str(summary["episode_id"]))
            prior_time = _capture_time(prior.get("started_at")) if prior else None
            if (not prior or prior.get("app_id") != episode.get("app_id")
                    or prior_time is None or prior_time >= current_time):
                continue
            match_type = str(summary.get("match_type") or "same_target")
            # Select from stored identities before loading bounded artifacts. A
            # matching older member must not disappear behind newer other alerts.
            signals = sorted(prior.get("signals", []),
                             key=lambda item: (item.get("started_at", ""), item["incident_id"]), reverse=True)
            member_relations = {}
            future_matching_member = False
            for item in signals:
                if identity and item.get("recurrence_key") == identity:
                    relation = "same_target"
                elif match_type == "same_workload_different_pod" and self._same_live_workload_alert_other_pod(
                    current, item, episode.get("app_id")
                ):
                    relation = "same_workload_different_pod"
                else:
                    continue
                member_time = _capture_time(item.get("started_at"))
                if member_time is None or member_time >= current_time:
                    future_matching_member = True
                    continue
                member_relations[item["incident_id"]] = relation
            if future_matching_member and not member_relations:
                continue
            signals.sort(key=lambda item: item["incident_id"] not in member_relations)
            selected = [item for item in signals if item["incident_id"] in member_relations][:4]
            prior_entries = self.entries(
                {**prior, "signals": selected},
                primary_incident_id=selected[0]["incident_id"] if selected else None,
                load_primary_capsule=False,
            )
            loaded = {item["incident"]["incident_id"] for item in prior_entries}
            matching_entries = [item for item in prior_entries if item["incident"]["incident_id"] in member_relations]
            cross_pod = match_type == "same_workload_different_pod"
            limitation = (
                "Same live app/workload, namespace, cluster and alert identity across different pod names is a retrieval relation only; it is not evidence of the same target or cause. Prior pod provenance remains attached. Missing matching captures cannot be replaced by other members or prior diagnoses."
                if cross_pod else
                "Same stored target and alert identity is not a cause match. Missing matching captures cannot be replaced by other members or prior diagnoses."
            )
            selection = {
                "policy": "exact_target_then_same_live_workload_alert_cross_pod",
                "candidate_relation": match_type,
                "current_incident_id": current_id, "identity_available": bool(identity),
                "current_target": {"kind": current.get("resource_kind"), "name": current.get("resource_name")},
                "matching_member_count": len(member_relations), "retained_matching_member_count": len(matching_entries),
                "omitted_member_count": max(0, len(signals) - len(selected)),
                "selected_members": [{"incident_id": item["incident_id"],
                    "matches_current_alert_identity": item["incident_id"] in member_relations,
                    "matches_current_target": member_relations.get(item["incident_id"]) == "same_target",
                    "target_relation": member_relations.get(item["incident_id"]),
                    "resource": {"kind": item.get("resource_kind"), "name": item.get("resource_name")},
                    "capture_available": item["incident_id"] in loaded} for item in selected],
                "limitation": limitation,
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
                        "target_relation": member_relations.get(entry["incident"]["incident_id"]),
                        "matches_current_target": member_relations.get(entry["incident"]["incident_id"]) == "same_target",
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
            for observation in observations:
                for provenance in observation.get("provenance", []):
                    relation = member_relations.get(str(provenance.get("incident_id") or ""))
                    provenance["target_relation"] = relation
                    provenance["matches_current_target"] = relation == "same_target"
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
                    "capture_limit": (
                        "At most four retained live member reports with the exact stable app/workload, cluster, namespace and alert identity from different pod names. This is a tentative related-capture relation, not same-target or same-cause evidence. Unrelated members are excluded; missing matching captures remain unavailable."
                        if cross_pod else
                        "At most four retained member reports with the exact stored scope/alert identity; unrelated episode members are excluded. Missing matching captures remain unavailable, not substituted by other members."
                    ),
                    "prior_hypothesis": (
                        {"provenance": "Earlier model output; not independent evidence and not citable.", **prior_hypothesis}
                        if prior_hypothesis and matching_entries else {}
                    ),
                    "captured_evidence": evidence,
                }
            )
        return candidates

    @staticmethod
    def _atlas_search_profile(context: dict[str, Any]) -> tuple[str, str, list[str]]:
        alerts = context.get("alerts") if isinstance(context.get("alerts"), list) else []
        primary_alert = next((item for item in alerts if isinstance(item, dict)), {})
        alert_family = next((primary_alert.get(key) for key in ("alert_identity", "alertname", "name")
                             if isinstance(primary_alert.get(key), str) and primary_alert[key].strip()), "")
        if not alert_family:
            return "", "", []

        diagnostic_keys = []
        evidence = context.get("evidence") if isinstance(context.get("evidence"), list) else []
        for item in evidence[:16]:
            if not isinstance(item, dict):
                continue
            metric = item.get("metric_observation") if isinstance(item.get("metric_observation"), dict) else {}
            metric_name = metric.get("metric")
            if isinstance(metric_name, str) and metric_name.strip():
                diagnostic_keys.append(metric_name.strip())
            fields = item.get("diagnostic_fields") if isinstance(item.get("diagnostic_fields"), dict) else {}
            for example in item.get("examples", [])[:3] if isinstance(item.get("examples"), list) else []:
                if isinstance(example, dict) and isinstance(example.get("diagnostic_fields"), dict):
                    fields = {**fields, **example["diagnostic_fields"]}
            for key in fields:
                if isinstance(key, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{2,63}", key):
                    diagnostic_keys.append(key)
        diagnostic_keys = list(dict.fromkeys(diagnostic_keys))[:2]
        if not diagnostic_keys:
            return "", alert_family, []
        # Estima search is token-OR, so keep its terms focused on the exact alert
        # family and structured diagnostic identities; free-form summaries and
        # service names cause unrelated cross-instance matches.
        query = scrub(" ".join([alert_family, *diagnostic_keys]), reference_ids=set())[:500]
        return query, alert_family, diagnostic_keys

    @staticmethod
    def _atlas_candidate_relevant(candidate: Any, alert_family: str, diagnostic_keys: list[str]) -> bool:
        if not isinstance(candidate, dict):
            return False
        score = candidate.get("score")
        if type(score) in {int, float} and math.isfinite(score) and score >= ATLAS_MIN_SCORE:
            return True
        if not alert_family or not diagnostic_keys:
            return False
        observations = candidate.get("observations") if isinstance(candidate.get("observations"), list) else []
        normalized_alert = _atlas_normalize_term(alert_family)
        observed_alert = False
        observed_keys = set()
        for item in observations:
            if not isinstance(item, dict):
                continue
            key = _atlas_normalize_term(item.get("key"))
            value = _atlas_normalize_term(item.get("value"))
            if key in {"alert_family", "alertname", "alert_identity"} and value == normalized_alert:
                observed_alert = True
            if key.startswith("diagnostic_"):
                key = key[len("diagnostic_"):]
            if key:
                observed_keys.add(key)
        summary = _atlas_normalize_term(candidate.get("summary"))
        observed_alert = observed_alert or bool(normalized_alert and
                                                f"_{normalized_alert}_" in f"_{summary}_")
        diagnostic_overlap = any(
            _atlas_normalize_term(key).removeprefix("diagnostic_") in observed_keys
            for key in diagnostic_keys
        )
        return observed_alert and diagnostic_overlap

    def _estima_retrieval(self, context: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        limitation = "Estima retrieval is optional; retained local evidence remains the investigation source of truth."
        get_client = getattr(self.plane, "estima_client", None) or getattr(self.plane, "atlas_client", None)
        if not callable(get_client):
            return [], {"status": "disabled", "case_count": 0, "limitation": limitation}
        try:
            client = get_client("read")
        except Exception:
            return [], {"status": "unavailable", "case_count": 0,
                        "limitation": "Estima retrieval was unavailable; the local evidence investigation continues."}
        if client is None:
            return [], {"status": "disabled", "case_count": 0, "limitation": limitation}

        scope = context.get("scope") if isinstance(context.get("scope"), dict) else {}
        before = _aware_capture_time(scope.get("alert_started_at"))
        if before is None:
            return [], {"status": "skipped_no_time", "case_count": 0,
                        "limitation": "Estima retrieval was skipped because no reliable alert time was available; future records are excluded."}
        query, alert_family, diagnostic_keys = self._atlas_search_profile(context)
        if not query:
            return [], {"status": "skipped_no_diagnostics", "case_count": 0,
                        "observed_before": before.isoformat().replace("+00:00", "Z"),
                        "limitation": "Estima retrieval was skipped because the primary alert lacked structured diagnostic keys; local evidence continues."}
        observed_before = before.isoformat().replace("+00:00", "Z")
        # Estima scope accepts deployment identity fields, not Kubernetes resource
        # kinds. Query text carries the target/alert signal without excluding
        # useful records from another cluster or installation.
        estima_scope: dict[str, Any] = {}
        try:
            response = client.search(estima_scope, query, limit=ATLAS_SEARCH_LIMIT, before=observed_before)
        except Exception:
            return [], {"status": "unavailable", "case_count": 0, "observed_before": observed_before,
                        "limitation": "Estima retrieval was unavailable; the local evidence investigation continues."}
        raw_cases = response.get("cases") if isinstance(response, dict) else None
        if not isinstance(raw_cases, list):
            return [], {"status": "unavailable", "case_count": 0, "observed_before": observed_before,
                        "limitation": "Estima returned no usable retrieval response; the local evidence investigation continues."}
        cases = []
        rejected = 0
        for raw in raw_cases[:ATLAS_SEARCH_LIMIT]:
            if not self._atlas_candidate_relevant(raw, alert_family, diagnostic_keys):
                rejected += 1
                continue
            candidate = _atlas_case(raw, before)
            if candidate:
                cases.append(candidate)
            if len(cases) == MAX_ATLAS_CASES:
                break
        status = "matched" if cases else "no_relevant_matches" if rejected else "no_matches"
        return cases, {"status": status, "case_count": len(cases), "observed_before": observed_before,
                       "limitation": limitation if cases else
                       ("No prior Estima records met the alert-time and relevance gates; local evidence investigation continues."
                        if rejected else
                        "No prior Estima records met the strict alert-time cutoff; local evidence investigation continues.")}

    def _atlas_retrieval(self, context: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Compatibility alias for persisted FCAPSule investigation integrations."""
        return self._estima_retrieval(context)

    @staticmethod
    def _same_live_workload_alert_other_pod(current: dict[str, Any], prior: dict[str, Any], app_id: Any) -> bool:
        """Allow only live pod records with an exact stable app/workload scope."""

        current_name = str(current.get("resource_name") or "")
        prior_name = str(prior.get("resource_name") or "")
        alert_identity = _alert_family(str(current.get("recurrence_key") or ""))
        return bool(
            app_id
            and current.get("app_id") == app_id == prior.get("app_id")
            and current.get("source_kind") == prior.get("source_kind") == "live"
            and current.get("resource_kind") == prior.get("resource_kind") == "pod"
            and current_name and prior_name and current_name != prior_name
            and alert_identity and _alert_family(str(prior.get("recurrence_key") or "")) == alert_identity
        )

    @staticmethod
    def report_fingerprint(entries, primary_incident_id: str | None = None) -> str:
        payload = {
            "reports": [
                [item.get("incident", {}).get("incident_id", ""), item.get("report", "")]
                for item in entries
            ],
            "primary_incident_id": primary_incident_id or "",
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

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
                "provider": config["provider"], "model": config["model"], "created_at": now(), "result": None,
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
                                 provider=config["provider"], created_at=queued["created_at"], source_mode="retained_only")
                    self._write_source_review(state)
                    if state.get("status") in {"ready", "incomplete"}:
                        self._write_revision_exports(episode_id)

            run_source_disconnected_review(
                context, checks, str(queued["question"]), config["model"],
                min(config["max_tokens"], 700), publish,
                max_prompt_tokens=min(config["max_prompt_tokens"], 2200),
                max_total_tokens=min(config["max_total_tokens"], 3500),
                provider=config["provider"],
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
              source_mode: str = "live_sources", primary_incident_id: str | None = None,
              automatic_recovery: bool = False) -> dict[str, Any]:
        with self.plane.briefing_lock:
            episode = self.plane.store.get_episode(episode_id)
            if not episode:
                raise KeyError("Episode not found")
            if primary_incident_id and primary_incident_id not in {
                item["incident_id"] for item in episode.get("signals", [])
            }:
                raise ValueError("Primary incident is not a member of this episode")
            if self.stopping:
                return self.read(episode_id)
            if episode_id in self.jobs:
                state = self.read(episode_id)
                if primary_incident_id:
                    self.pending_targets[episode_id] = primary_incident_id
                    state["pending_primary_incident_id"] = primary_incident_id
                    state["follow_up_status"] = "queued"
                    self.plane._write_briefing_state(self.path(episode_id), state)
                return state
            primary_incident_id = primary_incident_id or episode.get("primary_incident_id")
            entries = self.entries(episode, primary_incident_id=primary_incident_id)
            if not entries:
                raise ValueError("Build at least one report before starting an investigation")
            if primary_incident_id and primary_incident_id not in {item["incident"]["incident_id"] for item in entries}:
                raise ValueError("Primary incident is not a member of this episode")
            evidence_manifest = self.plane.evidence.manifest(episode_id)
            fingerprint = self.fingerprint(entries, evidence_manifest, primary_incident_id)
            previous = self.read(episode_id)
            config = self.plane.ai_configuration()
            if previous.get("status") in {"queued", "running"}:
                interrupted_at = now()
                calls = previous.get("calls") if isinstance(previous.get("calls"), list) else []
                checks = previous.get("checks") if isinstance(previous.get("checks"), list) else []
                active_call = False
                for call in calls:
                    if isinstance(call, dict) and call.get("status") == "running":
                        call.update(status="failed", finished_at=interrupted_at,
                                    error_type="InterruptedError", interrupted=True)
                        active_call = True
                for check in checks:
                    if isinstance(check, dict) and check.get("status") == "running":
                        check.update(
                            status="unavailable",
                            finished_at=interrupted_at,
                            result={"limitation": "Check was interrupted before an observation was retained."},
                        )
                usage = previous.setdefault(
                    "usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                )
                if active_call:
                    usage["complete"] = False
                previous.update(
                    status="incomplete",
                    finished_at=interrupted_at,
                    error_type="InterruptedError",
                    message="Investigation was interrupted by process shutdown. Retained checks are available; retry is explicit.",
                )
                self.plane._write_briefing_state(self.path(episode_id), previous)
                self._record_revision(previous)
            if not retry and previous.get("input_fingerprint") == fingerprint and previous.get("status") in {"ready", "incomplete", "inconclusive"}:
                return previous
            revision_id = f"revision-{uuid.uuid4().hex}"
            state = {"version": "1", "episode_id": episode_id, "revision_id": revision_id,
                     "parent_revision_id": previous.get("revision_id"), "revision_reason": reason,
                     "source_mode": source_mode, "status": "queued", "queued_at": now(),
                     "provider": config["provider"], "model": config["model"],
                     "input_fingerprint": fingerprint, "checks": [], "assessment": None,
                     "attempt": previous.get("attempt", 0) + 1, "evidence_manifest": evidence_manifest,
                     "report_fingerprint": self.report_fingerprint(entries, primary_incident_id),
                     "primary_incident_id": primary_incident_id,
                     "recovery_attempt": 1 if automatic_recovery else 0}
            history = list(previous.get("previous_runs", []))
            if previous.get("started_at"):
                history.append({key: previous.get(key) for key in (
                    "attempt", "started_at", "finished_at", "status", "provider", "model", "usage",
                    "assessment", "checks", "calls", "draft_assessment", "review", "policy_version",
                    "recovery_attempt",
                )})
            state["previous_runs"] = history[-3:]
            if previous.get("usage"):
                prior = previous.get("lifetime_usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "complete": True})
                state["lifetime_usage"] = {key: prior.get(key, 0) + previous["usage"].get(key, 0)
                                           for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
                state["lifetime_usage"]["complete"] = prior.get("complete", True) and previous["usage"].get("complete", False)
            elif previous.get("lifetime_usage"):
                state["lifetime_usage"] = previous["lifetime_usage"]
            if not config["api_key_configured"]:
                state.update(status="not_configured", message="Add a provider key in Settings to enable episode investigation.")
            elif not retry and len(entries) < min(12, len(episode["signals"])):
                state.update(status="waiting", message="Waiting for the episode's reports to finish.")
            self.plane._write_briefing_state(self.path(episode_id), state)
            self._record_revision(state)
            if state["status"] == "not_configured":
                self._notify_estima_publisher(episode_id)
            elif state["status"] == "queued":
                self.jobs.add(episode_id)
                self.plane.briefing_executor.submit(self._run, episode_id, state)
            return state

    def resume(self) -> None:
        for episode in self.plane.store.list_episodes(limit=10000):
            if any(signal.get("report_ready") for signal in episode["signals"]):
                episode_id = str(episode["episode_id"])
                previous = self.read(episode_id)
                primary_id = previous.get("primary_incident_id")
                if previous.get("status") in {"ready", "incomplete", "inconclusive"}:
                    entries = self.entries(episode, primary_incident_id=primary_id or episode.get("primary_incident_id"))
                    manifest = self.plane.evidence.manifest(episode_id)
                    current_primary_id = primary_id or episode.get("primary_incident_id")
                    # Older manual revisions fingerprinted an implicit primary as
                    # None. Keep completed work when its inputs are unchanged.
                    if previous.get("input_fingerprint") == self.fingerprint(
                        entries, manifest, primary_id
                    ):
                        continue
                    old_manifest = previous.get("evidence_manifest")
                    old_attachment_ids = {
                        str(item.get("attachment_id")) for item in old_manifest if isinstance(item, dict)
                    } if isinstance(old_manifest, list) else set()
                    current_attachment_ids = {
                        str(item.get("attachment_id")) for item in manifest if isinstance(item, dict)
                    }
                    report_fingerprint = self.report_fingerprint(entries, current_primary_id)
                    prior_report_fingerprint = previous.get("report_fingerprint")
                    reports_unchanged = (
                        prior_report_fingerprint == report_fingerprint
                        if isinstance(prior_report_fingerprint, str)
                        else bool(previous.get("input_fingerprint"))
                    )
                    if (reports_unchanged and current_attachment_ids - old_attachment_ids
                            and old_attachment_ids.issubset(current_attachment_ids)):
                        continue
                if previous.get("status") in {"queued", "running"}:
                    recover_automatically = int(previous.get("recovery_attempt", 0) or 0) < 1
                    self.start(
                        episode_id,
                        retry=recover_automatically,
                        reason=str(previous.get("revision_reason") or "initial_capture"),
                        source_mode=str(previous.get("source_mode") or "live_sources"),
                        primary_incident_id=primary_id or episode.get("primary_incident_id"),
                        automatic_recovery=recover_automatically,
                    )
                else:
                    self.start(episode_id, primary_incident_id=episode.get("primary_incident_id"))

    def invalidate(self, episode_id: str) -> None:
        """Do not retain deleted member evidence in a surviving episode assessment."""
        self.pending_targets.pop(episode_id, None)
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
            primary_incident_id = queued.get("primary_incident_id") or episode.get("primary_incident_id")
            entries = self.entries(episode, primary_incident_id=primary_incident_id)
            original_ids = {item["incident"]["incident_id"] for item in entries}
            evidence_manifest = self.plane.evidence.manifest(episode_id)
            input_fingerprint = self.fingerprint(entries, evidence_manifest, primary_incident_id)
            context = episode_context(episode, entries, primary_incident_id)
            oversized_capsules = [
                item["incident"]["incident_id"] for item in entries
                if item.get("capsule_load_status") == "size_limit"
            ]
            if oversized_capsules:
                context["retained_artifact_limitations"] = {
                    "incident_ids": oversized_capsules,
                    "message": "The full retained capsule remains stored, but the investigation did not load it because it exceeds the per-capsule memory bound. Use the bounded incident report and available source checks instead.",
                }
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
            atlas_cases, atlas_retrieval = self._estima_retrieval(context)
            # Keep the saved context keys stable for older capsules and prompt consumers.
            context["atlas_cases"] = atlas_cases
            context["atlas_retrieval"] = atlas_retrieval
            context["capture_limit"] = "At most 12 latest member reports and 80 initial evidence items; additional members remain individually accessible."
            application = self.plane.store.get_application(episode["app_id"])
            kit = InvestigationTools(
                entries, application or {}, self.plane.live_sources, historical,
                primary_incident_id=primary_incident_id,
            )
            config = self.plane.ai_configuration()
            context["investigation_limits"] = {
                "provider": config["provider"],
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
                                 provider=config["provider"], model=config["model"],
                                 report_fingerprint=queued.get("report_fingerprint"),
                                 recovery_attempt=queued.get("recovery_attempt", 0),
                                 lifetime_usage=queued.get("lifetime_usage", {}), previous_runs=queued.get("previous_runs", []),
                                 revision_id=queued.get("revision_id"), parent_revision_id=queued.get("parent_revision_id"),
                                 primary_incident_id=primary_incident_id,
                                 revision_reason=queued.get("revision_reason"), source_mode=queued.get("source_mode"),
                                 evidence_manifest=evidence_manifest)
                    pending_primary = self.pending_targets.get(episode_id)
                    if pending_primary:
                        state["pending_primary_incident_id"] = pending_primary
                        state["follow_up_status"] = "queued"
                    if state.get("status") in {"ready", "incomplete", "inconclusive"}:
                        state["findings"] = derive_findings(state)
                    self.plane._write_briefing_state(self.path(episode_id), state)
                    self._record_revision(state)
                    if state["status"] in {"ready", "incomplete", "inconclusive"}:
                        self._notify_estima_publisher(episode_id)
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
                    pending_primary = self.pending_targets.pop(episode_id, None)
                    next_primary = pending_primary or queued.get("primary_incident_id") or current.get("primary_incident_id")
                    fresh = self.entries(current, primary_incident_id=next_primary)
                    available_ids = {item["incident"]["incident_id"] for item in fresh}
                    if next_primary and next_primary not in available_ids:
                        next_primary = current.get("primary_incident_id")
                        fresh = self.entries(current, primary_incident_id=next_primary)
                        available_ids = {item["incident"]["incident_id"] for item in fresh}
                    fresh_manifest = self.plane.evidence.manifest(episode_id)
                    if next_primary in available_ids and self.fingerprint(fresh, fresh_manifest, next_primary) != input_fingerprint:
                        self.start(episode_id, primary_incident_id=next_primary)
