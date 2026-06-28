"""Optional local demo UI for FCAPSule P1."""

from __future__ import annotations

import html
import json
import os
import sys
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from fcapsule.io.case_loader import load_case
from fcapsule.pipeline import investigate_case
from fcapsule.reasoning.llm_client import LLMUnavailableError
from fcapsule.reasoning.model_comparator import DEFAULT_MODELS, compare_models
from fcapsule.ui.dashboard import render_dashboard


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
    for domain_id, domain in summary.get("domains", {}).items():
        cards.append(
            "<section class='card domain'>"
            f"<span>{html.escape(domain_id)}</span>"
            f"<h3>{html.escape(str(domain.get('label', domain_id)))}</h3>"
            f"<p>{html.escape(str(domain.get('signal_family', '')))}</p>"
            "<dl>"
            f"<div><dt>Selected</dt><dd>{domain.get('selected_evidence_items', 0)}</dd></div>"
            f"<div><dt>Candidates</dt><dd>{domain.get('candidate_evidence_items', 0)}</dd></div>"
            f"<div><dt>Raw</dt><dd>{domain.get('raw_items', 0)}</dd></div>"
            "</dl>"
            "</section>"
        )
    return "\n".join(cards) or "<p class='muted'>Run P1 to build the domain map.</p>"


def _model_rows(summary: dict[str, Any]) -> str:
    rows = []
    for item in summary["models"].get("results", []):
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('model')))}</td>"
            f"<td>{item.get('total_score')}</td>"
            f"<td>{item.get('domain_score')}</td>"
            f"<td>{item.get('expected_signal_score')}</td>"
            f"<td>{item.get('citation_score')}</td>"
            f"<td>{item.get('latency_seconds')}s</td>"
            f"<td>{item.get('total_tokens')}</td>"
            "</tr>"
        )
    return "\n".join(rows) or "<tr><td colspan='7'>Run DeepSeek comparison to populate this table.</td></tr>"


def _html_page(summary: dict[str, Any], printout: str) -> str:
    evaluation = summary["evaluation"]
    models = summary["models"]
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FCAPSule AI P1 Demo</title>
  <style>
    :root {{ --ink:#17202a; --muted:#607080; --line:#d8e0e8; --paper:#fff; --bg:#edf2f7; --accent:#2f7d62; --warn:#a4385a; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Arial, Helvetica, sans-serif; color:var(--ink); background:var(--bg); }}
    header {{ background:#ffffff; border-bottom:1px solid var(--line); padding:24px 30px; }}
    main {{ width:min(1240px, calc(100% - 28px)); margin:22px auto 44px; display:grid; gap:18px; }}
    h1 {{ margin:0 0 6px; font-size:28px; letter-spacing:0; }}
    h2 {{ margin:0 0 12px; font-size:20px; letter-spacing:0; }}
    h3 {{ margin:0 0 8px; font-size:16px; letter-spacing:0; }}
    p {{ line-height:1.45; }}
    button, a.button {{ appearance:none; border:1px solid #1f604a; background:var(--accent); color:white; border-radius:6px; padding:10px 12px; font-weight:700; cursor:pointer; text-decoration:none; display:inline-flex; align-items:center; justify-content:center; min-height:38px; }}
    button.secondary, a.secondary {{ background:#ffffff; color:var(--ink); border-color:var(--line); }}
    button:disabled {{ opacity:.6; cursor:wait; }}
    .muted {{ color:var(--muted); }}
    .panel, .card {{ background:var(--paper); border:1px solid var(--line); border-radius:8px; padding:16px; }}
    .actions {{ display:flex; flex-wrap:wrap; gap:10px; }}
    .metrics {{ display:grid; grid-template-columns:repeat(5, minmax(0, 1fr)); gap:12px; }}
    .metric strong {{ display:block; font-size:24px; margin-top:4px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(230px, 1fr)); gap:12px; }}
    .domain span {{ display:inline-block; color:#ffffff; background:#56616d; border-radius:4px; padding:2px 6px; font-size:12px; margin-bottom:10px; }}
    dl {{ display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; margin:12px 0 0; }}
    dt {{ color:var(--muted); font-size:12px; }}
    dd {{ margin:2px 0 0; font-weight:700; }}
    table {{ width:100%; border-collapse:collapse; background:#fff; }}
    th, td {{ border-bottom:1px solid var(--line); padding:10px; text-align:left; vertical-align:top; }}
    th {{ color:var(--muted); font-size:12px; }}
    pre {{ white-space:pre-wrap; overflow:auto; background:#101820; color:#eff6f4; border-radius:8px; padding:14px; margin:0; line-height:1.45; }}
    .winner {{ color:#0f6b4d; font-weight:700; }}
    .status {{ min-height:22px; color:var(--muted); }}
    @media (max-width:860px) {{ header {{ padding:20px 16px; }} .metrics {{ grid-template-columns:1fr 1fr; }} dl {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <header>
    <h1>FCAPSule AI P1 Demo</h1>
    <p class="muted">Optional local UI for showing the failing service capture, evidence reduction, domain map, and DeepSeek comparison.</p>
  </header>
  <main>
    <section class="panel">
      <h2>Run Controls</h2>
      <div class="actions">
        <button data-action="run-p1">Run P1 on current case</button>
        <button data-action="capture-p1">Capture fresh failure + run P1</button>
        <button data-action="compare">Run DeepSeek comparison</button>
        <a class="button secondary" href="/dashboard.html" target="_blank">Open dashboard</a>
        <a class="button secondary" href="/outputs/capsule.md" target="_blank">Open capsule</a>
      </div>
      <p id="status" class="status">Ready. DeepSeek key available: {str(summary['api_key_available']).lower()}.</p>
    </section>
    <section class="metrics">
      <div class="card metric">Log compression<strong>{_pct(evaluation.get('log_compression_ratio'))}</strong></div>
      <div class="card metric">Token reduction<strong>{_pct(evaluation.get('token_reduction_percentage'))}</strong></div>
      <div class="card metric">Signal preserved<strong>{_pct(evaluation.get('important_signal_preservation'))}</strong></div>
      <div class="card metric">Grounded claims<strong>{_pct(evaluation.get('hypothesis_grounding_score'))}</strong></div>
      <div class="card metric">LLM winner<strong class="winner">{html.escape(str(models.get('winner', 'pending')))}</strong></div>
    </section>
    <section class="panel">
      <h2>What Happened</h2>
      <pre id="printout">{html.escape(printout)}</pre>
    </section>
    <section class="panel">
      <h2>Operational Telemetry Domains</h2>
      <div class="grid">{_domain_cards(summary)}</div>
    </section>
    <section class="panel">
      <h2>DeepSeek Same-Input Comparison</h2>
      <p class="muted">{html.escape(str(models.get('interpretation') or 'Run the comparison to record model-specific observations.'))}</p>
      <table>
        <thead><tr><th>Model</th><th>Total</th><th>Domains</th><th>Signals</th><th>Citations</th><th>Latency</th><th>Tokens</th></tr></thead>
        <tbody>{_model_rows(summary)}</tbody>
      </table>
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
            summary = self._summary()
            page = _html_page(summary, render_printout(summary))
            self._send_bytes(page.encode("utf-8"), "text/html; charset=utf-8")
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
        self.case_dir = Path(case_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.models = models


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
