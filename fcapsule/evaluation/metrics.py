"""Objective capsule metrics with documented, reproducible definitions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fcapsule.config import ANOMALY_THRESHOLD
from fcapsule.models.schemas import CaseBundle


def _estimate_tokens(value: Any) -> int:
    return max(1, len(json.dumps(value, ensure_ascii=True)) // 4)


def _representative_ids(items: list[dict[str, Any]], id_field: str, text_field: str, groups: tuple[tuple[str, ...], ...]) -> set[str]:
    selected: set[str] = set()
    for terms in groups:
        match = next(
            (
                item
                for item in items
                if any(term in str(item.get(text_field, "")).lower() for term in terms)
            ),
            None,
        )
        if match:
            selected.add(str(match[id_field]))
    return selected


def calculate_metrics(
    bundle: CaseBundle,
    log_templates: list[dict[str, Any]],
    metric_anomalies: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    hypotheses: list[dict[str, Any]],
    capsule_payload: dict[str, Any],
    runtime_seconds: float,
    output_dir: Path,
) -> dict[str, Any]:
    raw_log_lines = len(bundle.logs)
    selected_log_lines = sum(
        len(item.get("representative_lines", [])) for item in selected if item["type"] == "log_template"
    )
    selected_source_ids = {item["source_id"] for item in selected}
    important_logs = _representative_ids(
        log_templates,
        "template_id",
        "template",
        (
            ("error", "failed", "unavailable", "aborted"),
            ("retry", "attempt", "breaker"),
            ("pool", "exhaust"),
            ("lock", "deadline", "timeout"),
        ),
    )
    anomalous_metrics = [item for item in metric_anomalies if item["anomaly_score"] >= ANOMALY_THRESHOLD]
    important_metrics = _representative_ids(
        anomalous_metrics,
        "metric_id",
        "metric",
        (
            ("error", "failure"),
            ("latency", "duration"),
            ("retry", "attempt"),
            ("pool", "exhaust", "saturation"),
            ("log_indexing", "scrape", "telemetry"),
        ),
    )
    if not important_logs:
        important_logs = {item["template_id"] for item in log_templates[:4]}
    if not important_metrics:
        important_metrics = {item["metric_id"] for item in anomalous_metrics[:5]}
    important_alerts = {f"alert_{index:03d}" for index in range(1, len(bundle.alerts) + 1)}
    important = important_logs | important_metrics | important_alerts
    preserved = important & selected_source_ids
    signal_preservation = len(preserved) / len(important) if important else 1.0
    anomaly_preservation = len(important_metrics & selected_source_ids) / len(important_metrics) if important_metrics else 1.0
    cited = [evidence_id for hypothesis in hypotheses for evidence_id in hypothesis.get("supporting_evidence", [])]
    selected_ids = {item["evidence_id"] for item in selected}
    grounding = sum(1 for evidence_id in cited if evidence_id in selected_ids) / len(cited) if cited else 1.0

    raw_payload = {"alerts": bundle.alerts, "metrics": bundle.metrics, "logs": bundle.logs, "metadata": bundle.metadata}
    raw_tokens = _estimate_tokens(raw_payload)
    capsule_tokens = _estimate_tokens(capsule_payload)
    source_files = [bundle.case_dir / name for name in ("alert.json", "prometheus_metrics.json", "opensearch_logs.json", "metadata.yaml")]
    raw_bytes = sum(path.stat().st_size for path in source_files)
    capsule_path = output_dir / "capsule.md"
    capsule_bytes = capsule_path.stat().st_size if capsule_path.exists() else len(json.dumps(capsule_payload).encode("utf-8"))
    required_retention_sections = (
        bool(selected), bool(hypotheses), bool(metric_anomalies), bool(log_templates),
        bool(bundle.alerts), bool(capsule_payload.get("entity_resolution")), bool(capsule_payload.get("timeline")),
        bool(capsule_payload.get("missing_evidence")),
    )

    return {
        "raw_log_lines": raw_log_lines,
        "selected_log_lines": selected_log_lines,
        "log_compression_ratio": round(1 - selected_log_lines / raw_log_lines, 4) if raw_log_lines else 0.0,
        "raw_log_bytes": raw_bytes,
        "capsule_bytes": capsule_bytes,
        "template_count": len(log_templates),
        "template_reduction_ratio": round(1 - len(log_templates) / raw_log_lines, 4) if raw_log_lines else 0.0,
        "estimated_raw_tokens": raw_tokens,
        "estimated_capsule_tokens": capsule_tokens,
        "token_reduction_percentage": round(1 - capsule_tokens / raw_tokens, 4) if raw_tokens else 0.0,
        "important_signal_count": len(important),
        "preserved_signal_count": len(preserved),
        "important_signal_ids": sorted(important),
        "preserved_signal_ids": sorted(preserved),
        "missing_signal_ids": sorted(important - preserved),
        "important_signal_preservation": round(signal_preservation, 4),
        "metric_anomaly_preservation": round(anomaly_preservation, 4),
        "hypothesis_grounding_score": round(grounding, 4),
        "runtime_seconds": round(runtime_seconds, 4),
        "retention_survivability_score": round(sum(required_retention_sections) / len(required_retention_sections), 4),
        "definitions": {
            "log_compression_ratio": "1 - representative selected log lines / raw log lines",
            "template_reduction_ratio": "1 - grouped templates / raw log lines",
            "token_reduction_percentage": "1 - estimated capsule tokens / estimated raw telemetry tokens",
            "important_signal_preservation": "selected representative fault, failure, retry, saturation, latency, and telemetry-health signals / identified signal groups",
            "hypothesis_grounding_score": "valid cited evidence IDs / all cited evidence IDs",
            "retention_survivability_score": "present required capsule sections / eight required sections",
        },
    }
