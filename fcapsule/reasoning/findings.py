"""Turn retained investigation observations into a small evidence-backed finding set."""

from __future__ import annotations

from typing import Any


def _text(value: Any, limit: int = 360) -> str:
    result = str(value or "").strip()
    return result if len(result) <= limit else result[: limit - 1] + "..."


def _finding(
    finding_id: str,
    *,
    state: str,
    category: str,
    title: str,
    summary: str,
    evidence_ids: list[str],
    scope: dict[str, Any] | None = None,
    observations: list[dict[str, Any]] | None = None,
    next_check: str = "Inspect the cited evidence before changing production configuration.",
) -> dict[str, Any]:
    return {
        "id": finding_id,
        "state": state,
        "category": category,
        "title": _text(title, 180),
        "summary": _text(summary),
        "scope": scope or {},
        "evidence_ids": list(dict.fromkeys(str(item) for item in evidence_ids if item)),
        "observations": observations or [],
        "next_check": _text(next_check, 280),
    }


def _alert_target_identity(discovery: dict[str, Any]) -> tuple[str, set[str]]:
    targets = discovery.get("discovery_targets") if isinstance(discovery.get("discovery_targets"), dict) else {}
    scope = discovery.get("scope") if isinstance(discovery.get("scope"), dict) else {}
    service = str(targets.get("target_service") or scope.get("service") or "")
    pods = {str(item) for item in scope.get("pods", []) if item}
    return service, pods


def _matches_alert_target(target: dict[str, Any], service: str, pods: set[str]) -> bool:
    return bool((service and str(target.get("service") or "") == service)
                or (target.get("pod") and str(target.get("pod")) in pods))


