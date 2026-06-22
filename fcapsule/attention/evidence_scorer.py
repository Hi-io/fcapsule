"""Transparent cross-domain evidence scoring."""

from __future__ import annotations

from typing import Any

from fcapsule.domains import domain_for_evidence_type
from fcapsule.models.schemas import CaseBundle

ALERT_SEVERITY = {"critical": 1.0, "error": 0.85, "warning": 0.65, "warn": 0.65, "info": 0.25}
SUSPICIOUS_TERMS = ("error", "failed", "timeout", "unavailable", "dropped", "queue", "latency", "exception")


def _entity_score(entities: list[str], expected: set[str]) -> float:
    if not expected:
        return 0.5
    return round(len(set(entities) & expected) / len(expected), 4)


def score_evidence(
    bundle: CaseBundle,
    log_templates: list[dict[str, Any]],
    metric_anomalies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    expected_entities = {
        str(bundle.metadata[key])
        for key in ("service", "cluster", "namespace", "pod", "cncc_uuid")
        if bundle.metadata.get(key)
    }
    evidence: list[dict[str, Any]] = []

    for index, alert in enumerate(bundle.alerts, start=1):
        labels = alert.get("labels", {})
        entities = [str(labels[key]) for key in labels if key in {"service", "cluster", "namespace", "pod", "cncc_uuid"}]
        components = {
            "severity_weight": ALERT_SEVERITY.get(str(alert.get("severity", "info")).lower(), 0.2),
            "anomaly_score": 1.0,
            "temporal_proximity": 1.0,
            "entity_match_score": _entity_score(entities, expected_entities),
            "rarity_score": 0.5,
            "semantic_relevance": 1.0,
            "repetition_penalty": 0.0,
        }
        score = sum(components[key] * weight for key, weight in {
            "severity_weight": 0.25, "anomaly_score": 0.2, "temporal_proximity": 0.15,
            "entity_match_score": 0.15, "rarity_score": 0.05, "semantic_relevance": 0.2,
        }.items())
        evidence.append(
            {
                "evidence_id": f"ev_alert_{index:03d}",
                "type": "alert",
                "domain": domain_for_evidence_type("alert"),
                "source_id": f"alert_{index:03d}",
                "title": str(alert["alertname"]),
                "summary": alert.get("annotations", {}).get("description", "Alert fired"),
                "score": round(score, 4),
                "score_components": components,
                "why_selected": "Alert defines the incident trigger and affected entities.",
                "linked_entities": entities,
                "time_range": {"start": alert["startsAt"], "end": alert.get("endsAt")},
            }
        )

    for template in log_templates:
        semantic = 1.0 if any(term in template["template"].lower() for term in SUSPICIOUS_TERMS) else 0.2
        volume_signal = min(1.0, template["volume_percentage"] / 25)
        repetition_penalty = 0.35 if template["volume_percentage"] > 50 and template["severity_score"] < 0.5 else 0.0
        components = {
            "severity_weight": template["severity_score"],
            "anomaly_score": volume_signal,
            "temporal_proximity": template["temporal_proximity"],
            "entity_match_score": _entity_score(template["linked_entities"], expected_entities),
            "rarity_score": template["rarity_score"],
            "semantic_relevance": semantic,
            "repetition_penalty": repetition_penalty,
        }
        positive = sum(components[key] * weight for key, weight in {
            "severity_weight": 0.25, "anomaly_score": 0.15, "temporal_proximity": 0.2,
            "entity_match_score": 0.15, "rarity_score": 0.1, "semantic_relevance": 0.15,
        }.items())
        score = max(0.0, positive - repetition_penalty)
        evidence.append(
            {
                "evidence_id": f"ev_{template['template_id']}",
                "type": "log_template",
                "domain": domain_for_evidence_type("log_template"),
                "source_id": template["template_id"],
                "title": template["template"],
                "summary": f"{template['count']} matching logs ({template['volume_percentage']:.1f}% of the case).",
                "score": round(score, 4),
                "score_components": components,
                "why_selected": "Log pattern score combines severity, volume, alert proximity, entity match, rarity, and suspicious terms.",
                "linked_entities": template["linked_entities"],
                "time_range": {"start": template["first_seen"], "end": template["last_seen"]},
                "representative_lines": template["representative_lines"],
            }
        )

    for metric in metric_anomalies:
        entities = list(metric["labels"].values())
        name = metric["metric"].lower()
        semantic = 1.0 if any(term in name for term in ("error", "latency", "memory", "cpu", "queue", "log", "request")) else 0.4
        components = {
            "severity_weight": 0.5,
            "anomaly_score": metric["anomaly_score"],
            "temporal_proximity": 1.0,
            "entity_match_score": _entity_score(entities, expected_entities),
            "rarity_score": 0.5,
            "semantic_relevance": semantic,
            "repetition_penalty": 0.0,
        }
        score = sum(components[key] * weight for key, weight in {
            "severity_weight": 0.1, "anomaly_score": 0.4, "temporal_proximity": 0.15,
            "entity_match_score": 0.15, "rarity_score": 0.05, "semantic_relevance": 0.15,
        }.items())
        evidence.append(
            {
                "evidence_id": f"ev_{metric['metric_id']}",
                "type": "metric_anomaly",
                "domain": domain_for_evidence_type("metric_anomaly"),
                "source_id": metric["metric_id"],
                "title": metric["metric"],
                "summary": metric["reason"],
                "score": round(score, 4),
                "score_components": components,
                "why_selected": "Metric score combines anomaly magnitude, relevance, timing, and entity match.",
                "linked_entities": entities,
                "time_range": {"start": metric["peak_timestamp"], "end": metric["peak_timestamp"]},
            }
        )

    return sorted(evidence, key=lambda item: (-item["score"], item["evidence_id"]))
