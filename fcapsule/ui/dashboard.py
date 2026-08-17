"""Generate a static review dashboard for FCAPSule outputs."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _bar(value: float, label: str) -> str:
    pct = max(0.0, min(1.0, value)) * 100
    return (
        f"<div class='bar' aria-label='{html.escape(label)}'>"
        f"<span style='width:{pct:.1f}%'></span><strong>{pct:.1f}%</strong></div>"
    )


def _domain_cards(capsule: dict[str, Any]) -> str:
    cards = []
    palette = {
        "fault_events": "#a4385a",
        "log_text": "#27746f",
        "time_series_metrics": "#6b5aa6",
        "topology_metadata": "#6d6a26",
        "trace_access": "#94612f",
        "llm_reasoning": "#3c6f9f",
    }
    active_domains = {"fault_events", "log_text", "time_series_metrics", "topology_metadata", "trace_access", "llm_reasoning"}
    for domain_id, domain in capsule.get("domain_summary", {}).items():
        if domain_id not in active_domains:
            continue
        if not any(domain.get(key) for key in ("raw_items", "candidate_evidence_items", "selected_evidence_items")):
            continue
        color = palette.get(domain_id, "#57606a")
        cards.append(
            "<section class='domain-card' style='--accent:{color}'>"
            "<div class='domain-head'><h3>{label}</h3><code>{domain_id}</code></div>"
            "<p>{signal}</p>"
            "<dl><div><dt>Raw loaded</dt><dd>{raw}</dd></div>"
            "<div><dt>Scored</dt><dd>{candidates}</dd></div>"
            "<div><dt>Kept</dt><dd>{selected}</dd></div></dl>"
            "<small>{role}</small>"
            "</section>".format(
                color=color,
                label=html.escape(str(domain.get("label", domain_id))),
                domain_id=html.escape(domain_id),
                signal=html.escape(str(domain.get("signal_family", ""))),
                selected=domain.get("selected_evidence_items", 0),
                candidates=domain.get("candidate_evidence_items", 0),
                raw=domain.get("raw_items", 0),
                role=html.escape(str(domain.get("role", ""))),
            )
        )
    return "\n".join(cards) or "<p class='muted'>No active telemetry source domains were selected.</p>"


def _evidence_rows(capsule: dict[str, Any]) -> str:
    rows = []
    for item in capsule.get("selected_evidence", [])[:16]:
        rows.append(
            "<tr><td><code>{eid}</code></td><td>{domain}</td><td>{title}</td><td>{score:.3f}</td></tr>".format(
                eid=html.escape(item.get("evidence_id", "")),
                domain=html.escape(item.get("domain", "")),
                title=html.escape(str(item.get("title", ""))[:120]),
                score=float(item.get("score", 0)),
            )
        )
    return "\n".join(rows)


def _model_cards(comparison: dict[str, Any]) -> str:
    if not comparison:
        return "<p class='muted'>LLM comparison has not been generated yet.</p>"
    cards = []
    winner = comparison.get("winner")
    for result in comparison.get("results", []):
        score = result.get("score", {})
        is_winner = result.get("model") == winner
        cards.append(
            "<section class='model-card {winner_class}'>"
            "<div class='model-title'><h3>{model}</h3><span>{badge}</span></div>"
            "<p>Total score</p>{total}"
            "<p>Domain coverage</p>{domains}"
            "<p>Expected signal coverage</p>{signals}"
            "<p>Evidence breadth</p>{evidence_depth}"
            "<p>Citation validity</p>{citations}"
            "<dl><div><dt>Latency</dt><dd>{latency:.2f}s</dd></div>"
            "<div><dt>Finish</dt><dd>{finish}</dd></div>"
            "<div><dt>Total tokens</dt><dd>{tokens}</dd></div></dl>"
            "</section>".format(
                winner_class="winner" if is_winner else "",
                model=html.escape(result.get("model", "")),
                badge="Best observed" if is_winner else "Compared",
                total=_bar(float(score.get("total_score", 0)), "total score"),
                domains=_bar(float(score.get("domain_score", 0)), "domain coverage"),
                signals=_bar(float(score.get("expected_signal_score", 0)), "signal coverage"),
                evidence_depth=_bar(float(score.get("evidence_depth_score", 0)), "evidence breadth"),
                citations=_bar(float(score.get("citation_score", 0)), "citation validity"),
                latency=float(result.get("latency_seconds", 0)),
                finish=html.escape(str(result.get("finish_reason", ""))),
                tokens=result.get("usage", {}).get("total_tokens", 0),
            )
        )
    return "\n".join(cards)


def render_dashboard(output_dir: str | Path) -> Path:
    output = Path(output_dir).resolve()
    capsule = _load_json(output / "capsule.json")
    evaluation = _load_json(output / "evaluation.json")
    comparison = _load_json(output / "llm_comparison.json")
    case = capsule.get("case", {})
    title = f"{case.get('case_id', 'case')} FCAPSule Dashboard"
    winner_label = comparison.get("winner") or "No measurable winner"
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light; --ink:#17202a; --muted:#5c6670; --line:#d7dde5; --panel:#f7f9fb; --paper:#ffffff; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Arial, Helvetica, sans-serif; color:var(--ink); background:#eef2f6; }}
    header {{ padding:28px 32px 20px; background:#ffffff; border-bottom:1px solid var(--line); }}
    main {{ width:min(1180px, calc(100% - 32px)); margin:24px auto 42px; display:grid; gap:22px; }}
    h1 {{ margin:0 0 8px; font-size:28px; letter-spacing:0; }}
    h2 {{ margin:0 0 14px; font-size:20px; letter-spacing:0; }}
    h3 {{ margin:0; font-size:16px; letter-spacing:0; }}
    p {{ line-height:1.45; }}
    .muted {{ color:var(--muted); }}
    .summary {{ display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:12px; }}
    .stat, .domain-card, .model-card, .panel {{ background:var(--paper); border:1px solid var(--line); border-radius:2px; padding:16px; }}
    .stat strong {{ display:block; font-size:24px; margin-top:4px; }}
    .domains, .models {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:14px; }}
    .domain-card {{ border-top:5px solid var(--accent); }}
    .domain-head, .model-title {{ display:flex; align-items:center; justify-content:space-between; gap:10px; }}
    code {{ background:#eef2f6; padding:2px 5px; border-radius:4px; font-size:12px; }}
    dl {{ display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; margin:14px 0 0; }}
    dt {{ color:var(--muted); font-size:12px; }}
    dd {{ margin:2px 0 0; font-weight:700; }}
    small {{ display:block; color:var(--muted); margin-top:12px; }}
    .bar {{ height:24px; background:#e5eaf0; border-radius:4px; overflow:hidden; position:relative; margin:6px 0 12px; }}
    .bar span {{ display:block; height:100%; background:#2f7d62; }}
    .bar strong {{ position:absolute; inset:4px 8px auto auto; font-size:12px; color:#111; }}
    .winner {{ border-color:#2f7d62; box-shadow:0 0 0 2px rgba(47,125,98,.12); }}
    table {{ width:100%; border-collapse:collapse; background:#fff; }}
    th, td {{ border-bottom:1px solid var(--line); padding:10px; text-align:left; vertical-align:top; }}
    th {{ color:var(--muted); font-size:12px; }}
    @media (max-width:760px) {{ header {{ padding:22px 18px; }} .summary {{ grid-template-columns:1fr 1fr; }} dl {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(str(case.get('case_title', title)))}</h1>
    <p class="muted">Case <code>{html.escape(str(case.get('case_id', 'unknown')))}</code> - Service <code>{html.escape(str(case.get('service', 'unknown')))}</code> - generated from retained capsule outputs.</p>
  </header>
  <main>
    <section class="summary">
      <div class="stat">FCAPSule log reduction<strong>{float(evaluation.get('log_compression_ratio', 0))*100:.1f}%</strong></div>
      <div class="stat">Evidence signal kept<strong>{float(evaluation.get('important_signal_preservation', 0))*100:.1f}%</strong></div>
      <div class="stat">Hypothesis grounding<strong>{float(evaluation.get('hypothesis_grounding_score', 0))*100:.1f}%</strong></div>
      <div class="stat">LLM winner<strong>{html.escape(str(winner_label))}</strong></div>
    </section>
    <section class="panel">
      <h2>Operational Signal Domains</h2>
      <p class="muted">FM, PM, logs, topology, on-demand traces, and grounded reasoning are handled as distinct signal families.</p>
      <div class="domains">{_domain_cards(capsule)}</div>
    </section>
    <section class="panel">
      <h2>Model Comparison</h2>
      <p class="muted">{html.escape(str(comparison.get('interpretation', 'Run compare-llms to generate model observations.')))}</p>
      <div class="models">{_model_cards(comparison)}</div>
    </section>
    <section class="panel">
      <h2>Selected Evidence</h2>
      <table>
        <thead><tr><th>Evidence ID</th><th>Domain</th><th>Signal</th><th>Score</th></tr></thead>
        <tbody>{_evidence_rows(capsule)}</tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""
    path = output / "dashboard.html"
    path.write_text(html_text, encoding="utf-8")
    return path