def _selector_mismatches(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    scope = discovery.get("scope") if isinstance(discovery.get("scope"), dict) else {}
    affected = {str(item) for item in scope.get("pods", []) if item}
    pod_labels = {
        str(item.get("pod")): item.get("labels")
        for item in discovery.get("current_pod_labels", [])
        if isinstance(item, dict) and isinstance(item.get("labels"), dict)
    }
    mismatches = []
    for selection in discovery.get("monitor_selection", []):
        if not isinstance(selection, dict):
            continue
        monitor = selection.get("monitor") if isinstance(selection.get("monitor"), dict) else {}
        required = monitor.get("match_labels") if isinstance(monitor.get("match_labels"), dict) else {}
        if not required:
            continue
        kind = str(monitor.get("kind") or "")
        if kind == "PodMonitor" and not selection.get("matched_pods"):
            for pod, labels in pod_labels.items():
                if affected and pod not in affected:
                    continue
                differences = [
                    {"label": key, "required": value, "observed": labels.get(key, "<missing>")}
                    for key, value in required.items() if labels.get(key) != value
                ]
                if differences:
                    mismatches.append({
                        "monitor": f"PodMonitor/{monitor.get('namespace', 'default')}/{monitor.get('name', 'unknown')}",
                        "target": pod,
                        "differences": differences,
                    })
        elif kind == "ServiceMonitor" and not selection.get("matched_services"):
            mismatches.append({
                "monitor": f"ServiceMonitor/{monitor.get('namespace', 'default')}/{monitor.get('name', 'unknown')}",
                "target": "No matching Service in the captured namespace",
                "differences": [{"label": key, "required": value, "observed": "not matched"} for key, value in required.items()],
            })
    return mismatches[:3]


def derive_findings(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one primary observation and a few clearly subordinate facts.

    `observed` means the stated fact comes directly from a retained source record.
    `likely_explanation` remains separate because it is the investigator's synthesis.
    """

    findings: list[dict[str, Any]] = []
    for check in state.get("checks", []):
        if not isinstance(check, dict) or check.get("status") != "completed":
            continue
        evidence_id = str(check.get("id") or "")
        result = check.get("result") if isinstance(check.get("result"), dict) else {}
        if check.get("tool") == "scrape_discovery":
            scope = result.get("scope") if isinstance(result.get("scope"), dict) else {}
            target_service, target_pods = _alert_target_identity(result)
            active_targets = [item for item in result.get("active_targets", []) if isinstance(item, dict)]
            scoped_active = [item for item in active_targets
                             if (target_service or target_pods)
                             and _matches_alert_target(item, target_service, target_pods)]
            scoped_down = [item for item in scoped_active if str(item.get("health")) == "down"]
            # A selected active target rules out a selector mismatch as the cause of this target's scrape failure.
            mismatches = [] if scoped_down else _selector_mismatches(result)
            if mismatches:
                first = mismatches[0]
                detail = "; ".join(
                    f"{item['label']}: requires {item['required']!r}, observed {item['observed']!r}"
                    for item in first["differences"]
                )
                findings.append(_finding(
                    f"finding-{evidence_id}-selector", state="observed", category="monitoring_selection",
                    title="Monitoring selector does not match the current workload labels",
                    summary=f"{first['monitor']} did not select {first['target']}. {detail}.",
                    evidence_ids=[evidence_id], scope=scope, observations=mismatches,
                    next_check="Verify the intended monitoring policy and the Service or Pod label at the object named above.",
                ))
            dropped = [item for item in result.get("dropped_targets", []) if isinstance(item, dict)]
            if target_service or target_pods:
                dropped = [item for item in dropped if _matches_alert_target(item, target_service, target_pods)]
            if scoped_down:
                active_pools = {str(item.get("scrape_pool") or "") for item in scoped_down if item.get("scrape_pool")}
                dropped = [item for item in dropped if str(item.get("scrape_pool") or "") in active_pools]
            if dropped:
                target = dropped[0]
                name = target.get("pod") or target.get("service") or "the affected endpoint"
                findings.append(_finding(
                    f"finding-{evidence_id}-dropped", state="observed", category="target_discovery",
                    title="Prometheus discovered a target that was later dropped",
                    summary=f"{name} appears in dropped target discovery. This differs from a contacted target reporting scrape failure.",
                    evidence_ids=[evidence_id], scope=scope, observations=dropped[:3],
                    next_check="Inspect the retained selection chain and any relabeling or target filters for this target.",
                ))
            down = [item for item in active_targets if str(item.get("health")) == "down"]
            if target_service or target_pods:
                down = [item for item in down if _matches_alert_target(item, target_service, target_pods)]
            if down:
                target = down[0]
                name = target.get("pod") or target.get("service") or "the affected endpoint"
                error = _text(target.get("last_error"), 180) or "Prometheus did not report a scrape error detail."
                findings.append(_finding(
                    f"finding-{evidence_id}-down", state="observed", category="scrape_health",
                    title="Prometheus contacted a target but the scrape is failing",
                    summary=f"{name} is an active target with health down. Reported scrape error: {error}",
                    evidence_ids=[evidence_id], scope=scope, observations=down[:3],
                    next_check="Check the reported endpoint, port, path and target error before treating this as an application failure.",
                ))
        elif check.get("tool") == "alert_rule_logic":
            definitions = result.get("current_definitions") if isinstance(result.get("current_definitions"), dict) else {}
            if definitions:
                names = ", ".join(sorted(definitions)[:3])
                findings.append(_finding(
                    f"finding-{evidence_id}-rule", state="observed", category="alert_logic",
                    title="Alert logic was retained for the investigation",
                    summary=f"Current Prometheus rule definition is available for {names}. Detection logic explains a trigger condition, not the underlying cause.",
                    evidence_ids=[evidence_id], observations=[{"rules": definitions}],
                    next_check="Compare the incident-time alert condition with the cited target, configuration and workload evidence.",
                ))

    assessment = state.get("assessment") if isinstance(state.get("assessment"), dict) else {}
    if assessment:
        findings.append(_finding(
            "finding-assessment", state="likely_explanation", category="investigator_assessment",
            title="Likely explanation",
            summary=str(assessment.get("likely_mechanism") or assessment.get("summary") or "No validated explanation was retained."),
            evidence_ids=list(assessment.get("evidence_ids") or []),
            next_check=str(assessment.get("next_action") or "Review cited evidence before acting."),
        ))

    priority = ({"scrape_health": 0, "monitoring_selection": 1, "target_discovery": 2,
                 "alert_logic": 3, "investigator_assessment": 4} if any(
                     item.get("category") == "scrape_health" and item.get("state") == "observed"
                     and any(target.get("health") == "down" for target in item.get("observations", []))
                     for item in findings
                 ) else {"monitoring_selection": 0, "target_discovery": 1, "scrape_health": 2,
                         "alert_logic": 3, "investigator_assessment": 4})
    findings.sort(key=lambda item: priority.get(str(item.get("category")), 9))
    return findings[:3]
