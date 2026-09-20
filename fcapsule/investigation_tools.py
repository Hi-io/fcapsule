"""Bounded, read-only investigation tools. Models never supply URLs, SQL or PromQL."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from fcapsule.processing.anonymizer import anonymize_text, diagnostic_fields, template_for_message


def scrub(value: Any) -> Any:
    if isinstance(value, str):
        return anonymize_text(value)[:2000]
    if isinstance(value, dict):
        return {str(key): scrub(item) for key, item in value.items()
                if not any(term in str(key).lower() for term in ("password", "secret", "token", "credential"))}
    if isinstance(value, list):
        return [scrub(item) for item in value[:80]]
    return value


def stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def metric_summary(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in series[:24]:
        points = [point for point in item.get("values", []) if math.isfinite(float(point[1]))]
        if not points:
            continue
        values = [float(point[1]) for point in points]
        result.append({"metric": item["metric"], "labels": item.get("labels", {}),
                       "min": min(values), "max": max(values), "median": statistics.median(values),
                       "samples": len(points), "start": points[0][0], "end": points[-1][0],
                       "trend": points[::max(1, len(points) // 24)][:25]})
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


def episode_context(episode: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    evidence: dict[str, dict[str, Any]] = {}
    alerts = []
    for entry in entries:
        report = entry["report"]
        incident_id = entry["incident"]["incident_id"]
        alerts.append({**report["incident"], "incident_id": incident_id,
                       "current_status": entry["incident"].get("status"), "ended_at": entry["incident"].get("ended_at")})
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
    return scrub({"episode_id": episode["episode_id"], "live_capture": any(entry["incident"].get("source_kind") == "live" for entry in entries),
        "episode_lifecycle": {key: episode.get(key) for key in
                  ("status", "started_at", "ended_at", "last_activity_at")}, "alerts": alerts,
        "evidence": list(evidence.values())[:80],
        "impact": [item for entry in entries for item in entry["report"].get("impact", [])][:15],
        "source_retention": "Unknown. Do not infer expiry from incident age or FCAPSule's own cleanup policy.",
        "limits": "Time grouping is not causation. Measurements are sampled. Current state is not historical state."})


class InvestigationTools:
    CATALOG = {
        "workload_state": "Preserve current limits, last termination and configuration. Mutable state; collect early. args: {}",
        "resource_history": "Read captured-window CPU/memory/limits/throttling/restarts/OOM metrics. args: {pod?: known pod}",
        "search_logs": "Search incident-window logs on a known pod for up to 3 literal terms, preserving diagnostic variants. args: {pod?: known pod, terms: [text]}",
        "compare_baseline": "Compare a ready peer with the same workload, or the preceding equal time window. args: {}",
        "database_pressure": "Query namespace-scoped MySQL connection/max-connection series if exported. Not automatically attributed to this workload. args: {}",
        "dependency_evidence": "Follow one declared same-namespace Service from workload_state.declared_dependencies; inspect one selected pod's incident logs, metrics and current config. Corroborate the dependency with application evidence. args: {service: declared name, terms?: up to 3 literal log terms}",
        "review_omitted": "Inspect candidate evidence excluded from the initial selection, including possible counterevidence. args: {terms?: [text]}",
    }

    def __init__(self, entries: list[dict[str, Any]], application: dict[str, Any], sources: Any):
        self.entries, self.application, self.sources = entries, application, sources
        self.namespace = str(application.get("namespace", ""))
        self.workload = str(application.get("name", ""))
        self.pods = sorted({str(entry["capsule"]["case"]["pod"]) for entry in entries if entry["capsule"]["case"].get("pod")})
        self.window_start = min(stamp(entry["capsule"]["case"]["window"]["start"]) for entry in entries)
        self.window_end = min(datetime.now(timezone.utc), max(stamp(entry["capsule"]["case"]["window"]["end"]) for entry in entries))
        self.window_start = max(self.window_start, self.window_end - timedelta(minutes=30))

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
                   "dependency_evidence": {"service", "terms"}}.get(name, set())
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
        prometheus, opensearch, kubernetes = self._adapters()
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
                      "window": [self.window_start.isoformat(), self.window_end.isoformat()], "observations": [],
                      "unavailable_sources": [], "observed_at": datetime.now(timezone.utc).isoformat(),
                      "limitation": "One pod sampled. Declaration is not proof of traffic or causation. Service membership and config are current, not historical. Missing samples do not prove health."}
            for source, collect in (
                ("OpenSearch", lambda: log_patterns(opensearch.collect_logs(self.namespace, target["name"], self.window_start, self.window_end, limit=200, terms=terms))),
                ("Prometheus", lambda: {"observations": metric_summary(prometheus.collect_pod_metrics(self.namespace, target["name"], self.window_start, self.window_end))}),
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
            return scrub({"source": "Prometheus", "pod": pod, "observations": metric_summary(
                prometheus.collect_pod_metrics(self.namespace, pod, self.window_start, self.window_end)),
                "metric_semantics": {
                    "pod_oom_terminated": "Kubernetes last-termination reason OOMKilled (1=yes); a state flag, not an event count. Correlate its onset with the restart counter. A concurrent flag/restart is positive OOM evidence even if the peak was not sampled.",
                    "pod_memory_working_set_bytes": "Sampled working set is not peak total cgroup-accounted memory. Low samples cannot exclude an OOM or establish a false OOM alert.",
                    "pod_cpu_throttled_ratio": "Fraction of CFS periods throttled, not percentage of CPU time or throughput lost. Low average CPU does not exclude brief throttling.",
                    "pod_container_restarts_total": "Cumulative counter; use increase, not its absolute value, for window restart activity.",
                    "pod_memory_limit_bytes": "Configured pod-summed limit, not measured memory peak. Multi-container attribution needs individual limits/termination state."},
                "limitation": "Historical samples may be missing or miss short peaks; no source TTL was inferred."})
        if name == "search_logs":
            logs = opensearch.collect_logs(self.namespace, pod, self.window_start, self.window_end, limit=300, terms=terms)
            return scrub({"source": "OpenSearch", "pod": pod, "window": [self.window_start.isoformat(), self.window_end.isoformat()],
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
        for metric in ("mysql_global_status_threads_connected", "mysql_global_status_threads_running", "mysql_global_variables_max_connections"):
            for item in prometheus.query_range(metric + "{namespace=" + selector + "}", self.window_start, self.window_end, 60)[:4]:
                observations.extend(metric_summary([{"metric": metric, "labels": item.get("metric", {}),
                    "values": [[datetime.fromtimestamp(float(t), timezone.utc).isoformat(), v] for t, v in item.get("values", [])]}]))
        return scrub({"source": "Prometheus MySQL exporter", "scope": self.namespace, "observations": observations,
            "limitation": "Exporter labels identify the database. Namespace proximity alone does not prove an application dependency. Missing series do not imply zero connections."})
