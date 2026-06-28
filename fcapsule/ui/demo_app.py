"""Optional local demo UI for FCAPSule P1."""

from __future__ import annotations

import html
import json
import os
import sys
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from fcapsule.env import load_env_file
from fcapsule.io.case_loader import load_case
from fcapsule.pipeline import investigate_case
from fcapsule.reasoning.llm_client import LLMUnavailableError
from fcapsule.reasoning.model_comparator import DEFAULT_MODELS, compare_models
from fcapsule.ui.dashboard import render_dashboard


PHASES = {
    "capture": {
        "title": "1. Generate failing app and alert",
        "waiting": "Waiting to start the local checkout failure.",
    },
    "pipeline": {
        "title": "2. Build FCAPSule evidence capsule",
        "waiting": "Waiting for the captured telemetry case.",
    },
    "deepseek-v4-flash": {
        "title": "3A. DeepSeek v4 Flash",
        "waiting": "Waiting for the same capsule input.",
    },
    "deepseek-v4-pro": {
        "title": "3B. DeepSeek v4 Pro",
        "waiting": "Waiting for the same capsule input.",
    },
}


class DemoRunState:
    """In-memory live state for the optional local demo UI."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.running = False
            self.active_job = None
            self.last_error = None
            self.started_at = None
            self.finished_at = None
            self.phases = {
                key: {"status": "waiting", "message": value["waiting"], "details": {}, "updated_at": None}
                for key, value in PHASES.items()
            }
            self.events: list[dict[str, Any]] = []
            self.capture_result: dict[str, Any] | None = None
            self.pipeline_result: dict[str, Any] | None = None
            self.comparison_result: dict[str, Any] | None = None

    def begin(self, job: str) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.active_job = job
            self.last_error = None
            self.started_at = time.time()
            self.finished_at = None
            return True

    def finish(self, error: str | None = None) -> None:
        with self.lock:
            self.running = False
            self.finished_at = time.time()
            if error:
                self.last_error = error
                self.events.append({"time": time.time(), "phase": "system", "status": "error", "message": error, "details": {}})

    def clear_after_capture(self) -> None:
        with self.lock:
            for key in ("pipeline", "deepseek-v4-flash", "deepseek-v4-pro"):
                self.phases[key] = {"status": "waiting", "message": PHASES[key]["waiting"], "details": {}, "updated_at": None}
            self.pipeline_result = None
            self.comparison_result = None

    def clear_analysis(self) -> None:
        with self.lock:
            for key in ("pipeline", "deepseek-v4-flash", "deepseek-v4-pro"):
                self.phases[key] = {"status": "waiting", "message": PHASES[key]["waiting"], "details": {}, "updated_at": None}
            self.pipeline_result = None
            self.comparison_result = None

    def emit(self, phase: str, status: str, message: str, details: dict[str, Any] | None = None) -> None:
        details = details or {}
        with self.lock:
            if phase not in self.phases:
                self.phases[phase] = {"status": "waiting", "message": "", "details": {}, "updated_at": None}
            self.phases[phase] = {"status": status, "message": message, "details": details, "updated_at": time.time()}
            self.events.append({"time": time.time(), "phase": phase, "status": status, "message": message, "details": details})
            self.events = self.events[-80:]

    def set_capture_result(self, result: dict[str, Any]) -> None:
        with self.lock:
            self.capture_result = result

    def set_pipeline_result(self, result: dict[str, Any]) -> None:
        with self.lock:
            self.pipeline_result = result

    def set_comparison_result(self, result: dict[str, Any]) -> None:
        with self.lock:
            self.comparison_result = result

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            elapsed = None
            if self.started_at:
                end = self.finished_at or time.time()
                elapsed = round(end - self.started_at, 2)
            return {
                "running": self.running,
                "active_job": self.active_job,
                "last_error": self.last_error,
                "elapsed_seconds": elapsed,
                "phases": json.loads(json.dumps(self.phases)),
                "events": json.loads(json.dumps(self.events)),
                "capture_result": json.loads(json.dumps(self.capture_result)) if self.capture_result else None,
                "pipeline_result": json.loads(json.dumps(self.pipeline_result)) if self.pipeline_result else None,
                "comparison_result": json.loads(json.dumps(self.comparison_result)) if self.comparison_result else None,
                "api_key_available": bool(os.environ.get("DEEPSEEK_API_KEY")),
            }


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "pending"


def _case_counts(case_dir: Path) -> dict[str, Any]:
    if not case_dir.exists():
        return {"case_available": False}
    try:
        bundle = load_case(case_dir)
    except Exception as exc:  # pragma: no cover - defensive UI path
        return {"case_available": False, "error": str(exc)}
    return {
        "case_available": True,
        "case_id": bundle.case_id,
        "alerts": len(bundle.alerts),
        "logs": len(bundle.logs),
        "metric_series": len(bundle.metrics),
        "service": bundle.metadata.get("service"),
        "window": bundle.metadata.get("window", {}),
    }


def build_demo_summary(case_dir: str | Path, output_dir: str | Path) -> dict[str, Any]:
    """Summarize the current demo state from case and output files."""

    env_path = load_env_file()
    case = Path(case_dir).resolve()
    output = Path(output_dir).resolve()
    capsule = _load_json(output / "capsule.json")
    evaluation = _load_json(output / "evaluation.json")
    comparison = _load_json(output / "llm_comparison.json")
    evidence = _load_json(output / "evidence.json")
    case_counts = _case_counts(case)
    selected = capsule.get("selected_evidence", [])
    domain_summary = capsule.get("domain_summary", {})
    model_results = []
    for item in comparison.get("results", []):
        score = item.get("score", {})
        model_results.append(
            {
                "model": item.get("model"),
                "total_score": score.get("total_score"),
                "domain_score": score.get("domain_score"),
                "expected_signal_score": score.get("expected_signal_score"),
                "citation_score": score.get("citation_score"),
                "latency_seconds": item.get("latency_seconds"),
                "total_tokens": item.get("usage", {}).get("total_tokens"),
                "finish_reason": item.get("finish_reason"),
            }
        )
    return {
        "case_dir": str(case),
        "output_dir": str(output),
        "case": case_counts,
        "outputs": {
            "capsule": (output / "capsule.md").exists(),
            "dashboard": (output / "dashboard.html").exists(),
            "llm_comparison": (output / "llm_comparison.json").exists(),
            "archive": str(output / f"fcapsule_{case_counts.get('case_id', 'case_001')}.zip"),
        },
        "evaluation": {
            "log_compression_ratio": evaluation.get("log_compression_ratio"),
            "token_reduction_percentage": evaluation.get("token_reduction_percentage"),
            "important_signal_preservation": evaluation.get("important_signal_preservation"),
            "hypothesis_grounding_score": evaluation.get("hypothesis_grounding_score"),
            "retention_survivability_score": evaluation.get("retention_survivability_score"),
            "raw_log_lines": evaluation.get("raw_log_lines"),
            "selected_log_lines": evaluation.get("selected_log_lines"),
            "template_count": evaluation.get("template_count"),
        },
        "pipeline": {
            "selected_evidence": len(selected),
            "candidate_evidence": len(evidence) if isinstance(evidence, list) else 0,
            "hypotheses": len(capsule.get("hypotheses", [])),
            "verified_hypotheses": sum(1 for item in capsule.get("hypotheses", []) if item.get("verdict") == "plausible"),
            "log_templates": len(capsule.get("log_templates", [])),
            "metric_anomalies": len(capsule.get("metric_anomalies", [])),
        },
        "domains": domain_summary,
        "models": {
            "winner": comparison.get("winner"),
            "score_delta": comparison.get("score_delta"),
            "interpretation": comparison.get("interpretation"),
            "results": model_results,
        },
        "api_key_available": bool(os.environ.get("DEEPSEEK_API_KEY")),
        "env_loaded_from": str(env_path) if env_path else None,
    }


def render_printout(summary: dict[str, Any]) -> str:
    """Create the user-facing run log shown in the demo UI."""

    case = summary["case"]
    evaluation = summary["evaluation"]
    pipeline = summary["pipeline"]
    models = summary["models"]
    lines = [
        "FCAPSule AI P1 demo summary",
        f"Case directory: {summary['case_dir']}",
        f"Output directory: {summary['output_dir']}",
        "",
        "1. Input telemetry",
        f"- Case available: {case.get('case_available')}",
        f"- Case ID: {case.get('case_id', 'pending')}",
        f"- Service: {case.get('service', 'pending')}",
        f"- Alerts: {case.get('alerts', 'pending')}",
        f"- Raw logs: {case.get('logs', 'pending')}",
        f"- Metric series: {case.get('metric_series', 'pending')}",
        "",
        "2. P1 processing",
        f"- Log templates generated: {pipeline.get('log_templates')}",
        f"- Metric anomalies analyzed: {pipeline.get('metric_anomalies')}",
        f"- Candidate evidence items: {pipeline.get('candidate_evidence')}",
        f"- Selected evidence items: {pipeline.get('selected_evidence')}",
        f"- Hypotheses generated: {pipeline.get('hypotheses')}",
        f"- Plausible verified hypotheses: {pipeline.get('verified_hypotheses')}",
        "",
        "3. Reductions and preservation",
        f"- Log compression: {_pct(evaluation.get('log_compression_ratio'))}",
        f"- Token reduction: {_pct(evaluation.get('token_reduction_percentage'))}",
        f"- Signal preservation: {_pct(evaluation.get('important_signal_preservation'))}",
        f"- Grounded citations: {_pct(evaluation.get('hypothesis_grounding_score'))}",
        f"- Retention survivability: {_pct(evaluation.get('retention_survivability_score'))}",
        "",
        "4. LLM comparison",
        f"- Winner: {models.get('winner', 'pending')}",
        f"- Score delta: {models.get('score_delta', 'pending')}",
    ]
    for item in models.get("results", []):
        lines.append(
            "- {model}: total={total}, domains={domains}, signals={signals}, citations={citations}, "
            "latency={latency}s, tokens={tokens}".format(
                model=item.get("model"),
                total=item.get("total_score"),
                domains=item.get("domain_score"),
                signals=item.get("expected_signal_score"),
                citations=item.get("citation_score"),
                latency=item.get("latency_seconds"),
                tokens=item.get("total_tokens"),
            )
        )
    if models.get("interpretation"):
        lines.extend(["", f"Observed interpretation: {models['interpretation']}"])
    return "\n".join(lines)


def _domain_cards(summary: dict[str, Any]) -> str:
    cards = []
    explanations = {
        "fault_events": "The alert that tells us an incident happened.",
        "log_text": "Repeated application messages compressed into templates.",
        "time_series_metrics": "Numeric behavior before and during the incident.",
        "topology_metadata": "Service, pod, namespace, cluster, and CNCC labels that connect evidence.",
        "llm_reasoning": "Model-written interpretation grounded in selected evidence.",
    }
    for domain_id, domain in summary.get("domains", {}).items():
        cards.append(
            "<section class='card domain'>"
            f"<span>{html.escape(domain_id)}</span>"
            f"<h3>{html.escape(str(domain.get('label', domain_id)))}</h3>"
            f"<p>{html.escape(explanations.get(domain_id, str(domain.get('signal_family', ''))))}</p>"
            "<dl>"
            f"<div><dt>Selected</dt><dd>{domain.get('selected_evidence_items', 0)}</dd></div>"
            f"<div><dt>Candidates</dt><dd>{domain.get('candidate_evidence_items', 0)}</dd></div>"
            f"<div><dt>Raw</dt><dd>{domain.get('raw_items', 0)}</dd></div>"
            "</dl>"
            "</section>"
        )
    return "\n".join(cards) or "<p class='muted'>Run P1 to build the domain map.</p>"


def _score_bar(value: Any, label: str) -> str:
    try:
        pct = max(0.0, min(1.0, float(value))) * 100
        shown = f"{float(value):.3f}"
    except (TypeError, ValueError):
        pct = 0.0
        shown = "pending"
    return (
        f"<div class='score-row'><span>{html.escape(label)}</span>"
        f"<div class='score-track'><i style='width:{pct:.1f}%'></i></div><strong>{shown}</strong></div>"
    )


def _model_cards(summary: dict[str, Any]) -> str:
    cards = []
    winner = summary["models"].get("winner")
    for item in summary["models"].get("results", []):
        is_winner = item.get("model") == winner
        cards.append(
            "<section class='model-card card {klass}'>"
            "<div class='model-heading'>"
            f"<h3>{html.escape(str(item.get('model')))}</h3>"
            f"<span>{'Best observed' if is_winner else 'Compared'}</span>"
            "</div>"
            f"{_score_bar(item.get('total_score'), 'Overall quality')}"
            f"{_score_bar(item.get('expected_signal_score'), 'Incident signal coverage')}"
            f"{_score_bar(item.get('citation_score'), 'Valid evidence citations')}"
            f"{_score_bar(item.get('domain_score'), 'Telemetry domain coverage')}"
            "<dl>"
            f"<div><dt>Latency</dt><dd>{item.get('latency_seconds')}s</dd></div>"
            f"<div><dt>Tokens</dt><dd>{item.get('total_tokens')}</dd></div>"
            f"<div><dt>Finish</dt><dd>{html.escape(str(item.get('finish_reason')))}</dd></div>"
            "</dl>"
            "</section>".format(klass="winner-card" if is_winner else "")
        )
    return "\n".join(cards) or "<p class='muted'>Run DeepSeek comparison to populate model cards.</p>"


def _workflow_steps(summary: dict[str, Any]) -> str:
    case = summary["case"]
    pipeline = summary["pipeline"]
    models = summary["models"]
    steps = [
        ("1", "Capture incident", f"{case.get('logs', 'pending')} logs, {case.get('metric_series', 'pending')} metric series, {case.get('alerts', 'pending')} alert."),
        ("2", "Reduce telemetry", f"{pipeline.get('log_templates')} log templates and {pipeline.get('selected_evidence')} selected evidence items."),
        ("3", "Ground hypotheses", f"{pipeline.get('verified_hypotheses')} plausible hypotheses with checked evidence IDs."),
        ("4", "Compare models", f"Winner: {models.get('winner', 'pending')}; score delta: {models.get('score_delta', 'pending')}."),
    ]
    return "\n".join(
        "<section class='step card'>"
        f"<b>{number}</b><div><h3>{html.escape(title)}</h3><p>{html.escape(text)}</p></div>"
        "</section>"
        for number, title, text in steps
    )


def _comparison_takeaway(summary: dict[str, Any]) -> str:
    models = summary["models"]
    winner = models.get("winner")
    if not winner:
        return "Run the DeepSeek comparison to see whether the smarter model produces a better grounded incident note."
    delta = models.get("score_delta")
    return (
        f"{winner} performed best on the same capsule input. The observed score delta is {delta}, "
        "mainly measuring whether the model used the expected incident signals while keeping valid evidence citations."
    )


def _plain_result(summary: dict[str, Any]) -> str:
    case = summary["case"]
    evaluation = summary["evaluation"]
    pipeline = summary["pipeline"]
    return (
        f"FCAPSule converted {case.get('logs', 'pending')} raw logs into "
        f"{pipeline.get('log_templates')} templates and selected {pipeline.get('selected_evidence')} evidence items. "
        f"The capsule preserved {_pct(evaluation.get('important_signal_preservation'))} of important signals "
        f"with {_pct(evaluation.get('log_compression_ratio'))} log compression."
    )


def _pipeline_ui_result(output_dir: Path, result: dict[str, Any]) -> dict[str, Any]:
    capsule = _load_json(output_dir / "capsule.json")
    enriched = dict(result)
    enriched["domain_summary"] = capsule.get("domain_summary", {})
    return enriched


def _html_page_live() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FCAPSule AI P1 Live Demo</title>
  <style>
    :root { --ink:#16202a; --muted:#637181; --line:#d8e0e8; --paper:#fff; --bg:#edf2f7; --accent:#2f7d62; --accent-soft:#e6f3ee; --warn:#a4385a; --blue:#356a96; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:Arial, Helvetica, sans-serif; color:var(--ink); background:var(--bg); }
    header { background:#fff; border-bottom:1px solid var(--line); padding:26px 30px 18px; }
    main { width:min(1240px, calc(100% - 28px)); margin:22px auto 44px; display:grid; gap:18px; }
    h1 { margin:0 0 8px; font-size:30px; letter-spacing:0; }
    h2 { margin:0 0 12px; font-size:20px; letter-spacing:0; }
    h3 { margin:0 0 8px; font-size:16px; letter-spacing:0; }
    p { line-height:1.45; }
    button, a.button { appearance:none; border:1px solid #1f604a; background:var(--accent); color:#fff; border-radius:6px; padding:10px 12px; font-weight:700; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; justify-content:center; min-height:38px; }
    button.secondary, a.secondary { background:#fff; color:var(--ink); border-color:var(--line); }
    button:disabled { opacity:.58; cursor:wait; }
    .muted { color:var(--muted); }
    .hero { display:grid; grid-template-columns:minmax(0, 1.25fr) minmax(300px, .9fr); gap:16px; align-items:stretch; }
    .hero-card, .panel, .card { background:var(--paper); border:1px solid var(--line); border-radius:8px; padding:16px; }
    .hero-card.primary { border-left:6px solid var(--accent); }
    .takeaway { font-size:18px; line-height:1.45; margin:0; }
    .actions { display:flex; flex-wrap:wrap; gap:10px; }
    .status { min-height:22px; color:var(--muted); }
    .flow { display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:12px; }
    .phase-card { min-height:168px; }
    .phase-card.active { border-color:var(--blue); box-shadow:0 0 0 2px rgba(53,106,150,.12); }
    .phase-card.done { border-color:var(--accent); box-shadow:0 0 0 2px rgba(47,125,98,.12); }
    .phase-card.error { border-color:var(--warn); }
    .phase-head { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; }
    .badge { border-radius:999px; border:1px solid var(--line); background:#f7f9fb; color:var(--muted); padding:3px 8px; font-size:12px; text-transform:uppercase; }
    .active .badge { background:#e7f0f8; color:#204f73; border-color:#b9d0e2; }
    .done .badge { background:var(--accent-soft); color:#15583f; border-color:#b7dccd; }
    .error .badge { background:#f8e8ee; color:#832a45; border-color:#e5b8c8; }
    .phase-card p { margin:8px 0 10px; color:var(--muted); }
    .facts, .metrics { display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:12px; }
    .fact, .metric { background:#fff; border:1px solid var(--line); border-radius:8px; padding:14px; }
    .fact span, .metric span { display:block; color:var(--muted); font-size:12px; }
    .fact strong, .metric strong { display:block; margin-top:5px; font-size:24px; }
    .metric small { color:var(--muted); display:block; margin-top:6px; line-height:1.35; }
    .model-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:12px; }
    .model-card.done { border-color:var(--accent); }
    .model-card.winner { box-shadow:0 0 0 2px rgba(47,125,98,.14); }
    .model-title { display:flex; justify-content:space-between; gap:8px; align-items:flex-start; }
    .score-row { display:grid; grid-template-columns:132px minmax(80px, 1fr) 58px; align-items:center; gap:8px; margin:9px 0; }
    .score-row span { color:var(--muted); font-size:13px; }
    .score-row strong { text-align:right; font-size:13px; }
    .score-track { height:10px; background:#e5ebf1; border-radius:999px; overflow:hidden; }
    .score-track i { display:block; height:100%; width:0%; background:var(--accent); transition:width .25s ease; }
    .domain-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(230px, 1fr)); gap:12px; }
    .domain span { display:inline-block; color:#fff; background:#56616d; border-radius:4px; padding:2px 6px; font-size:12px; margin-bottom:10px; }
    dl { display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; margin:12px 0 0; }
    dt { color:var(--muted); font-size:12px; }
    dd { margin:2px 0 0; font-weight:700; }
    .events { display:grid; gap:8px; max-height:320px; overflow:auto; padding-right:4px; }
    .event { display:grid; grid-template-columns:88px 120px minmax(0, 1fr); gap:10px; align-items:start; border-bottom:1px solid var(--line); padding:8px 0; }
    .event code { color:var(--muted); font-size:12px; }
    .event b { font-size:13px; }
    @media (max-width:980px) { header { padding:20px 16px; } .hero, .model-grid { grid-template-columns:1fr; } .flow, .facts, .metrics { grid-template-columns:1fr 1fr; } }
    @media (max-width:640px) { .flow, .facts, .metrics { grid-template-columns:1fr; } .score-row, .event { grid-template-columns:1fr; } .score-row strong { text-align:left; } dl { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>FCAPSule AI P1 Live Demo</h1>
    <p class="muted">Start from an empty UI, create a failing checkout service scenario, capture raw telemetry, build the evidence capsule, and compare DeepSeek Flash vs Pro.</p>
  </header>
  <main>
    <section class="hero">
      <div class="hero-card primary">
        <h2>What is happening?</h2>
        <p id="main-takeaway" class="takeaway">Nothing has run in this UI session yet. Start by generating a local checkout failure; then run FCAPSule to reduce the raw telemetry and compare the two DeepSeek outputs.</p>
        <p class="muted">FCAPSule does not claim a final root cause. It preserves a compact, evidence-grounded incident capsule so humans or models can reason over less noise.</p>
      </div>
      <div class="hero-card">
        <h2>Demo controls</h2>
        <p class="muted">Use the buttons in order. The cards below fill while the process runs.</p>
        <div class="actions">
          <button id="capture-button" data-action="start-capture">1. Generate failing app + alert</button>
          <button id="analysis-button" data-action="start-analysis" disabled>2. Run FCAPSule + DeepSeek</button>
          <button class="secondary" id="reset-button" data-action="reset">Reset UI</button>
        </div>
        <p id="status" class="status">Ready.</p>
      </div>
    </section>
    <section class="flow" id="phase-flow"></section>
    <section class="panel">
      <h2>Raw Incident Data</h2>
      <p class="muted">This fills after the first button starts the demo app, enables the failure, triggers the alert, and writes the case files.</p>
      <div class="facts" id="raw-facts"></div>
    </section>
    <section class="metrics">
      <div class="metric"><span>Log compression</span><strong id="metric-compression">pending</strong><small>Raw log volume removed while keeping representatives.</small></div>
      <div class="metric"><span>Signal preserved</span><strong id="metric-signal">pending</strong><small>Important alerts, logs, and metrics kept in the capsule.</small></div>
      <div class="metric"><span>Grounded claims</span><strong id="metric-grounding">pending</strong><small>Hypothesis citations that point to real evidence IDs.</small></div>
      <div class="metric"><span>Model winner</span><strong id="metric-winner">pending</strong><small>Best result using the same capsule and prompt.</small></div>
    </section>
    <section class="panel">
      <h2>DeepSeek Same-Input Comparison</h2>
      <p id="comparison-text" class="muted">Both model cards start empty. When step 2 reaches the model phase, Flash and Pro are filled with score, signal coverage, valid citations, latency, and token usage.</p>
      <div class="model-grid" id="model-grid"></div>
    </section>
    <section class="panel">
      <h2>Telemetry Domains</h2>
      <p class="muted">These are observability signal families, not media types. FCAPSule aligns them into one evidence view.</p>
      <div class="domain-grid" id="domain-grid"></div>
    </section>
    <section class="panel">
      <h2>Live Progress Log</h2>
      <div class="events" id="events"></div>
    </section>
    <section class="panel">
      <h2>Detailed Artifacts</h2>
      <div class="actions">
        <a class="button secondary" href="/dashboard.html" target="_blank">Open generated dashboard</a>
        <a class="button secondary" href="/outputs/capsule.md" target="_blank">Open Markdown capsule</a>
        <a class="button secondary" href="/api/summary" target="_blank">Open JSON summary</a>
      </div>
    </section>
  </main>
  <script>
    const phaseDefinitions = {
      capture: 'Generate failing app and alert',
      pipeline: 'Build FCAPSule evidence capsule',
      'deepseek-v4-flash': 'DeepSeek v4 Flash',
      'deepseek-v4-pro': 'DeepSeek v4 Pro'
    };
    const statusEl = document.querySelector('#status');
    const captureButton = document.querySelector('#capture-button');
    const analysisButton = document.querySelector('#analysis-button');
    const resetButton = document.querySelector('#reset-button');
    const pct = (value) => typeof value === 'number' ? (value * 100).toFixed(1) + '%' : 'pending';
    const show = (value, fallback='pending') => value === null || value === undefined ? fallback : String(value);
    const scoreBar = (label, value) => {
      const number = Number(value);
      const safe = Number.isFinite(number) ? Math.max(0, Math.min(1, number)) : 0;
      const shown = Number.isFinite(number) ? number.toFixed(3) : 'pending';
      return `<div class="score-row"><span>${label}</span><div class="score-track"><i style="width:${safe * 100}%"></i></div><strong>${shown}</strong></div>`;
    };
    function phaseClass(status) {
      if (status === 'done') return 'done';
      if (status === 'running') return 'active';
      if (status === 'error') return 'error';
      return '';
    }
    function renderPhaseCards(state) {
      document.querySelector('#phase-flow').innerHTML = Object.entries(phaseDefinitions).map(([key, title]) => {
        const phase = state.phases[key] || { status: 'waiting', message: 'Waiting', details: {} };
        const details = phase.details || {};
        const detailText = Object.entries(details).slice(0, 3).map(([k, v]) => `${k}: ${v}`).join(' · ');
        return `<section class="phase-card card ${phaseClass(phase.status)}"><div class="phase-head"><h3>${title}</h3><span class="badge">${phase.status}</span></div><p>${phase.message}</p><small class="muted">${detailText || 'No data yet.'}</small></section>`;
      }).join('');
    }
    function renderRawFacts(state) {
      const result = state.capture_result;
      const facts = result ? [
        ['Healthy requests', result.healthy_requests], ['Failing requests', result.failing_requests], ['Captured logs', result.captured_logs],
        ['Metric series', result.metric_series], ['Alert', result.alert], ['Alert status', result.alert_status], ['Final error rate', result.final_error_rate], ['Service port', result.service_port]
      ] : [
        ['Healthy requests', 'pending'], ['Failing requests', 'pending'], ['Captured logs', 'pending'], ['Metric series', 'pending'],
        ['Alert', 'pending'], ['Alert status', 'pending'], ['Final error rate', 'pending'], ['Service port', 'pending']
      ];
      document.querySelector('#raw-facts').innerHTML = facts.map(([label, value]) => `<div class="fact"><span>${label}</span><strong>${show(value)}</strong></div>`).join('');
    }
    function renderMetrics(state) {
      const evaluation = state.pipeline_result?.evaluation || {};
      document.querySelector('#metric-compression').textContent = pct(evaluation.log_compression_ratio);
      document.querySelector('#metric-signal').textContent = pct(evaluation.important_signal_preservation);
      document.querySelector('#metric-grounding').textContent = pct(evaluation.hypothesis_grounding_score);
      document.querySelector('#metric-winner').textContent = state.comparison_result?.winner || 'pending';
    }
    function renderModels(state) {
      const comparison = state.comparison_result;
      const results = comparison?.results || [];
      const byModel = Object.fromEntries(results.map((item) => [item.model, item]));
      document.querySelector('#model-grid').innerHTML = ['deepseek-v4-flash', 'deepseek-v4-pro'].map((model) => {
        const item = byModel[model];
        const phase = state.phases[model] || { status: 'waiting', message: 'Waiting' };
        const score = item?.score || {};
        const isWinner = comparison?.winner === model;
        return `<section class="model-card card ${phaseClass(phase.status)} ${isWinner ? 'winner' : ''}"><div class="model-title"><h3>${model}</h3><span class="badge">${isWinner ? 'winner' : phase.status}</span></div><p class="muted">${phase.message}</p>${scoreBar('Overall quality', score.total_score)}${scoreBar('Signal coverage', score.expected_signal_score)}${scoreBar('Citation validity', score.citation_score)}${scoreBar('Domain coverage', score.domain_score)}<dl><div><dt>Latency</dt><dd>${show(item?.latency_seconds)}s</dd></div><div><dt>Tokens</dt><dd>${show(item?.usage?.total_tokens)}</dd></div><div><dt>Finish</dt><dd>${show(item?.finish_reason)}</dd></div></dl></section>`;
      }).join('');
      document.querySelector('#comparison-text').textContent = comparison
        ? `${comparison.winner} performed best on the same capsule input. Score delta: ${comparison.score_delta}.`
        : 'Both model cards start empty. When step 2 reaches the model phase, Flash and Pro are filled with score, signal coverage, valid citations, latency, and token usage.';
    }
    function renderDomains(state) {
      const domains = state.pipeline_result?.domain_summary || {};
      const explanations = {
        fault_events: 'The alert/event stream that confirms an incident happened.',
        log_text: 'Repeated application log messages compressed into templates.',
        time_series_metrics: 'Numeric behavior before and during the incident.',
        topology_metadata: 'Labels that connect service, pod, namespace, cluster, and CNCC identity.',
        llm_reasoning: 'Model-written interpretation grounded in selected evidence.'
      };
      const entries = Object.entries(domains);
      document.querySelector('#domain-grid').innerHTML = entries.length ? entries.map(([key, domain]) => `<section class="card domain"><span>${key}</span><h3>${domain.label}</h3><p>${explanations[key] || domain.signal_family}</p><dl><div><dt>Selected</dt><dd>${domain.selected_evidence_items}</dd></div><div><dt>Candidates</dt><dd>${domain.candidate_evidence_items}</dd></div><div><dt>Raw</dt><dd>${domain.raw_items}</dd></div></dl></section>`).join('') : '<p class="muted">Run FCAPSule to populate the telemetry domain map.</p>';
    }
    function renderEvents(state) {
      const events = state.events || [];
      document.querySelector('#events').innerHTML = events.length ? events.slice().reverse().map((event) => {
        const time = new Date(event.time * 1000).toLocaleTimeString();
        return `<div class="event"><code>${time}</code><b>${event.phase}</b><span>${event.message}</span></div>`;
      }).join('') : '<p class="muted">No live events yet. Start with button 1.</p>';
    }
    function renderTakeaway(state) {
      const capture = state.capture_result;
      const pipeline = state.pipeline_result;
      const comparison = state.comparison_result;
      let text = 'Nothing has run in this UI session yet. Start by generating a local checkout failure; then run FCAPSule to reduce the raw telemetry and compare the two DeepSeek outputs.';
      if (capture && !pipeline) text = `The demo app failed successfully: ${capture.captured_logs} logs and ${capture.metric_series} metric series were captured, and ${capture.alert} is ${capture.alert_status}. Now run FCAPSule.`;
      if (pipeline && !comparison) text = `FCAPSule reduced ${pipeline.logs} logs into ${pipeline.templates} templates and selected ${pipeline.selected_evidence} evidence items. DeepSeek comparison is running or ready to start.`;
      if (pipeline && comparison) text = `Complete: FCAPSule preserved ${(pipeline.evaluation.important_signal_preservation * 100).toFixed(1)}% of important signal with ${(pipeline.evaluation.log_compression_ratio * 100).toFixed(1)}% log compression. ${comparison.winner} won the same-input model comparison.`;
      document.querySelector('#main-takeaway').textContent = text;
    }
    function renderState(state) {
      statusEl.textContent = state.running ? `Running ${state.active_job}... elapsed ${state.elapsed_seconds || 0}s` : `Ready. DeepSeek key loaded: ${state.api_key_available}. ${state.last_error ? 'Last error: ' + state.last_error : ''}`;
      captureButton.disabled = state.running;
      analysisButton.disabled = state.running || !state.capture_result;
      resetButton.disabled = state.running;
      renderPhaseCards(state); renderRawFacts(state); renderMetrics(state); renderModels(state); renderDomains(state); renderEvents(state); renderTakeaway(state);
    }
    async function refreshState() {
      const response = await fetch('/api/state');
      renderState(await response.json());
    }
    async function postAction(action) {
      statusEl.textContent = 'Starting ' + action + '...';
      try {
        const response = await fetch('/api/' + action, { method: 'POST' });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.error || 'Action failed');
        await refreshState();
      } catch (error) {
        statusEl.textContent = 'Error: ' + error.message;
      }
    }
    document.querySelectorAll('button[data-action]').forEach((button) => button.addEventListener('click', () => postAction(button.dataset.action)));
    refreshState();
    setInterval(refreshState, 800);
  </script>
</body>
</html>
"""


