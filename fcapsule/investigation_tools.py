"""Bounded, read-only investigation tools. Models never supply URLs, SQL or PromQL."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from fcapsule.adapters.kubernetes_adapter import evaluate_label_selector
from fcapsule.processing.anonymizer import anonymize_text, diagnostic_fields, template_for_message


def scrub(value: Any, *, reference_ids: set[str] | frozenset[str] = frozenset()) -> Any:
    if isinstance(value, str):
        # Only exact, caller-validated references bypass masking. Never exempt a
        # UUID/hex-shaped string merely because it looks like an internal ID.
        if value in reference_ids:
            return value
        return anonymize_text(value)[:2000]
    if isinstance(value, dict):
        return {str(key): scrub(item, reference_ids=reference_ids) for key, item in value.items()
                if not any(term in str(key).lower() for term in ("password", "secret", "token", "credential"))}
    if isinstance(value, list):
        return [scrub(item, reference_ids=reference_ids) for item in value[:80]]
    return value


def stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _observed_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def metric_summary(
    series: list[dict[str, Any]], focus: datetime | None = None, *, captured_at: str | None = None,
) -> list[dict[str, Any]]:
    result = []
    for item in series[:24]:
        points = []
        for point in item.get("values", []):
            try:
                timestamp, value = str(point[0]), float(point[1])
                if math.isfinite(value):
                    points.append((stamp(timestamp), timestamp, value))
            except (IndexError, TypeError, ValueError):
                continue
        points.sort(key=lambda point: point[0])
        metric = {"metric": item.get("metric", "unknown"), "labels": item.get("labels", {}),
                  "samples": len(points),
                  "freshness": {"status": "sampled" if points else "no_data",
                                "latest_sample_at": points[-1][1] if points else None,
                                "age_seconds": None,
                                "assessment": "Sample age is reported; no scrape-interval threshold is assumed."}}
        if captured_at:
            try:
                metric["freshness"]["age_seconds"] = max(0, int((stamp(captured_at) - points[-1][0]).total_seconds())) if points else None
                metric["freshness"]["captured_at"] = captured_at
            except ValueError:
                pass
        if not points:
            result.append(metric)
            continue
        values = [point[2] for point in points]
        metric.update({"min": min(values), "max": max(values), "median": statistics.median(values),
                       "start": points[0][1], "end": points[-1][1],
                       "first": values[0], "last": values[-1],
                       "trend": [[point[1], point[2]] for point in points[::max(1, len(points) // 24)][:25]]})
        if focus:
            before = [point for point in points if point[0] < focus]
            after = [point for point in points if point[0] > focus]
            nearest = min(points, key=lambda point: abs((point[0] - focus).total_seconds()))
            peak = max(points, key=lambda point: point[2])

            def phase(point):
                return {"timestamp": point[1], "value": point[2]}

            metric["before_alert"] = phase(before[-1]) if before else None
            metric["nearest_alert"] = {**phase(nearest),
                                       "offset_seconds": round((nearest[0] - focus).total_seconds(), 1)}
            metric["after_alert"] = phase(after[0]) if after else None
            metric["sampled_peak"] = phase(peak)
            recent = [point for point in points if point[0] >= focus]
            recent_values = [point[2] for point in recent]
            metric["at_or_after_latest_alert"] = ({"samples": len(recent), "start": recent[0][1], "end": recent[-1][1],
                "min": min(recent_values), "max": max(recent_values), "median": statistics.median(recent_values),
                "first": recent_values[0], "last": recent_values[-1]} if recent else {"samples": 0})
        result.append(metric)
    return result


def log_patterns(logs: list[dict[str, Any]], terms: list[str] | None = None) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for row in logs:
        raw_message = row.get("message", "")
        message = json.dumps(raw_message, ensure_ascii=True, sort_keys=True, separators=(",", ":")) \
            if isinstance(raw_message, dict) else str(raw_message)
        fields = diagnostic_fields(message, row.get("diagnostic_fields")
                                   if isinstance(row.get("diagnostic_fields"), dict) else None)
        if terms and not any(term.lower() in message.lower() for term in terms):
            continue
        pattern = template_for_message(message, fields)
        group = groups.setdefault(pattern, {"pattern": pattern[:1000], "count": 0,
            "fields": fields, "examples": [], "first_seen": row.get("@timestamp")})
        group["count"] += 1
        group["last_seen"] = row.get("@timestamp")
        example = {"timestamp": row.get("@timestamp"), "level": row.get("level"),
                   "message": anonymize_text(message)[:1000], "diagnostic_fields": fields}
        if len(group["examples"]) < 2:
            group["examples"].append(example)
        else:
            group["examples"][-1] = example
    ranked = sorted(groups.values(), key=lambda item: (-bool(item["fields"]), -item["count"]))
    return {"scanned_lines": len(logs), "matching_patterns": len(groups), "patterns": ranked[:12],
            "limitation": "Bounded sample; no matches does not prove the event did not occur."}


def _selector_observation(labels: dict[str, Any], monitor: dict[str, Any], namespace_status: str) -> dict[str, Any]:
    evaluated = evaluate_label_selector(
        labels,
        monitor.get("match_labels", {}),
        monitor.get("match_expressions", []),
        complete=monitor.get("selector_complete") is not False,
    )
    if namespace_status != "resolved":
        evaluated = {**evaluated, "status": "unknown", "reason": "namespace_selector_unknown"}
    return evaluated


def _port_comparison(declaration: dict[str, Any], service_ports: list[dict[str, Any]]) -> dict[str, Any]:
    endpoint = declaration.get("configured_endpoint") if isinstance(declaration.get("configured_endpoint"), dict) else {}
    configured_port = endpoint.get("port")
    valid_ports = [item for item in service_ports if isinstance(item, dict) and type(item.get("port")) is int]
    if type(configured_port) is not int or not valid_ports:
        status = "unknown"
    elif any(configured_port == item["port"] for item in valid_ports):
        status = "matches_service_port"
    else:
        status = "does_not_match_service_port"
    return {
        "configured_host": endpoint.get("host"),
        "configured_port": configured_port,
        "configured_port_source": endpoint.get("port_source", "not_declared"),
        "port_configured_via": declaration.get("port_configured_via"),
        "service_ports": valid_ports[:12],
        "status": status,
        "comparison_basis": "Configured endpoint port compared with Kubernetes Service port; targetPort is the backend port and is not used as the client-facing Service port.",
    }


def episode_context(
    episode: dict[str, Any],
    entries: list[dict[str, Any]],
    primary_incident_id: str | None = None,
) -> dict[str, Any]:
    from fcapsule.reasoning.context_budget import compact_metric_observation

    evidence: dict[str, dict[str, Any]] = {}
    alerts = []
    # A recurrence can reopen a retained operator episode. Put the primary
    # incident first so bounded context selection investigates its latest
    # evidence; older member reports are still available as non-current context.
    requested_primary = str(primary_incident_id or "")
    entry_ids = {str(entry["incident"].get("incident_id") or "") for entry in entries}
    primary_incident_id = requested_primary if requested_primary in entry_ids else str(episode.get("primary_incident_id") or "")
    ordered_entries = sorted(
        entries,
        key=lambda entry: str(entry["incident"].get("incident_id")) != primary_incident_id,
    )
    # Scope describes the primary retained capture, not inferred dependencies or
    # live state. Whitelist fields so labels, annotations and private case data
    # cannot hitch a ride in this small, budget-protected identity record.
    primary = ordered_entries[0] if ordered_entries else {}
    case = (primary.get("capsule") or {}).get("case") or {}
    report_incident = (primary.get("report") or {}).get("incident") or {}
    incident = primary.get("incident") or {}
    scope = {}
    for key in ("cluster", "namespace", "service", "pod"):
        value = case.get(key) or report_incident.get(key)
        if isinstance(value, str) and value.strip():
            scope[key] = anonymize_text(value.strip())[:253]
    resource = incident.get("resource") or report_incident.get("resource") or {}
    if isinstance(resource, dict):
        retained_resource = {key: anonymize_text(resource[key].strip())[:253] for key in ("kind", "name")
                             if isinstance(resource.get(key), str) and resource[key].strip()}
        if retained_resource:
            scope["resource"] = retained_resource
    if "pod" not in scope and isinstance(resource, dict) and str(resource.get("kind", "")).casefold() == "pod":
        name = scope.get("resource", {}).get("name")
        if name:
            scope["pod"] = name
    alert_start = incident.get("started_at") or report_incident.get("started_at")
    if isinstance(alert_start, str) and alert_start.strip():
        scope["alert_started_at"] = alert_start.strip()[:40]
    window = case.get("window") or {}
    if isinstance(window, dict):
        retained_window = {key: window[key].strip()[:40] for key in ("start", "end")
                           if isinstance(window.get(key), str) and window[key].strip()}
        if retained_window:
            scope["window"] = retained_window
    recurrence = episode.get("recurrence") or {}
    prior_count = recurrence.get("previous_count") if isinstance(recurrence, dict) else None
    recurrence_context = {}
    if type(prior_count) is int and prior_count >= 0:
        recurrence_context = {
            "previous_count": min(prior_count, 9999),
            "count_capped": prior_count > 9999,
            "limitation": "Same-signature retained candidates only; not evidence of the same cause.",
        }
    for entry in ordered_entries:
        report = entry["report"]
        incident_id = entry["incident"]["incident_id"]
        alert_context = {**report["incident"], "incident_id": incident_id,
                         "current_status": entry["incident"].get("status"),
                         "ended_at": entry["incident"].get("ended_at") if entry["incident"].get("status") == "resolved" else None}
        discovery_labels = _discovery_labels(report)
        if discovery_labels:
            alert_context["labels"] = discovery_labels
        alert_identity = _alert_identity(report)
        if alert_identity:
            alert_context["alert_identity"] = alert_identity
        alerts.append(alert_context)
        for item in report.get("supporting_evidence", []):
            identity = [item.get("type"), item.get("title"), item.get("summary"), item.get("time_range"),
                        item.get("linked_entities"), item.get("configuration"), item.get("representative_events")]
            metric_observation = compact_metric_observation(item.get("metric_observation"))
            if metric_observation:
                # Same prose can describe different sampled values or rules.
                identity.append(metric_observation)
            metric_capture = item.get("alert_metric_evidence")
            capture_limitation = None
            if isinstance(metric_capture, dict) and metric_capture.get("status") == "unavailable":
                reason = metric_capture.get("reason")
                if isinstance(reason, str) and reason.strip():
                    capture_limitation = "Alert-rule metric capture unavailable: " + anonymize_text(reason.strip())[:120]
                    identity.append(capture_limitation)
            ref = "E" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:12]
            if ref not in evidence:
                evidence[ref] = {"id": ref, "domain": item.get("type"), "title": item.get("title"),
                    "summary": item.get("summary"), "time_range": item.get("time_range"),
                    "examples": item.get("representative_events") or item.get("representative_lines", []),
                    "diagnostic_fields": item.get("diagnostic_fields", {}),
                    "configuration": item.get("configuration"),
                    "alert": next((alert for alert in report.get("fault_alerts", []) if alert.get("evidence_id") == item["evidence_id"]), None),
                    "provenance": []}
                if metric_observation:
                    evidence[ref]["metric_observation"] = metric_observation
                    if item.get("signal_origin") == "alert_rule":
                        evidence[ref]["signal_origin"] = "alert_rule"
                    if isinstance(item.get("series_id"), str):
                        evidence[ref]["series_id"] = item["series_id"][:120]
                if capture_limitation:
                    evidence[ref]["limitation"] = capture_limitation
            evidence[ref]["provenance"].append({"incident_id": incident_id, "evidence_id": item["evidence_id"]})
    ordered_evidence = list(evidence.values())
    priority_evidence_ids = []
    if primary_incident_id:
        for item in ordered_evidence:
            if any(provenance.get("incident_id") == primary_incident_id for provenance in item["provenance"]):
                item["revision_priority"] = True
                priority_evidence_ids.append(item["id"])
    ordered_evidence.sort(key=lambda item: not item.get("revision_priority", False))
    alerts.sort(key=lambda item: str(item.get("incident_id")) != primary_incident_id)
    return scrub({"episode_id": episode["episode_id"], "live_capture": any(entry["incident"].get("source_kind") == "live" for entry in entries),
        "episode_lifecycle": {key: episode.get(key) for key in
                  ("status", "started_at", "ended_at", "last_activity_at")}, "alerts": alerts,
        "scope": scope, "recurrence": recurrence_context,
        "evidence": ordered_evidence[:80], "priority_evidence_ids": priority_evidence_ids,
        "impact": [item for entry in ordered_entries for item in entry["report"].get("impact", [])][:15],
        "source_retention": "Unknown. Do not infer expiry from incident age or FCAPSule's own cleanup policy.",
        "grouping_basis": "Same application and temporal proximity only. Independent failure phases can share an episode. A resolved alert followed by another alert is not a demonstrated causal chain.",
        "limits": "Time grouping is not causation. Measurements are sampled. Current state is not historical state."},
        reference_ids={str(episode["episode_id"]), *entry_ids, *evidence,
                       *(str(row["evidence_id"]) for item in evidence.values() for row in item["provenance"])})


def _discovery_labels(report: dict[str, Any]) -> dict[str, str]:
    """Retain only explicit, non-sensitive identities needed to inspect target discovery."""

    for alert in report.get("fault_alerts", []):
        rule = alert.get("rule") if isinstance(alert.get("rule"), dict) else {}
        labels = rule.get("labels") if isinstance(rule.get("labels"), dict) else {}
        if not labels and isinstance(alert.get("labels"), dict):
            labels = alert["labels"]
        retained = {
            key: str(labels[key]).strip()[:253]
            for key in ("target_service", "kubernetes_service", "target_workload")
            if isinstance(labels.get(key), str) and labels[key].strip()
        }
        if retained:
            return retained
    return {}


def _alert_identity(report: dict[str, Any]) -> str:
    """Return the retained rule identity without deriving one from prose."""

    for alert in report.get("fault_alerts", []):
        name = alert.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return ""


def historical_episode_result(candidate: dict[str, Any], *, include_hypothesis: bool = True) -> dict[str, Any]:
    """Put captured facts ahead of episode metadata in the bounded tool ledger."""

    episode = {key: value for key, value in candidate.items() if key not in {"observations", "retained_checks"}
               and (include_hypothesis or key != "prior_hypothesis")}
    observations = []
    selection = candidate.get("member_selection") or {}
    matching_members = {item["incident_id"] for item in selection.get("selected_members", [])
                        if item.get("matches_current_alert_identity")}
    retained_checks = candidate.get("retained_checks", [])[-4:]
    for item in candidate.get("observations", [])[:80 - len(retained_checks)]:
        observation = {key: item[key] for key in (
            "summary", "metric_observation", "configuration", "examples", "time_range", "limitation",
        ) if item.get(key) not in (None, "", [], {})}
        metric = observation.get("metric_observation")
        if isinstance(metric, dict) and metric.get("condition"):
            # Generic tool compaction keeps only a few fields. Measured values
            # must precede optional rule metadata even in an older check.
            observation["metric_observation"] = {"condition": metric["condition"], **metric}
        observation["source"] = {"episode_id": candidate["episode_id"], "provenance": item.get("provenance", [])}
        if selection:
            observation["source"]["matches_current_alert_identity"] = any(
                row.get("incident_id") in matching_members for row in item.get("provenance", []))
        observations.append(observation)
    for check in retained_checks:
        observations.append({
            "retained_check": check.get("result"),
            "source": {"episode_id": candidate["episode_id"], "check_id": check.get("id"),
                       "tool": check.get("tool"), "finished_at": check.get("finished_at")},
            "limitation": "Previously saved episode-level source observation; it need not concern the matching member and its collection time may differ from the incident window.",
        })
    # These references come from the selected stored candidate, not model input.
    reference_ids = {str(candidate["episode_id"])}
    reference_ids.update(str(item["incident_id"]) for item in selection.get("selected_members", []))
    if selection.get("current_incident_id"):
        reference_ids.add(str(selection["current_incident_id"]))
    reference_ids.update(str(row[key]) for item in candidate.get("observations", [])
                         for row in item.get("provenance", []) for key in ("incident_id", "evidence_id") if row.get(key))
    return scrub({
        "source": "Retained FCAPSule historical episode",
        "observations": observations,
        "episode": episode,
        "availability": candidate.get("availability", "retained" if observations else "unavailable"),
        "limitation": "This is a prior captured episode, not proof of the same cause. Any prior_hypothesis is earlier model output, not independent evidence and not citable. Compare only its retained observations; missing captures cannot establish similarity or difference.",
    }, reference_ids=reference_ids)


class InvestigationTools:
    CATALOG = {
        "workload_state": "Preserve current limits, last termination and configuration. Mutable state; collect early. args: {}",
        "resource_history": "Read captured-window CPU/memory/limits/throttling/restarts/OOM metrics. Pod must be in allowed_pods; dependency pods use dependency_evidence. args: {pod?: allowed_pods entry}",
        "search_logs": "Search incident-window logs for up to 3 literal terms, preserving diagnostic variants. Pod must be in allowed_pods, not a pod discovered through a dependency. For dependency log follow-up use dependency_evidence with service and terms. args: {pod?: allowed_pods entry, terms: [text]}",
        "compare_baseline": "Compare a ready peer with the same workload, or the preceding equal time window. args: {}",
        "database_pressure": "Query namespace-scoped MySQL connection/limit series and exporter database reachability, with latest-alert phase summaries. Not automatically attributed to this workload. args: {}",
        "dependency_evidence": "Inspect one declared same-namespace Service's port mapping and at most one selected pod's bounded evidence. No pod means no pod queries; a declaration does not prove traffic. args: {service: declared name, terms?: up to 3 literal log terms}",
        "review_omitted": "Inspect candidate evidence excluded from the initial selection, including possible counterevidence. args: {terms?: [text]}",
        "historical_episode": "Read one retained, deterministic recurrence candidate. Earlier assessments are hypotheses; compare their captured evidence with this episode. args: {episode_id: supplied candidate}",
        "alert_rule_logic": "Read the retained/live definitions for the episode's named alert rules. Explains detection logic, not root cause. args: {}",
        "scrape_discovery": "Compare active/dropped targets with bounded ServiceMonitor/PodMonitor label and namespace selectors and current matching Service/Pod labels. Unsupported selection stays unknown. args: {}",
    }

    def __init__(
        self,
        entries: list[dict[str, Any]],
        application: dict[str, Any],
        sources: Any,
        historical_episodes: list[dict[str, Any]] | None = None,
    ):
        self.entries, self.application, self.sources = entries, application, sources
        self.historical_episodes = {str(item["episode_id"]): item for item in historical_episodes or []}
        self.namespace = str(application.get("namespace", ""))
        self.workload = str(application.get("name", ""))
        self.pods = sorted({str(entry["capsule"]["case"]["pod"]) for entry in entries if entry["capsule"]["case"].get("pod")})
        self.window_start = min(stamp(entry["capsule"]["case"]["window"]["start"]) for entry in entries)
        self.window_end = min(datetime.now(timezone.utc), max(stamp(entry["capsule"]["case"]["window"]["end"]) for entry in entries))
        self.window_start = max(self.window_start, self.window_end - timedelta(minutes=30))
        self.focus_time = max((stamp(item["incident"]["started_at"]) for item in entries if item["incident"].get("started_at")), default=self.window_start)
        self.alert_names = {
            str(alert.get("name") or alert.get("alertname") or "")
            for entry in entries for alert in entry["report"].get("fault_alerts", [])
        } - {""}
        self.discovery_targets: dict[str, str] = {}
        latest_first = sorted(entries, key=lambda entry: str(
            entry.get("incident", {}).get("started_at") or
            (entry.get("report", {}).get("incident") or {}).get("started_at") or ""), reverse=True)
        for entry in latest_first:
            labels = _discovery_labels(entry.get("report", {}))
            target_service = labels.get("target_service") or labels.get("kubernetes_service")
            target_workload = labels.get("target_workload")
            self.discovery_targets = {
                    key: value.strip()[:253] for key, value in (
                    ("target_service", target_service), ("target_workload", target_workload)
                ) if isinstance(value, str) and value.strip()
            }
            if self.discovery_targets:
                break

    def _adapters(self):
        config = self.sources.configuration()
        if not any(item["incident"].get("source_kind") == "live" for item in self.entries):
            raise ValueError("Imported case: live queries are disabled; use retained evidence.")
        if config.get("cluster_name") != self.application.get("cluster"):
            raise ValueError("The episode belongs to a different cluster than the configured sources.")
        if config.get("namespaces") and self.namespace not in config["namespaces"]:
            raise ValueError("The episode namespace is no longer in the configured source scope.")
        return self.sources.adapters(config)

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self.CATALOG or not isinstance(arguments, dict):
            raise ValueError("Unknown investigation tool")
        allowed = {"resource_history": {"pod"}, "search_logs": {"pod", "terms"}, "review_omitted": {"terms"},
                   "dependency_evidence": {"service", "terms"}, "historical_episode": {"episode_id"}}.get(name, set())
        if set(arguments) - allowed:
            raise ValueError("Unsupported tool arguments")
        pod = arguments.get("pod") or (self.pods[0] if self.pods else None)
        if "pod" in arguments and pod not in self.pods:
            raise ValueError("Pod is outside the captured episode scope")
        terms = arguments.get("terms", [])
        if not isinstance(terms, list) or len(terms) > 3 or any(not isinstance(term, str) or not 1 <= len(term) <= 80 for term in terms):
            raise ValueError("Terms must contain at most three short literal strings")
        if name == "review_omitted":
            candidates = []
            for entry in self.entries:
                selected = {item["source_id"] for item in entry["capsule"].get("selected_evidence", [])}
                for item in entry["capsule"].get("log_templates", []):
                    if item["template_id"] in selected:
                        continue
                    if terms and not any(term.lower() in json.dumps(item).lower() for term in terms):
                        continue
                    candidates.append({"incident_id": entry["incident"]["incident_id"], "pattern": item["template"],
                        "count": item["count"], "examples": item["representative_lines"],
                        "time_range": {"start": item["first_seen"], "end": item["last_seen"]}})
            return scrub({"source": "retained unselected log templates", "observations": candidates[:12],
                          "matching_candidates": len(candidates), "limitation": "Only retained candidates; absence is not disproof."})
        if name == "historical_episode":
            episode_id = str(arguments.get("episode_id") or "")
            candidate = self.historical_episodes.get(episode_id)
            if not candidate:
                raise ValueError("Episode is outside the deterministic recurrence candidates")
            return historical_episode_result(candidate)
        prometheus, opensearch, kubernetes = self._adapters()
        if name == "alert_rule_logic":
            retained = []
            for entry in self.entries:
                for alert in entry["report"].get("fault_alerts", [])[:8]:
                    rule = alert.get("rule") if isinstance(alert.get("rule"), dict) else {}
                    if rule:
                        retained.append({"name": alert.get("name"), "rule": rule, "incident_id": entry["incident"]["incident_id"]})
            current = {}
            try:
                rules = prometheus.alert_rules()
                current = {name: rules[name] for name in self.alert_names if name in rules}
            except (RuntimeError, OSError, ValueError):
                current = {}
            return scrub({
                "source": "Prometheus alert rules",
                "alert_names": sorted(self.alert_names),
                "retained_definitions": retained[:12],
                "current_definitions": current,
                "limitation": "A rule explains when Prometheus detects a condition; it does not establish the underlying cause. Current definitions can differ from incident-time rules.",
            })
        if name == "scrape_discovery":
            targets = prometheus.scrape_targets(self.namespace, set(self.pods))
            monitors = kubernetes.monitoring_resources({self.namespace})
            services = kubernetes.list_services(self.namespace)
            target_workload = self.discovery_targets.get("target_workload")
            current_pods = [item for item in kubernetes.list_pods({self.namespace})
                            if item.get("workload") in {self.workload, target_workload} or item["name"] in self.pods]
            current_pods.sort(key=lambda item: (item.get("workload") != target_workload if target_workload else False,
                                                item["name"] not in self.pods, str(item.get("name") or "")))
            selections = []
            for monitor in monitors:
                monitor_namespace = str(monitor.get("namespace") or "default")
                namespace_selector = monitor.get("namespace_selector") if isinstance(monitor.get("namespace_selector"), dict) else {}
                namespace_status = namespace_selector.get("status", "resolved")
                effective_namespaces = monitor.get("effective_namespaces", monitor.get("target_namespaces", []))
                if not isinstance(effective_namespaces, list):
                    effective_namespaces = []
                if monitor["kind"] == "ServiceMonitor":
                    candidates = []
                    for service in services:
                        service_namespace = str(service.get("namespace") or self.namespace)
                        namespace_selected = service_namespace in effective_namespaces
                        is_alert_service = service.get("name") == self.discovery_targets.get("target_service")
                        if not namespace_selected and not is_alert_service:
                            continue
                        labels = service.get("labels", {}) if isinstance(service.get("labels"), dict) else {}
                        evaluation = _selector_observation(labels, monitor, namespace_status)
                        service_selector = service.get("selector", {}) if isinstance(service.get("selector"), dict) else {}
                        workload_pods = [pod for pod in current_pods
                                         if (target_workload and pod.get("workload") == target_workload)
                                         or pod.get("name") in self.pods]
                        selector_matches = ([pod for pod in workload_pods if service_selector and all(
                            (pod.get("labels") or {}).get(key) == value for key, value in service_selector.items())]
                            if service_selector and workload_pods else [])
                        workload_selector_match = bool(selector_matches) if service_selector and workload_pods else None
                        target_relevance = ("alert_target_service" if is_alert_service else
                            "alert_target_workload" if target_workload and workload_selector_match is True else None)
                        candidates.append({"name": service.get("name"), "namespace": service_namespace,
                                           "labels": labels, "selector_evaluation": evaluation,
                                           "namespace_selected": namespace_selected,
                                           "service_selector": service_selector,
                                           "workload_selector_match": workload_selector_match,
                                           "target_relevance": target_relevance})
                    candidates.sort(key=lambda item: (item["name"] != self.discovery_targets.get("target_service"),
                                                        item["target_relevance"] not in {"alert_target_service", "alert_target_workload"},
                                                        item["workload_selector_match"] is not True,
                                                        not item["namespace_selected"],
                                                        0 if item["selector_evaluation"]["status"] == "matched" else 1,
                                                        sum(1 for row in item["selector_evaluation"].get("requirements", []) if row.get("matches") is False),
                                                        str(item.get("name") or "")))
                    matched = [item["name"] for item in candidates if item["namespace_selected"] and item["selector_evaluation"]["status"] == "matched"]
                    selections.append({"monitor": monitor, "target_kind": "Service labels",
                                       "namespace_scope": {"status": namespace_status, "effective_namespaces": effective_namespaces[:12]},
                                       "matched_services": matched[:12], "matched_pods": [],
                                       "evaluated_services": candidates[:12], "omitted_service_candidates": max(0, len(candidates) - 12)})
                else:
                    candidates = []
                    for pod in current_pods:
                        pod_namespace = str(pod.get("namespace") or self.namespace)
                        if pod_namespace not in effective_namespaces:
                            continue
                        labels = pod.get("labels", {}) if isinstance(pod.get("labels"), dict) else {}
                        evaluation = _selector_observation(labels, monitor, namespace_status)
                        candidates.append({"name": pod.get("name"), "namespace": pod_namespace,
                                           "labels": labels, "selector_evaluation": evaluation,
                                           "target_relevance": "alert_target_workload" if target_workload and pod.get("workload") == target_workload else None})
                    candidates.sort(key=lambda item: (item["name"] not in self.pods,
                                                        item["target_relevance"] != "alert_target_workload",
                                                        0 if item["selector_evaluation"]["status"] == "matched" else 1,
                                                        sum(1 for row in item["selector_evaluation"].get("requirements", []) if row.get("matches") is False),
                                                        str(item.get("name") or "")))
                    matched = [item["name"] for item in candidates if item["selector_evaluation"]["status"] == "matched"]
                    selections.append({"monitor": monitor, "target_kind": "Pod labels",
                                       "namespace_scope": {"status": namespace_status, "effective_namespaces": effective_namespaces[:12]},
                                       "matched_services": [], "matched_pods": matched[:12],
                                       "evaluated_pods": candidates[:12], "omitted_pod_candidates": max(0, len(candidates) - 12)})
            observed_at = _observed_at()
            return scrub({
                "source": "Prometheus target discovery + Kubernetes monitoring resources",
                "scope": {"namespace": self.namespace, "pods": self.pods, "workload": self.workload},
                "discovery_targets": self.discovery_targets,
                "observed_at": observed_at,
                "provenance": [
                    {"source": "Prometheus target API", "observed_at": observed_at},
                    {"source": "Kubernetes monitoring and Service/Pod APIs", "observed_at": observed_at},
                ],
                "active_targets": targets["active"],
                "dropped_targets": targets["dropped"],
                "monitor_selection": selections,
                "current_pod_labels": [{"pod": item["name"], "namespace": item.get("namespace", self.namespace), "labels": item.get("labels", {})} for item in current_pods[:12]],
                "current_service_labels": [{"service": item.get("name"), "namespace": item.get("namespace", self.namespace), "labels": item.get("labels", {})} for item in sorted(
                    services, key=lambda item: (item.get("name") != self.discovery_targets.get("target_service"), str(item.get("name") or "")))[:12]],
                "limitation": "ServiceMonitor selectors apply to Service labels; PodMonitor selectors apply to Pod labels. Supported label expressions are evaluated only for current, bounded candidates in the captured namespace. An unknown selector, Prometheus resource selector, relabeling rule, scrape configuration, or RBAC restriction can prevent a conclusion. A matched selector does not prove the target was retained or scraped.",
            })
        if name == "workload_state":
            pods = [item for item in kubernetes.list_pods({self.namespace})
                    if item["name"] in self.pods or item.get("workload") == self.workload][:4]
            dependencies = []
            for item in pods:
                try:
                    dependencies.extend(kubernetes.declared_services(item))
                except (RuntimeError, OSError):
                    pass  # Keep termination evidence if endpoint discovery is unavailable.
            return scrub({"source": "Kubernetes API", "scope": self.namespace, "observed_at": datetime.now(timezone.utc).isoformat(),
                "declared_dependencies": dependencies[:12],
                "observations": [record for item in pods for record in kubernetes.configuration_snapshot(item)][:16],
                "limitation": "Current snapshot, not the incident-time state. Last termination can be overwritten by later restarts."})
        if name == "dependency_evidence":
            available = kubernetes.list_pods({self.namespace})
            roots = [item for item in available if item["name"] in self.pods or item.get("workload") == self.workload][:4]
            declarations = [reference for root in roots for reference in kubernetes.declared_services(root)]
            matching = [item for item in declarations if item["service"] == arguments.get("service")]
            if not matching:
                raise ValueError("Service is not declared by this workload")
            resolution = kubernetes.resolve_service(self.namespace, arguments["service"], available)
            service_info = resolution["service"]
            targets = resolution["pods"]
            target = targets[0] if targets else None
            port_comparisons = [_port_comparison(item, service_info.get("ports", [])) for item in matching[:4]]
            result = {"source": "Declared dependency: Kubernetes + OpenSearch + Prometheus", "pod": target["name"] if target else None,
                      "service": arguments["service"], "declared_endpoints": matching[:4], "service_observation": service_info,
                      "port_comparisons": port_comparisons, "matching_pods": len(targets),
                      "latest_alert_at": self.focus_time.isoformat(),
                      "window": [self.window_start.isoformat(), self.window_end.isoformat()], "observations": [],
                      "unavailable_sources": [], "not_collected_sources": [], "observed_at": _observed_at(),
                      "provenance": [{"source": service_info.get("source", "Kubernetes API Service"),
                                      "resource": f"Service/{self.namespace}/{arguments['service']}",
                                      "observed_at": service_info.get("observed_at")}],
                      "limitation": "Current Service and configuration facts are not historical. Endpoint port is compared with the client-facing Service port, not its backend targetPort. A declared endpoint does not prove traffic or causation."}
            if target:
                result["provenance"].append({"source": "Kubernetes API Pod", "resource": f"Pod/{self.namespace}/{target['name']}",
                                             "observed_at": _observed_at()})
                for source, collect in (
                    ("OpenSearch", lambda: log_patterns(opensearch.collect_logs(self.namespace, target["name"], self.window_start, self.window_end, limit=200, terms=terms, focus=self.focus_time))),
                    ("Prometheus", lambda: {"observations": metric_summary(prometheus.collect_pod_metrics(self.namespace, target["name"], self.window_start, self.window_end), self.focus_time)}),
                    ("Kubernetes", lambda: {"observations": kubernetes.configuration_snapshot(target)[:8]}),
                ):
                    try:
                        observation = collect()
                        result["observations"].extend(observation.pop("observations", []))
                        if observation.get("limitation"):
                            result["limitation"] += " " + observation.pop("limitation")
                        result.update(observation)
                        result["provenance"].append({"source": source, "resource": f"Pod/{self.namespace}/{target['name']}",
                                                     "observed_at": _observed_at()})
                    except (RuntimeError, OSError, ValueError):
                        result["unavailable_sources"].append(source)
            else:
                result["not_collected_sources"] = ["OpenSearch", "Prometheus", "Pod configuration"]
                result["limitation"] += " No current pod matched the Service selector, so pod-scoped logs, metrics and configuration were not queried."
            return scrub(result)
        if not pod and name != "database_pressure":
            raise ValueError("No captured pod identity is available")
        if name == "resource_history":
            series = prometheus.collect_pod_metrics(self.namespace, pod, self.window_start, self.window_end)
            captured_at = _observed_at()
            observations = metric_summary(series, self.focus_time, captured_at=captured_at)
            return scrub({"source": "Prometheus", "pod": pod, "latest_alert_at": self.focus_time.isoformat(),
                "captured_at": captured_at, "observations": observations,
                "metric_semantics": {
                    "pod_oom_terminated": "Kubernetes last-termination reason OOMKilled (1=yes); a state flag, not an event count. Correlate its onset with the restart counter. A concurrent flag/restart is positive OOM evidence even if the peak was not sampled.",
                    "pod_memory_working_set_bytes": "Sampled working set is not peak total cgroup-accounted memory. Low samples cannot exclude an OOM or establish a false OOM alert.",
                    "pod_cpu_throttled_ratio": "Fraction of CFS periods throttled, not percentage of CPU time or throughput lost. Low average CPU does not exclude brief throttling.",
                    "pod_container_restarts_total": "Cumulative counter; use increase, not its absolute value, for window restart activity.",
                    "pod_memory_limit_bytes": "Configured pod-summed limit, not measured memory peak. Multi-container attribution needs individual limits/termination state."},
                "limitation": "Historical samples may be missing or miss short peaks; no source TTL was inferred."})
        if name == "search_logs":
            logs = opensearch.collect_logs(self.namespace, pod, self.window_start, self.window_end, limit=300, terms=terms, focus=self.focus_time)
            return scrub({"source": "OpenSearch", "pod": pod, "window": [self.window_start.isoformat(), self.window_end.isoformat()],
                         "latest_alert_at": self.focus_time.isoformat(), "sampling": "Up to one quarter before the latest alert; remaining budget at or after it. Bounded matching samples, not complete event counts.",
                         **log_patterns(logs)})
        if name == "compare_baseline":
            peers = [item for item in kubernetes.list_pods({self.namespace})
                     if item.get("workload") == self.workload and item["name"] not in self.pods and item.get("ready")]
            affected = metric_summary(prometheus.collect_pod_metrics(self.namespace, pod, self.window_start, self.window_end))
            if peers:
                peer = peers[0]
                reference = metric_summary(prometheus.collect_pod_metrics(self.namespace, peer["name"], self.window_start, self.window_end))
                method, reference_pod = "ready_peer", peer["name"]
            else:
                start = self.window_start - (self.window_end - self.window_start)
                reference = metric_summary(prometheus.collect_pod_metrics(self.namespace, pod, start, self.window_start))
                method, reference_pod = "preceding_window", pod
            return scrub({"source": "Prometheus + Kubernetes", "method": method, "affected_pod": pod,
                "reference_pod": reference_pod, "affected": affected, "reference": reference,
                "comparability": "Unverified: traffic, historical readiness, limits and configuration may differ. Not a controlled experiment."})
        selector = json.dumps(self.namespace)
        observations = []
        for metric in ("mysql_global_status_threads_connected", "mysql_global_status_threads_running", "mysql_global_variables_max_connections", "mysql_up"):
            for item in prometheus.query_range(metric + "{namespace=" + selector + "}", self.window_start, self.window_end, 60)[:4]:
                observations.extend(metric_summary([{"metric": metric, "labels": item.get("metric", {}),
                    "values": [[datetime.fromtimestamp(float(t), timezone.utc).isoformat(), v] for t, v in item.get("values", [])]}], self.focus_time))
        return scrub({"source": "Prometheus MySQL exporter", "scope": self.namespace, "observations": observations,
            "latest_alert_at": self.focus_time.isoformat(),
            "limitation": "Exporter labels identify the database. Namespace proximity alone does not prove an application dependency. Missing series do not imply zero connections. mysql_up=0 means the exporter could not collect from MySQL, not necessarily that the database process stopped. Healthy samples before the latest alert do not establish recovery or disprove retained sessions during it."})
