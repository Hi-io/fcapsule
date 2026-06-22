"""End-to-end P1 orchestration."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fcapsule.attention.evidence_scorer import score_evidence
from fcapsule.attention.evidence_selector import select_evidence
from fcapsule.domains import build_domain_summary
from fcapsule.evaluation.baselines import build_baselines
from fcapsule.evaluation.metrics import calculate_metrics
from fcapsule.io.archive_writer import create_archive
from fcapsule.io.case_loader import load_case
from fcapsule.io.output_writer import write_outputs
from fcapsule.processing.alert_timeline import build_alert_timeline
from fcapsule.processing.entity_resolver import resolve_entities
from fcapsule.processing.log_reducer import reduce_logs
from fcapsule.processing.metrics_analyzer import analyze_metrics
from fcapsule.reasoning.hypothesis_generator import generate_hypotheses
from fcapsule.reasoning.hypothesis_verifier import verify_hypotheses


def investigate_case(case_dir: str | Path, output_dir: str | Path) -> dict[str, Any]:
    started = time.perf_counter()
    bundle = load_case(case_dir)
    entities = resolve_entities(bundle)
    templates = reduce_logs(bundle)
    metrics = analyze_metrics(bundle)
    timeline = build_alert_timeline(bundle)
    candidates = score_evidence(bundle, templates, metrics)
    selected, selection_summary = select_evidence(candidates)
    hypotheses = verify_hypotheses(generate_hypotheses(selected), selected)
    missing_evidence = sorted({item for hypothesis in hypotheses for item in hypothesis.get("missing_evidence", [])})
    next_steps = list(dict.fromkeys(item for hypothesis in hypotheses for item in hypothesis.get("next_checks", [])))
    selected_log_lines = sum(len(item.get("representative_lines", [])) for item in selected if item["type"] == "log_template")

    payload: dict[str, Any] = {
        "schema_version": "p1-1.0",
        "case": bundle.metadata,
        "alerts": bundle.alerts,
        "entity_resolution": entities,
        "timeline": timeline,
        "selected_evidence": selected,
        "selection_summary": selection_summary,
        "log_summary": {"raw_lines": len(bundle.logs), "template_count": len(templates), "selected_lines": selected_log_lines},
        "log_templates": templates,
        "metric_anomalies": metrics,
        "hypotheses": hypotheses,
        "missing_evidence": missing_evidence,
        "next_steps": next_steps,
        "warnings": entities["warnings"],
    }
    payload["domain_summary"] = build_domain_summary(payload, candidates)
    baselines = build_baselines(bundle)
    output = Path(output_dir).resolve()
    write_outputs(output, payload, candidates, baselines)
    evaluation = calculate_metrics(bundle, templates, metrics, selected, hypotheses, payload, time.perf_counter() - started, output)
    write_outputs(output, payload, candidates, baselines, evaluation)
    archive = create_archive(output, bundle.case_id)
    return {
        "case_id": bundle.case_id,
        "service": bundle.metadata["service"],
        "window": bundle.metadata["window"],
        "alerts": len(bundle.alerts),
        "logs": len(bundle.logs),
        "metric_series": len(bundle.metrics),
        "templates": len(templates),
        "selected_evidence": len(selected),
        "hypotheses": len(hypotheses),
        "verified_hypotheses": sum(1 for item in hypotheses if item["verdict"] == "plausible"),
        "evaluation": evaluation,
        "archive": str(archive),
    }