def _html_page(summary: dict[str, Any], printout: str) -> str:
    evaluation = summary["evaluation"]
    models = summary["models"]
    case = summary["case"]
    pipeline = summary["pipeline"]
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FCAPSule AI P1 Demo</title>
  <style>
    :root {{ --ink:#16202a; --muted:#637181; --line:#d8e0e8; --paper:#fff; --bg:#edf2f7; --accent:#2f7d62; --accent-soft:#e6f3ee; --warn:#a4385a; --amber:#8a6a20; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Arial, Helvetica, sans-serif; color:var(--ink); background:var(--bg); }}
    header {{ background:#ffffff; border-bottom:1px solid var(--line); padding:26px 30px 18px; }}
    main {{ width:min(1240px, calc(100% - 28px)); margin:22px auto 44px; display:grid; gap:18px; }}
    h1 {{ margin:0 0 8px; font-size:30px; letter-spacing:0; }}
    h2 {{ margin:0 0 12px; font-size:20px; letter-spacing:0; }}
    h3 {{ margin:0 0 8px; font-size:16px; letter-spacing:0; }}
    p {{ line-height:1.45; }}
    button, a.button {{ appearance:none; border:1px solid #1f604a; background:var(--accent); color:white; border-radius:6px; padding:10px 12px; font-weight:700; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; justify-content:center; min-height:38px; }}
    button.secondary, a.secondary {{ background:#ffffff; color:var(--ink); border-color:var(--line); }}
    button:disabled {{ opacity:.6; cursor:wait; }}
    .muted {{ color:var(--muted); }}
    .hero {{ display:grid; grid-template-columns:minmax(0, 1.45fr) minmax(300px, .75fr); gap:16px; align-items:stretch; }}
    .hero-card {{ background:#ffffff; border:1px solid var(--line); border-radius:8px; padding:18px; }}
    .hero-card.primary {{ border-left:6px solid var(--accent); }}
    .takeaway {{ font-size:18px; line-height:1.45; margin:0; }}
    .mini-note {{ background:#f7f9fb; border:1px solid var(--line); border-radius:6px; padding:10px; margin-top:12px; }}
    .panel, .card {{ background:var(--paper); border:1px solid var(--line); border-radius:8px; padding:16px; }}
    .actions {{ display:flex; flex-wrap:wrap; gap:10px; }}
    .metrics {{ display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:12px; }}
    .metric strong {{ display:block; font-size:25px; margin-top:5px; }}
    .metric small {{ color:var(--muted); display:block; margin-top:6px; line-height:1.35; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(230px, 1fr)); gap:12px; }}
    .steps {{ display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:12px; }}
    .step {{ display:grid; grid-template-columns:38px minmax(0, 1fr); gap:10px; align-items:start; }}
    .step b {{ display:grid; place-items:center; width:34px; height:34px; border-radius:50%; background:var(--accent-soft); color:#15583f; }}
    .step p {{ margin:0; color:var(--muted); }}
    .domain span {{ display:inline-block; color:#ffffff; background:#56616d; border-radius:4px; padding:2px 6px; font-size:12px; margin-bottom:10px; }}
    dl {{ display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; margin:12px 0 0; }}
    dt {{ color:var(--muted); font-size:12px; }}
    dd {{ margin:2px 0 0; font-weight:700; }}
    table {{ width:100%; border-collapse:collapse; background:#fff; }}
    th, td {{ border-bottom:1px solid var(--line); padding:10px; text-align:left; vertical-align:top; }}
    th {{ color:var(--muted); font-size:12px; }}
    pre {{ white-space:pre-wrap; overflow:auto; background:#101820; color:#eff6f4; border-radius:8px; padding:14px; margin:0; line-height:1.45; }}
    .winner {{ color:#0f6b4d; font-weight:700; }}
    .comparison-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(310px, 1fr)); gap:12px; }}
    .model-heading {{ display:flex; align-items:center; justify-content:space-between; gap:8px; }}
    .model-heading span {{ border:1px solid var(--line); background:#f7f9fb; color:var(--muted); border-radius:999px; padding:3px 8px; font-size:12px; }}
    .winner-card {{ border-color:#2f7d62; box-shadow:0 0 0 2px rgba(47,125,98,.13); }}
    .winner-card .model-heading span {{ background:var(--accent-soft); border-color:#b7dccd; color:#15583f; }}
    .score-row {{ display:grid; grid-template-columns:130px minmax(80px, 1fr) 58px; align-items:center; gap:8px; margin:9px 0; }}
    .score-row span {{ color:var(--muted); font-size:13px; }}
    .score-row strong {{ text-align:right; font-size:13px; }}
    .score-track {{ height:10px; background:#e5ebf1; border-radius:999px; overflow:hidden; }}
    .score-track i {{ display:block; height:100%; background:var(--accent); }}
    details summary {{ cursor:pointer; font-weight:700; }}
    .status {{ min-height:22px; color:var(--muted); }}
    @media (max-width:920px) {{ header {{ padding:20px 16px; }} .hero {{ grid-template-columns:1fr; }} .steps, .metrics {{ grid-template-columns:1fr 1fr; }} dl {{ grid-template-columns:1fr; }} }}
    @media (max-width:620px) {{ .steps, .metrics {{ grid-template-columns:1fr; }} .score-row {{ grid-template-columns:1fr; }} .score-row strong {{ text-align:left; }} }}
  </style>
</head>
<body>
  <header>
    <h1>FCAPSule AI P1 Demo</h1>
    <p class="muted">A guided local view of the incident capture, evidence reduction, domain map, and DeepSeek model comparison.</p>
  </header>
  <main>
    <section class="hero">
      <div class="hero-card primary">
        <h2>What is happening?</h2>
        <p class="takeaway">{html.escape(_plain_result(summary))}</p>
        <div class="mini-note">
          <strong>How to read this:</strong> FCAPSule is not trying to declare a final root cause. It reduces noisy telemetry into a smaller evidence capsule, then checks whether model-written explanations stay grounded in that evidence.
        </div>
      </div>
      <div class="hero-card">
        <h2>Start Here</h2>
        <p class="muted">Use these in order during a demo. The current page already shows the latest recorded result.</p>
        <div class="actions">
          <button data-action="capture-p1">1. Capture fresh failure</button>
          <button data-action="run-p1">2. Run P1 reduction</button>
          <button data-action="compare">3. Compare DeepSeek models</button>
        </div>
        <p id="status" class="status">Ready. DeepSeek key loaded: {str(summary['api_key_available']).lower()}. Env file: {html.escape(str(summary.get('env_loaded_from') or 'not found'))}.</p>
      </div>
    </section>
    <section class="steps">
      {_workflow_steps(summary)}
    </section>
    <section class="metrics">
      <div class="card metric">Log compression<strong>{_pct(evaluation.get('log_compression_ratio'))}</strong><small>How much raw log volume was removed while keeping representatives.</small></div>
      <div class="card metric">Signal preserved<strong>{_pct(evaluation.get('important_signal_preservation'))}</strong><small>Important alerts, logs, and metrics kept in the capsule.</small></div>
      <div class="card metric">Grounded claims<strong>{_pct(evaluation.get('hypothesis_grounding_score'))}</strong><small>Hypothesis citations that point to real selected evidence IDs.</small></div>
      <div class="card metric">Model winner<strong class="winner">{html.escape(str(models.get('winner', 'pending')))}</strong><small>Best output using the same capsule input and scoring rubric.</small></div>
    </section>
    <section class="panel">
      <h2>Model Comparison, In Plain English</h2>
      <p>{html.escape(_comparison_takeaway(summary))}</p>
      <p class="muted">The comparison is fair because both models receive the same capsule and the same prompt. Higher is better. A good answer should cover the incident signals, cite valid evidence IDs, and avoid pretending the final root cause is proven.</p>
      <div class="comparison-grid">{_model_cards(summary)}</div>
    </section>
    <section class="panel">
      <h2>Telemetry Domains Used by FCAPSule</h2>
      <p class="muted">These are not media types like audio or images. They are observability signal families that require different handling before they can be compared together.</p>
      <div class="grid">{_domain_cards(summary)}</div>
    </section>
    <section class="panel">
      <h2>Open Detailed Artifacts</h2>
      <div class="actions">
        <a class="button secondary" href="/dashboard.html" target="_blank">Open generated dashboard</a>
        <a class="button secondary" href="/outputs/capsule.md" target="_blank">Open Markdown capsule</a>
        <a class="button secondary" href="/api/summary" target="_blank">Open JSON summary</a>
      </div>
    </section>
    <section class="panel">
      <details>
        <summary>Technical run log</summary>
        <p class="muted">This is the compact printout useful for copying into notes or checking exact counts.</p>
        <pre id="printout">{html.escape(printout)}</pre>
      </details>
    </section>
  </main>
  <script>
    const statusEl = document.getElementById('status');
    async function postAction(action) {{
      statusEl.textContent = 'Running ' + action + '...';
      for (const button of document.querySelectorAll('button[data-action]')) button.disabled = true;
      try {{
        const response = await fetch('/api/' + action, {{ method: 'POST' }});
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.error || 'Action failed');
        statusEl.textContent = payload.message;
        location.reload();
      }} catch (error) {{
        statusEl.textContent = 'Error: ' + error.message;
      }} finally {{
        for (const button of document.querySelectorAll('button[data-action]')) button.disabled = false;
      }}
    }}
    for (const button of document.querySelectorAll('button[data-action]')) {{
      button.addEventListener('click', () => postAction(button.dataset.action));
    }}
  </script>
</body>
</html>
"""


class DemoRequestHandler(BaseHTTPRequestHandler):
    server: "DemoServer"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[demo-ui] {self.address_string()} - {format % args}")

    def _send_bytes(self, data: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send_bytes(json.dumps(payload, indent=2).encode("utf-8"), "application/json; charset=utf-8", status)

    def _summary(self) -> dict[str, Any]:
        return build_demo_summary(self.server.case_dir, self.server.output_dir)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/index.html"}:
            page = _html_page_live()
            self._send_bytes(page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if self.path == "/api/state":
            self._send_json(self.server.state.snapshot())
            return
        if self.path == "/api/summary":
            self._send_json(self._summary())
            return
        if self.path == "/dashboard.html":
            path = render_dashboard(self.server.output_dir)
            self._serve_file(path, "text/html; charset=utf-8")
            return
        if self.path == "/outputs/capsule.md":
            self._serve_file(self.server.output_dir / "capsule.md", "text/markdown; charset=utf-8")
            return
        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self.path == "/api/reset":
                self.server.state.reset()
                self._send_json({"ok": True, "message": "Demo UI reset."})
                return
            if self.path == "/api/start-capture":
                if self.server.state.snapshot()["running"]:
                    self._send_json({"ok": False, "error": "A demo job is already running."}, HTTPStatus.CONFLICT)
                    return
                self.server.state.reset()
                self.server.state.begin("capture")
                thread = threading.Thread(target=self._run_capture_job, daemon=True)
                thread.start()
                self._send_json({"ok": True, "message": "Capture job started."}, HTTPStatus.ACCEPTED)
                return
            if self.path == "/api/start-analysis":
                if self.server.state.snapshot()["running"]:
                    self._send_json({"ok": False, "error": "A demo job is already running."}, HTTPStatus.CONFLICT)
                    return
                self.server.state.clear_analysis()
                if not self.server.state.begin("analysis"):
                    self._send_json({"ok": False, "error": "A demo job is already running."}, HTTPStatus.CONFLICT)
                    return
                thread = threading.Thread(target=self._run_analysis_job, daemon=True)
                thread.start()
                self._send_json({"ok": True, "message": "Analysis job started."}, HTTPStatus.ACCEPTED)
                return
            if self.path == "/api/run-p1":
                result = investigate_case(self.server.case_dir, self.server.output_dir)
                render_dashboard(self.server.output_dir)
                self._send_json({"ok": True, "message": "P1 investigation complete.", "result": result})
                return
            if self.path == "/api/capture-p1":
                from scripts.capture_demo_incident import capture

                capture_result = capture(self.server.case_dir)
                investigation = investigate_case(self.server.case_dir, self.server.output_dir)
                render_dashboard(self.server.output_dir)
                self._send_json(
                    {
                        "ok": True,
                        "message": "Fresh failure captured and P1 investigation complete.",
                        "capture": capture_result,
                        "investigation": investigation,
                    }
                )
                return
            if self.path == "/api/compare":
                capsule = self.server.output_dir / "capsule.json"
                comparison = compare_models(capsule, self.server.output_dir, self.server.models)
                self._send_json({"ok": True, "message": "DeepSeek comparison complete.", "comparison": comparison})
                return
        except LLMUnavailableError as exc:
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        except Exception as exc:  # pragma: no cover - defensive UI path
            traceback.print_exc()
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._send_json({"ok": False, "error": "Unknown action"}, HTTPStatus.NOT_FOUND)

    def _run_capture_job(self) -> None:
        try:
            from scripts.capture_demo_incident import capture

            def progress(status: str, message: str, details: dict[str, Any]) -> None:
                self.server.state.emit("capture", status, message, details)

            result = capture(self.server.case_dir, progress=progress)
            self.server.state.set_capture_result(result)
            self.server.state.emit("capture", "done", "Alert triggered and raw case files are ready", result)
            self.server.state.finish()
        except Exception as exc:  # pragma: no cover - defensive UI path
            traceback.print_exc()
            self.server.state.emit("capture", "error", str(exc), {})
            self.server.state.finish(str(exc))

    def _run_analysis_job(self) -> None:
        try:
            def pipeline_progress(status: str, message: str, details: dict[str, Any]) -> None:
                self.server.state.emit("pipeline", status, message, details)

            result = investigate_case(self.server.case_dir, self.server.output_dir, progress=pipeline_progress)
            enriched = _pipeline_ui_result(self.server.output_dir, result)
            self.server.state.set_pipeline_result(enriched)
            self.server.state.emit("pipeline", "done", "Evidence capsule, evaluation, and archive are ready", enriched)

            def model_progress(status: str, message: str, details: dict[str, Any]) -> None:
                model = details.get("model")
                phase = model if model in PHASES else "pipeline"
                self.server.state.emit(phase, status, message, details)

            comparison = compare_models(
                self.server.output_dir / "capsule.json",
                self.server.output_dir,
                self.server.models,
                progress=model_progress,
            )
            self.server.state.set_comparison_result(comparison)
            for item in comparison.get("results", []):
                score = item.get("score", {})
                self.server.state.emit(
                    item["model"],
                    "done",
                    f"{item['model']} final score recorded",
                    {
                        "model": item["model"],
                        "total_score": score.get("total_score"),
                        "expected_signal_score": score.get("expected_signal_score"),
                        "citation_score": score.get("citation_score"),
                        "latency_seconds": item.get("latency_seconds"),
                        "total_tokens": item.get("usage", {}).get("total_tokens"),
                    },
                )
            self.server.state.finish()
        except Exception as exc:  # pragma: no cover - defensive UI path
            traceback.print_exc()
            self.server.state.emit("pipeline", "error", str(exc), {})
            self.server.state.finish(str(exc))

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self._send_json({"error": f"Missing file: {path}"}, HTTPStatus.NOT_FOUND)
            return
        self._send_bytes(path.read_bytes(), content_type)


class DemoServer(ThreadingHTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        case_dir: str | Path,
        output_dir: str | Path,
        models: tuple[str, ...] = DEFAULT_MODELS,
    ) -> None:
        super().__init__(address, DemoRequestHandler)
        load_env_file()
        self.case_dir = Path(case_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.models = models
        self.state = DemoRunState()


def serve_demo_ui(
    case_dir: str | Path,
    output_dir: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    models: tuple[str, ...] = DEFAULT_MODELS,
) -> None:
    """Run the blocking local demo UI server."""

    server = DemoServer((host, port), case_dir, output_dir, models)
    url = f"http://{host}:{server.server_port}/"
    print("FCAPSule AI P1 demo UI")
    print(f"URL: {url}")
    print(f"Case: {Path(case_dir).resolve()}")
    print(f"Output: {Path(output_dir).resolve()}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping demo UI.")
    finally:
        server.server_close()


def demo_ui_main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the optional FCAPSule P1 local demo UI.")
    parser.add_argument("--case", default="cases/case_001", help="Prepared case directory")
    parser.add_argument("--out", default="outputs/case_001", help="Output directory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    args = parser.parse_args(argv)
    serve_demo_ui(args.case, args.out, args.host, args.port, tuple(args.models))
    return 0


if __name__ == "__main__":
    raise SystemExit(demo_ui_main(sys.argv[1:]))
