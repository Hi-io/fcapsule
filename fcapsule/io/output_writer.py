"""Render human-readable and machine-readable capsule outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def render_capsule(payload: dict[str, Any]) -> str:
    metadata = payload["case"]
    alert = payload["alerts"][0] if payload["alerts"] else None
    lines = [
        "# FCAPSule AI Evidence Capsule", "",
        "## 1. Case Summary", "",
        f"**{metadata['case_title']}** (`{metadata['case_id']}`) affects `{metadata['service']}` in `{metadata['namespace']}` / `{metadata['cluster']}`.", "",
        "This capsule records observed telemetry and ranked investigation paths. It does not assert a final root cause.", "",
        "## 2. Alert Context", "",
    ]
    if alert:
        lines.extend([f"- **{alert['alertname']}** is `{alert['status']}` with `{alert['severity']}` severity.", f"- Started at `{alert['startsAt']}`.", f"- {alert.get('annotations', {}).get('description', 'No description supplied.')}", ""])
    else:
        lines.extend(["No alert was supplied.", ""])
    lines.extend(["## 3. Telemetry Window", "", f"`{metadata['window']['start']}` to `{metadata['window']['end']}` ({metadata.get('timezone', 'UTC')}).", "", "## 4. Operational Signal Domains", "", "FCAPSule handles operational domains with different data shapes and analysis methods: fault management events, application logs, performance metrics, topology/configuration, on-demand trace access, and evidence-grounded AI reasoning.", ""])
    for domain_id, domain in payload.get("domain_summary", {}).items():
        lines.append(
            f"- **{domain['label']}** (`{domain_id}`): {domain['signal_family']}; "
            f"selected evidence `{domain['selected_evidence_items']}`; role: {domain['role']}"
        )
    lines.extend(["", "## 5. Incident Timeline", ""])
    for item in payload["timeline"]:
        lines.append(f"- `{item['timestamp']}` **{item['type']}**: {item['title']} - {item['description']}")
    lines.extend(["", "## 6. Selected Evidence", ""])
    for rank, item in enumerate(payload["selected_evidence"], start=1):
        domain = item.get("domain", "unknown")
        lines.append(f"{rank}. **{item['title']}** (`{item['evidence_id']}`, `{domain}`, score `{item['score']:.3f}`): {item['summary']}")
    lines.extend(["", "## 7. Log Compression Summary", "", f"- Raw lines: `{payload['log_summary']['raw_lines']}`", f"- Grouped templates: `{payload['log_summary']['template_count']}`", f"- Selected representative lines: `{payload['log_summary']['selected_lines']}`", ""])
    for template in payload["log_templates"][:10]:
        lines.append(f"- `{template['template_id']}`: {template['template']} - {template['count']} lines, levels {template['levels']}")
    lines.extend(["", "## 8. Metric Anomalies", ""])
    for metric in payload["metric_anomalies"]:
        lines.append(f"- `{metric['metric_id']}` **{metric['metric']}** (score `{metric['anomaly_score']:.3f}`): {metric['reason']}")
    lines.extend(["", "## 9. Ranked Investigation Hypotheses", ""])
    for hypothesis in payload["hypotheses"]:
        evidence = ", ".join(f"`{value}`" for value in hypothesis["supporting_evidence"])
        lines.extend([f"### {hypothesis['hypothesis_id']}: {hypothesis['hypothesis']}", "", f"- Verdict: `{hypothesis['verdict']}`; adjusted confidence: `{hypothesis['adjusted_confidence']:.3f}`.", f"- Supporting evidence: {evidence}.", f"- Verification: {' '.join(hypothesis['verification_notes'])}", ""])
    lines.extend(["## 10. Missing Evidence", ""])
    for item in payload["missing_evidence"]:
        lines.append(f"- {item}")
    lines.extend(["", "## 11. Suggested Next Steps", ""])
    for item in payload["next_steps"]:
        lines.append(f"- {item}")
    lines.extend(["", "## 12. Retention Note", "", "If raw telemetry expires, this capsule preserves the alert context, affected entities, event timing, anonymized representative log patterns, metric changes, evidence scores, grounded hypotheses, missing-data warnings, and next checks. Raw logs are intentionally not copied into the capsule archive.", "", "## 13. Evaluation Summary", ""])
    evaluation = payload.get("evaluation", {})
    if evaluation:
        lines.extend([f"- Log compression: `{evaluation['log_compression_ratio']:.1%}`", f"- Template reduction: `{evaluation['template_reduction_ratio']:.1%}`", f"- Signal preservation: `{evaluation['important_signal_preservation']:.1%}`", f"- Grounded hypothesis citations: `{evaluation['hypothesis_grounding_score']:.1%}`", f"- Retention survivability: `{evaluation['retention_survivability_score']:.1%}`"])
    else:
        lines.append("Evaluation is calculated after the initial capsule render.")
    lines.append("")
    return "\n".join(lines)


def write_outputs(output_dir: Path, payload: dict[str, Any], evidence: list[dict[str, Any]], baselines: dict[str, Any], evaluation: dict[str, Any] | None = None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    if evaluation is not None:
        payload["evaluation"] = evaluation
    write_json(output_dir / "capsule.json", payload)
    write_json(output_dir / "evidence.json", evidence)
    write_json(output_dir / "baselines.json", baselines)
    if evaluation is not None:
        write_json(output_dir / "evaluation.json", evaluation)
    (output_dir / "capsule.md").write_text(render_capsule(payload), encoding="utf-8")
