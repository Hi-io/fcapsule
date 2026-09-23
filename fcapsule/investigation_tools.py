"""Bounded, read-only investigation tools. Models never supply URLs, SQL or PromQL."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

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


def metric_summary(series: list[dict[str, Any]], focus: datetime | None = None) -> list[dict[str, Any]]:
    result = []
    for item in series[:24]:
        points = [point for point in item.get("values", []) if math.isfinite(float(point[1]))]
        if not points:
            continue
        values = [float(point[1]) for point in points]
        result.append({"metric": item["metric"], "labels": item.get("labels", {}),
                       "min": min(values), "max": max(values), "median": statistics.median(values),
                       "samples": len(points), "start": points[0][0], "end": points[-1][0],
                       "first": values[0], "last": values[-1],
                       "trend": points[::max(1, len(points) // 24)][:25]})
        if focus:
            recent = [point for point in points if stamp(str(point[0])) >= focus]
            recent_values = [float(point[1]) for point in recent]
            result[-1]["at_or_after_latest_alert"] = ({"samples": len(recent), "start": recent[0][0], "end": recent[-1][0],
                "min": min(recent_values), "max": max(recent_values), "median": statistics.median(recent_values),
                "first": recent_values[0], "last": recent_values[-1]} if recent else {"samples": 0})
    return result


def log_patterns(logs: list[dict[str, Any]], terms: list[str] | None = None) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for row in logs:
        message = str(row.get("message", ""))
        if terms and not any(term.lower() in message.lower() for term in terms):
            continue
        pattern = template_for_message(message)
        group = groups.setdefault(pattern, {"pattern": pattern[:1000], "count": 0,
            "fields": diagnostic_fields(message), "examples": [], "first_seen": row.get("@timestamp")})
        group["count"] += 1
        group["last_seen"] = row.get("@timestamp")
        example = {"timestamp": row.get("@timestamp"), "level": row.get("level"), "message": anonymize_text(message)[:1000]}
        if len(group["examples"]) < 2:
            group["examples"].append(example)
        else:
            group["examples"][-1] = example
    ranked = sorted(groups.values(), key=lambda item: (-bool(item["fields"]), -item["count"]))
    return {"scanned_lines": len(logs), "matching_patterns": len(groups), "patterns": ranked[:12],
            "limitation": "Bounded sample; no matches does not prove the event did not occur."}


def episode_context(
    episode: dict[str, Any],
    entries: list[dict[str, Any]],
    primary_incident_id: str | None = None,
) -> dict[str, Any]:
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
            identity = [item.get("type"), item.get("title"), item.get("summary"), item.get("time_range"), item.get("linked_entities"), item.get("configuration")]
            ref = "E" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:12]
            if ref not in evidence:
                evidence[ref] = {"id": ref, "domain": item.get("type"), "title": item.get("title"),
                    "summary": item.get("summary"), "time_range": item.get("time_range"),
                    "examples": item.get("representative_lines", []), "configuration": item.get("configuration"),
                    "alert": next((alert for alert in report.get("fault_alerts", []) if alert.get("evidence_id") == item["evidence_id"]), None),
                    "provenance": []}
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
        "evidence": ordered_evidence[:80], "priority_evidence_ids": priority_evidence_ids,
        "impact": [item for entry in ordered_entries for item in entry["report"].get("impact", [])][:15],
        "source_retention": "Unknown. Do not infer expiry from incident age or FCAPSule's own cleanup policy.",
        "grouping_basis": "Same application and temporal proximity only. Independent failure phases can share an episode. A resolved alert followed by another alert is not a demonstrated causal chain.",
        "limits": "Time grouping is not causation. Measurements are sampled. Current state is not historical state."})


def _discovery_labels(report: dict[str, Any]) -> dict[str, str]:
    """Retain only explicit, non-sensitive identities needed to inspect target discovery."""

    for alert in report.get("fault_alerts", []):
        rule = alert.get("rule") if isinstance(alert.get("rule"), dict) else {}
        labels = rule.get("labels") if isinstance(rule.get("labels"), dict) else {}
        retained = {
            key: str(labels[key])
            for key in ("target_service", "kubernetes_service", "target_workload")
            if labels.get(key)
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


class InvestigationTools:
    CATALOG = {
        "workload_state": "Preserve current limits, last termination and configuration. Mutable state; collect early. args: {}",
        "resource_history": "Read captured-window CPU/memory/limits/throttling/restarts/OOM metrics. Pod must be in allowed_pods; dependency pods use dependency_evidence. args: {pod?: allowed_pods entry}",
        "search_logs": "Search incident-window logs for up to 3 literal terms, preserving diagnostic variants. Pod must be in allowed_pods, not a pod discovered through a dependency. For dependency log follow-up use dependency_evidence with service and terms. args: {pod?: allowed_pods entry, terms: [text]}",
        "compare_baseline": "Compare a ready peer with the same workload, or the preceding equal time window. args: {}",
        "database_pressure": "Query namespace-scoped MySQL connection/limit series and exporter database reachability, with latest-alert phase summaries. Not automatically attributed to this workload. args: {}",
        "dependency_evidence": "Follow one declared same-namespace Service from workload_state.declared_dependencies; inspect one selected pod's incident logs, metrics and current config. Corroborate the dependency with application evidence. args: {service: declared name, terms?: up to 3 literal log terms}",
        "review_omitted": "Inspect candidate evidence excluded from the initial selection, including possible counterevidence. args: {terms?: [text]}",
        "historical_episode": "Read one retained, deterministic recurrence candidate. Earlier assessments are hypotheses; compare their captured evidence with this episode. args: {episode_id: supplied candidate}",
        "alert_rule_logic": "Read the retained/live definitions for the episode's named alert rules. Explains detection logic, not root cause. args: {}",
        "scrape_discovery": "Compare Prometheus active/dropped target discovery with read-only ServiceMonitor/PodMonitor selectors and current Kubernetes labels. args: {}",
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
            return scrub({
                "source": "Retained FCAPSule historical episode",
                "episode": candidate,
                "limitation": "This is a prior captured episode, not proof of the same cause. Any prior_hypothesis is earlier model output, not independent evidence and not citable. Compare only its retained observations.",
            })
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
            current_pods = [item for item in kubernetes.list_pods({self.namespace}) if item.get("workload") == self.workload or item["name"] in self.pods]
            selections = []
            for monitor in monitors:
                labels = monitor.get("match_labels", {})
                if monitor["kind"] == "ServiceMonitor":
                    matched = [service["name"] for service in services if all(service.get("labels", {}).get(key) == value for key, value in labels.items())]
                    selections.append({"monitor": monitor, "matched_services": matched, "matched_pods": []})
                else:
                    matched = [pod["name"] for pod in current_pods if all(pod.get("labels", {}).get(key) == value for key, value in labels.items())]
                    selections.append({"monitor": monitor, "matched_services": [], "matched_pods": matched})
            return scrub({
                "source": "Prometheus target discovery + Kubernetes monitoring resources",
                "scope": {"namespace": self.namespace, "pods": self.pods, "workload": self.workload},
                "active_targets": targets["active"],
                "dropped_targets": targets["dropped"],
                "monitor_selection": selections,
                "current_pod_labels": [{"pod": item["name"], "labels": item.get("labels", {})} for item in current_pods[:12]],
                "limitation": "ServiceMonitor selectors apply to Service labels, while PodMonitor selectors apply to Pod labels. Absence from this bounded view may reflect a different namespace, relabeling, RBAC or target filtering; it is not proof of a typo.",
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
            targets = kubernetes.service_pods(self.namespace, arguments["service"], available)
            if not targets:
                raise ValueError("No current pod matches the declared Service selector")
            target = targets[0]
            result = {"source": "Declared dependency: Kubernetes + OpenSearch + Prometheus", "pod": target["name"],
                      "service": arguments["service"], "configured_via": matching, "matching_pods": len(targets),
                      "latest_alert_at": self.focus_time.isoformat(),
                      "window": [self.window_start.isoformat(), self.window_end.isoformat()], "observations": [],
                      "unavailable_sources": [], "observed_at": datetime.now(timezone.utc).isoformat(),
                      "limitation": "One pod sampled. Declaration is not proof of traffic or causation. Service membership and config are current, not historical. Missing samples do not prove health."}
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
                except (RuntimeError, OSError, ValueError):
                    result["unavailable_sources"].append(source)
            if len(result["unavailable_sources"]) == 3:
                raise ValueError("Dependency sources unavailable")
            return scrub(result)
        if not pod and name != "database_pressure":
            raise ValueError("No captured pod identity is available")
        if name == "resource_history":
            return scrub({"source": "Prometheus", "pod": pod, "latest_alert_at": self.focus_time.isoformat(), "observations": metric_summary(
                prometheus.collect_pod_metrics(self.namespace, pod, self.window_start, self.window_end), self.focus_time),
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
