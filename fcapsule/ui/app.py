"""Dependency-light local web application for FCAPSule operations and testing."""

from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from fcapsule.control_plane import ControlPlane


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FCAPSule</title>
  <link rel="stylesheet" href="/assets/app.css">
</head>
<body>
  <header class="product-bar">
    <a class="wordmark" href="/console"><span>FC</span>APSule</a>
    <nav aria-label="Primary">
      <a href="/console" data-nav="console">Operations</a>
      <a href="/lab" data-nav="lab">Incident Lab</a>
    </nav>
    <div class="system-state"><i></i><span id="system-state">Ready</span></div>
  </header>
  <main id="app"><div class="boot">Loading FCAPSule...</div></main>
  <script src="/assets/app.js"></script>
</body>
</html>"""


CSS = r"""
:root {
  --ink:#172129; --muted:#68757f; --line:#d7dde1; --surface:#ffffff; --canvas:#f3f5f6;
  --nav:#11181d; --green:#167052; --green-bg:#e6f1ed; --amber:#9a5a12; --amber-bg:#fbefdb;
  --red:#a23838; --red-bg:#f8e7e7; --blue:#286a96; --blue-bg:#e6eff5;
}
* { box-sizing:border-box; }
body { margin:0; background:var(--canvas); color:var(--ink); font:14px/1.45 Inter, "Segoe UI", Arial, sans-serif; }
button, input, select { font:inherit; }
button { border:1px solid #16583f; border-radius:2px; background:var(--green); color:#fff; min-height:36px; padding:7px 13px; font-weight:650; cursor:pointer; }
button:hover { filter:brightness(.94); }
button:disabled { cursor:not-allowed; opacity:.52; }
button.secondary { color:var(--ink); background:#fff; border-color:#aeb8be; }
button.danger { color:var(--red); background:#fff; border-color:#d5a4a4; }
input, select { width:100%; height:36px; border:1px solid #aeb8be; border-radius:2px; background:#fff; color:var(--ink); padding:6px 9px; }
input:focus, select:focus { outline:2px solid #9bc4dc; outline-offset:0; border-color:var(--blue); }
.product-bar { height:58px; background:var(--nav); color:#fff; display:grid; grid-template-columns:220px 1fr auto; align-items:center; padding:0 24px; border-bottom:3px solid var(--green); }
.wordmark { color:#fff; text-decoration:none; font-size:21px; font-weight:750; letter-spacing:0; }
.wordmark span { color:#60bf9a; }
nav { height:100%; display:flex; align-items:stretch; }
nav a { color:#b8c2c8; text-decoration:none; padding:0 18px; display:flex; align-items:center; border-left:1px solid #293138; }
nav a:last-child { border-right:1px solid #293138; }
nav a.active { background:#202a31; color:#fff; box-shadow:inset 0 -3px #60bf9a; }
.system-state { display:flex; align-items:center; gap:8px; color:#c7d0d5; font-size:12px; text-transform:uppercase; }
.system-state i { width:8px; height:8px; background:#60bf9a; border-radius:50%; }
.system-state.busy i { background:#efb654; }
.system-state.error i { background:#db6969; }
main { width:min(1460px, calc(100% - 32px)); margin:20px auto 42px; }
.page-head { display:flex; justify-content:space-between; align-items:end; gap:20px; margin-bottom:18px; }
.page-head h1 { margin:0; font-size:26px; font-weight:680; letter-spacing:0; }
.page-head p { color:var(--muted); margin:4px 0 0; }
.eyebrow { text-transform:uppercase; color:var(--muted); font-size:11px; letter-spacing:.08em; font-weight:700; }
.sheet { background:var(--surface); border:1px solid var(--line); border-radius:2px; }
.sheet-head { min-height:48px; padding:11px 14px; border-bottom:1px solid var(--line); display:flex; align-items:center; justify-content:space-between; gap:12px; }
.sheet-head h2 { margin:0; font-size:15px; font-weight:700; }
.sheet-body { padding:14px; }
.kpis { display:grid; grid-template-columns:repeat(4, 1fr); border:1px solid var(--line); background:#fff; margin-bottom:14px; }
.kpi { min-height:95px; padding:15px 17px; border-right:1px solid var(--line); }
.kpi:last-child { border-right:0; }
.kpi span { display:block; color:var(--muted); font-size:12px; }
.kpi strong { display:block; margin-top:7px; font-size:27px; font-weight:680; }
.kpi small { color:var(--muted); }
.grid-2 { display:grid; grid-template-columns:minmax(0, 1.55fr) minmax(310px, .75fr); gap:14px; align-items:start; }
.grid-2 > *, .stack, .sheet, .table-wrap { min-width:0; }
.stack { display:grid; gap:14px; }
.table-wrap { max-width:100%; overflow-x:auto; }
table { width:100%; border-collapse:collapse; }
th { background:#f7f8f9; color:#53616b; text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.04em; font-weight:700; padding:9px 11px; border-bottom:1px solid var(--line); }
td { padding:10px 11px; border-bottom:1px solid #e8ecef; vertical-align:middle; }
tbody tr:last-child td { border-bottom:0; }
tbody tr:hover { background:#fafbfb; }
.status { display:inline-flex; align-items:center; gap:6px; white-space:nowrap; font-size:12px; font-weight:650; }
.status:before { content:""; width:7px; height:7px; border-radius:50%; background:#8c989f; }
.status.healthy:before, .status.ready:before, .status.done:before { background:var(--green); }
.status.degraded:before, .status.running:before, .status.firing:before { background:var(--amber); }
.status.error:before, .status.critical:before { background:var(--red); }
.source-row { display:flex; gap:4px; flex-wrap:wrap; }
.source { border:1px solid #bdc7cd; color:#4e5d66; padding:2px 5px; font-size:10px; font-weight:700; background:#f8fafb; }
.source.on { border-color:#9acbb8; color:#135c43; background:#edf7f3; }
.empty { padding:34px 18px; text-align:center; color:var(--muted); }
.model-row { display:grid; grid-template-columns:minmax(0, 1fr) 78px 98px; align-items:center; gap:8px; padding:10px 0; border-bottom:1px solid #e8ecef; }
.model-row:last-child { border:0; }
.model-row strong { display:block; }
.model-row small { color:var(--muted); }
.toggle { display:flex; align-items:center; gap:6px; color:var(--muted); }
.toggle input { width:16px; height:16px; }
.model-compare { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; margin-top:12px; }
.model-result { border:1px solid var(--line); border-top:3px solid #97a4ac; padding:11px; }
.model-result.winner { border-top-color:var(--green); }
.model-result h3 { margin:0 0 8px; font-size:13px; }
.model-result dl { display:grid; grid-template-columns:repeat(3, 1fr); gap:7px; margin:0; }
.model-result dt { color:var(--muted); font-size:10px; text-transform:uppercase; }
.model-result dd { margin:2px 0 0; font-weight:700; }
.lab-grid { display:grid; grid-template-columns:320px minmax(0, 1fr); gap:14px; align-items:start; }
.control-panel { position:sticky; top:14px; }
.field { margin-bottom:12px; }
.field label { display:block; font-size:12px; font-weight:650; margin-bottom:5px; }
.field small { display:block; color:var(--muted); margin-top:4px; }
.form-pair { display:grid; grid-template-columns:1fr 1fr; gap:9px; }
.actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:15px; }
.phase-strip { display:grid; grid-template-columns:repeat(5, 1fr); border:1px solid var(--line); background:#fff; margin-bottom:14px; }
.phase { min-height:92px; padding:12px; border-right:1px solid var(--line); border-top:3px solid #b7c0c6; }
.phase:last-child { border-right:0; }
.phase.running { border-top-color:var(--blue); background:#f8fbfd; }
.phase.done { border-top-color:var(--green); }
.phase.error { border-top-color:var(--red); background:#fffafa; }
.phase.skipped { border-top-color:#929da4; }
.phase b { display:block; font-size:12px; margin-bottom:7px; }
.phase span { color:var(--muted); font-size:11px; line-height:1.35; display:block; }
.metrics-line { display:grid; grid-template-columns:repeat(6, 1fr); border-bottom:1px solid var(--line); }
.metric { min-height:86px; padding:12px; border-right:1px solid var(--line); }
.metric:last-child { border-right:0; }
.metric span { display:block; color:var(--muted); font-size:11px; text-transform:uppercase; }
.metric strong { display:block; font-size:22px; margin-top:7px; font-weight:680; overflow-wrap:anywhere; }
.event-list { max-height:340px; overflow:auto; }
.event { display:grid; grid-template-columns:74px 92px minmax(0, 1fr); gap:10px; padding:8px 11px; border-bottom:1px solid #e8ecef; }
.event time, .event span { color:var(--muted); font-size:12px; }
.event b { font-size:12px; text-transform:uppercase; }
.alert-sequence { position:relative; padding-left:24px; display:grid; gap:14px; }
.alert-sequence:before { content:""; position:absolute; left:7px; top:6px; bottom:6px; width:2px; background:#d7dde1; }
.alert-item { position:relative; }
.alert-item:before { content:""; position:absolute; left:-22px; top:5px; width:10px; height:10px; background:var(--red); border:2px solid #fff; box-shadow:0 0 0 1px #c86f6f; }
.alert-item strong { display:block; }
.alert-item small { color:var(--muted); }
.notice { border-left:3px solid var(--blue); background:var(--blue-bg); padding:10px 12px; color:#234e6b; }
.capsule-detail h3 { font-size:14px; margin:14px 0 6px; }
.capsule-detail p { color:var(--muted); margin:0; }
.evidence-list { margin:8px 0 0; padding:0; list-style:none; }
.evidence-list li { padding:7px 0; border-bottom:1px solid #e8ecef; }
.evidence-list code { color:var(--blue); }
.queue-note { color:var(--muted); font-size:12px; }
.incident-title { font-weight:700; display:block; }
.impact-line { color:var(--muted); font-size:12px; margin-top:3px; }
.report { margin-top:14px; border-top:3px solid var(--green); }
.report-banner { padding:18px; border-bottom:1px solid var(--line); background:#f9fbfa; display:flex; justify-content:space-between; align-items:start; gap:16px; }
.report-banner h2 { margin:4px 0 5px; font-size:20px; }
.report-banner p { margin:0; color:var(--muted); max-width:760px; }
.report-layout { display:grid; grid-template-columns:minmax(0, 1.35fr) minmax(300px, .75fr); gap:0; }
.report-main { padding:18px; border-right:1px solid var(--line); }
.report-side { padding:18px; background:#fbfcfc; }
.report-section { margin-bottom:23px; }
.report-section:last-child { margin-bottom:0; }
.report-section h3 { margin:0 0 9px; font-size:15px; }
.hypothesis { border-left:3px solid var(--amber); background:var(--amber-bg); padding:12px 13px; }
.hypothesis p { margin:0 0 8px; font-size:15px; }
.hypothesis small { color:#6d4b22; }
.impact-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); border:1px solid var(--line); }
.impact-item { padding:11px; border-right:1px solid var(--line); border-bottom:1px solid var(--line); }
.impact-item:nth-child(2n) { border-right:0; }
.impact-item:nth-last-child(-n + 2) { border-bottom:0; }
.impact-item span, .timeline-item small { display:block; color:var(--muted); font-size:11px; }
.impact-item strong { display:block; margin:3px 0; font-size:18px; }
.action-list { margin:0; padding:0; list-style:none; display:grid; gap:7px; }
.action-list li { border:1px solid var(--line); padding:9px 10px; background:#fff; }
.action-list li.urgent { border-left:3px solid var(--red); background:#fffafa; }
.action-list b { display:block; font-size:12px; margin-bottom:3px; }
.timeline { margin:0; padding:0; list-style:none; display:grid; gap:9px; }
.timeline-item { border-left:2px solid #aab6bd; padding-left:10px; }
.timeline-item.critical { border-left-color:var(--red); }
.timeline-item.warning { border-left-color:var(--amber); }
.coverage-list { display:grid; gap:7px; }
.coverage-item { display:flex; justify-content:space-between; gap:10px; border-bottom:1px solid #e8ecef; padding-bottom:7px; }
.coverage-item:last-child { border-bottom:0; }
.retention { border-left:3px solid var(--blue); background:var(--blue-bg); padding:11px; color:#234e6b; }
.retention.urgent { border-left-color:var(--amber); background:var(--amber-bg); color:#65491e; }
details.engineering { border:1px solid var(--line); background:#fafbfb; }
details.engineering summary { cursor:pointer; padding:10px 11px; font-weight:650; }
.diagnostics { padding:0 11px 11px; color:var(--muted); font-size:12px; }
.diagnostics dl { display:grid; grid-template-columns:1fr auto; gap:5px 12px; margin:0; }
.diagnostics dd { margin:0; color:var(--ink); font-weight:650; }
.boot { color:var(--muted); padding:40px; text-align:center; }
.mono { font-family:"Cascadia Mono", Consolas, monospace; font-size:12px; }
.right { text-align:right; }
@media (max-width:1050px) { .grid-2, .lab-grid, .report-layout { grid-template-columns:1fr; } .control-panel { position:static; } .phase-strip { grid-template-columns:repeat(3, 1fr); } .metrics-line { grid-template-columns:repeat(3, 1fr); } .report-main { border-right:0; border-bottom:1px solid var(--line); } }
@media (max-width:700px) { .product-bar { height:auto; min-height:58px; padding:10px 14px; grid-template-columns:1fr auto; } nav { grid-column:1/-1; order:3; margin-top:8px; } nav a { min-height:40px; } .system-state { justify-self:end; } main { width:calc(100% - 18px); margin-top:12px; } .page-head, .report-banner { align-items:start; flex-direction:column; } .kpis, .phase-strip, .metrics-line, .impact-grid { grid-template-columns:1fr 1fr; } .kpi:nth-child(2) { border-right:0; } .kpi { border-bottom:1px solid var(--line); } .impact-item:nth-child(2n) { border-right:0; } .event { grid-template-columns:62px 1fr; } .event span { display:none; } .form-pair, .model-compare { grid-template-columns:1fr; } .incident-table .wide-only { display:none; } .incident-table td, .incident-table th { padding:9px 7px; } .incident-table button { padding:6px 8px; font-size:12px; } }
"""


JS = r"""
const view = location.pathname.startsWith('/lab') ? 'lab' : 'console';
const app = document.querySelector('#app');
const fmt = new Intl.NumberFormat('en-US');
const pct = value => typeof value === 'number' ? (value * 100).toFixed(1) + '%' : '--';
const bytes = value => {
  if (!value) return '0 B';
  const units = ['B','KB','MB','GB']; let unit = 0; let number = Number(value);
  while (number >= 1024 && unit < units.length - 1) { number /= 1024; unit++; }
  return number.toFixed(unit ? 1 : 0) + ' ' + units[unit];
};
const safe = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const shortTime = value => value ? new Date(value).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : '--';
document.querySelector(`[data-nav="${view}"]`)?.classList.add('active');
let lastState = null;
let selectedReport = null;
const requestedReportId = new URLSearchParams(location.search).get('incident');

function setSystem(state) {
  const node = document.querySelector('.system-state');
  const label = document.querySelector('#system-state');
  node.className = 'system-state' + (state.error ? ' error' : state.running ? ' busy' : '');
  label.textContent = state.error ? 'Attention' : state.running ? (state.active_job || 'Working') : 'Ready';
}

function status(value) { return `<span class="status ${safe(value)}">${safe(value)}</span>`; }
function sources(config) {
  return ['faults','metrics','logs','traces'].map(key => `<span class="source ${config?.[key]?.status ? 'on' : ''}">${key === 'faults' ? 'FM' : key === 'metrics' ? 'PM' : key === 'logs' ? 'LOG' : 'TRACE'}</span>`).join('');
}

function renderConsole(state) {
  const data = state.overview; const apps = data.applications; const incidents = data.incidents;
  app.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Incident workspace</div><h1>Operations</h1><p>Open the incident report to understand impact, evidence, and the next safe action.</p></div><a href="/lab"><button class="secondary">Open Test Lab</button></a></div>
    <section class="sheet"><div class="sheet-head"><h2>Incident queue</h2><span class="queue-note">${incidents.length} captured incident${incidents.length === 1 ? '' : 's'} · reports stay available after source telemetry expires</span></div><div class="table-wrap">${incidentTable(incidents, data.capsules)}</div></section>
    ${selectedReport ? reportPanel(selectedReport) : ''}
    <section class="sheet" style="margin-top:14px"><div class="sheet-head"><h2>Application coverage</h2><span class="queue-note">FM, PM, logs, and trace access configured per application</span></div><div class="table-wrap">${applicationTable(apps)}</div></section>`;
  document.querySelectorAll('[data-incident-report]').forEach(button => button.addEventListener('click', () => openReport(button.dataset.incidentReport)));
}

function applicationTable(items) {
  if (!items.length) return '<div class="empty">No applications are registered yet.</div>';
  return `<table><thead><tr><th>State</th><th>Application</th><th>Environment</th><th>Coverage</th><th>Latest incident</th><th class="right">Reports</th></tr></thead><tbody>${items.map(item => `<tr><td>${status(item.status)}</td><td><strong>${safe(item.name)}</strong><br><span class="mono">${safe(item.app_id)}</span></td><td>${safe(item.environment)}<br><small>${safe(item.cluster)} / ${safe(item.namespace)}</small></td><td><div class="source-row">${sources(item.source_config)}</div></td><td>${item.last_incident_at ? shortTime(item.last_incident_at) : '--'}</td><td class="right">${item.capsule_count || 0}</td></tr>`).join('')}</tbody></table>`;
}

function incidentTable(items, capsules) {
  if (!items.length) return '<div class="empty">No incidents have been captured yet.</div>';
  const reports = new Set(capsules.map(item => item.incident_id));
  return `<table class="incident-table"><thead><tr><th>Severity</th><th>Incident</th><th class="wide-only">Service</th><th class="wide-only">Started</th><th>Observed impact</th><th></th></tr></thead><tbody>${items.map(item => {
    const ready = reports.has(item.incident_id);
    return `<tr><td>${status(item.severity)}</td><td><span class="incident-title">${safe(item.summary || item.scenario)}</span><small class="mono">${safe(item.incident_id)}</small></td><td class="wide-only">${safe(item.app_id)}</td><td class="wide-only">${shortTime(item.started_at)}</td><td><span class="impact-line">${safe(item.alert_count)} alerts · ${fmt.format(item.log_count)} logs captured · ${fmt.format(item.metric_series_count)} PM series</span></td><td><button class="secondary" data-incident-report="${safe(item.incident_id)}">${ready ? 'Open report' : 'View incident'}</button></td></tr>`;
  }).join('')}</tbody></table>`;
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '--';
}

async function openReport(id) {
  const response = await fetch('/api/incidents/' + encodeURIComponent(id) + '/report'); const data = await response.json();
  if (!response.ok) { alert(data.error || 'Unable to load incident report'); return; }
  selectedReport = data;
  history.replaceState(null, '', '/console?incident=' + encodeURIComponent(id));
  renderConsole(lastState);
  document.querySelector('#incident-report')?.scrollIntoView({behavior:'smooth', block:'start'});
}

function reportPanel(payload) {
  if (!payload.report) return `<section id="incident-report" class="sheet report"><div class="sheet-body"><h2>Report is still being prepared</h2><p class="queue-note">The incident is captured. Build its capsule to preserve evidence and produce the responder report.</p></div></section>`;
  const report = payload.report; const incident = report.incident; const hypothesis = report.primary_hypothesis;
  const impact = report.impact.length ? report.impact.map(item => `<div class="impact-item"><span>${safe(item.label)}</span><strong>${safe(item.value)}</strong><small>baseline ${safe(item.baseline)} · ${safe(item.component || 'service')}</small></div>`).join('') : '<p class="queue-note">No material metric anomalies were retained.</p>';
  const timeline = report.timeline.length ? report.timeline.map(item => `<li class="timeline-item ${safe(item.severity)}"><strong>${safe(item.title)}</strong><small>${formatDate(item.timestamp)} · ${safe(item.description || '')}</small></li>`).join('') : '<li class="queue-note">No ordered incident events are available.</li>';
  const actions = report.actions.length ? report.actions.slice(0,6).map(item => `<li class="${safe(item.priority)}"><b>${item.priority === 'urgent' ? 'Do now' : 'Next check'}</b>${safe(item.action)}${item.reason ? `<small>${safe(item.reason)}</small>` : ''}</li>`).join('') : '<li>No follow-up action was generated.</li>';
  const evidence = report.supporting_evidence.slice(0,8).map(item => `<li><code>${safe(item.evidence_id)}</code> <strong>${safe(item.title)}</strong><br><span class="queue-note">${safe(item.summary)}</span></li>`).join('');
  const coverage = report.coverage.map(item => `<div class="coverage-item"><span>${safe(item.domain)}</span><span>${safe(item.detail)}</span></div>`).join('');
  const topology = report.topology.map(item => `${safe(item.from)} → ${safe(item.to)}`).join(' · ');
  const retentionClass = report.retention.trace_available && report.retention.source_retention_seconds ? 'retention urgent' : 'retention';
  return `<section id="incident-report" class="sheet report"><div class="report-banner"><div><div class="eyebrow">Incident report · ${safe(incident.incident_id)}</div><h2>${safe(incident.title)}</h2><p>${safe(incident.summary)}</p></div><div>${status(incident.severity)}<br><span class="queue-note">${safe(incident.service)} · ${safe(incident.cluster)} / ${safe(incident.namespace)}<br>${formatDate(incident.started_at)}</span></div></div><div class="report-layout"><div class="report-main"><section class="report-section"><h3>Observed impact</h3><div class="impact-grid">${impact}</div></section><section class="report-section"><h3>Likely failure path</h3><div class="hypothesis"><p>${safe(hypothesis.statement)}</p><small>${hypothesis.confidence != null ? `${Math.round(Number(hypothesis.confidence) * 100)}% confidence` : 'Confidence unavailable'} · ${safe(hypothesis.verdict)} · Requires verification</small></div>${hypothesis.uncertainty.length ? `<p class="queue-note" style="margin-top:9px">Still unknown: ${safe(hypothesis.uncertainty.join('; '))}</p>` : ''}</section><section class="report-section"><h3>What changed</h3><ul class="timeline">${timeline}</ul>${topology ? `<p class="queue-note" style="margin-top:10px">Observed path: ${topology}</p>` : ''}</section><section class="report-section"><h3>Supporting evidence</h3><ul class="evidence-list">${evidence || '<li>No retained evidence items.</li>'}</ul></section></div><aside class="report-side"><section class="report-section"><h3>Do next</h3><ul class="action-list">${actions}</ul></section><section class="report-section"><h3>Evidence availability</h3><div class="${retentionClass}"><strong>${report.retention.trace_available ? 'Trace window available' : 'Trace window unavailable'}</strong><br><span>${safe(report.retention.message)}</span>${report.retention.source_retention_seconds ? `<br><small>Source window: ${safe(report.retention.source_retention_seconds)} seconds · raw traces retained by FCAPSule: no</small>` : ''}</div></section><section class="report-section"><h3>Coverage</h3><div class="coverage-list">${coverage}</div></section><details class="engineering"><summary>Engineering diagnostics</summary><div class="diagnostics"><dl><dt>Derived evidence retained</dt><dd>${report.engineering_diagnostics.selected_evidence}</dd><dt>Log reduction</dt><dd>${pct(report.engineering_diagnostics.log_compression_ratio)}</dd><dt>Signal preservation</dt><dd>${pct(report.engineering_diagnostics.important_signal_preservation)}</dd><dt>Hypothesis grounding</dt><dd>${pct(report.engineering_diagnostics.hypothesis_grounding_score)}</dd><dt>Pipeline runtime</dt><dd>${Number(report.engineering_diagnostics.runtime_seconds || 0).toFixed(2)}s</dd></dl></div></details></aside></div></section>`;
}

function renderLab(state) {
  const live = state.live || {}; const phases = state.phases; const incident = state.current_incident_id;
  app.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Controlled test environment</div><h1>Incident Lab</h1><p>Run a real local failure and feed its evidence into FCAPSule.</p></div><span>${state.running ? status('running') : incident ? status('captured') : status('ready')}</span></div>
    <div class="lab-grid">
      <section class="sheet control-panel"><div class="sheet-head"><h2>Simulation</h2><span class="eyebrow">Local</span></div><div class="sheet-body">
        <div class="field"><label for="app-name">Application name</label><input id="app-name" value="Checkout Platform"></div>
        <div class="field"><label for="app-id">Application ID</label><input id="app-id" value="checkout-platform"></div>
        <div class="field"><label for="scenario">Failure scenario</label><select id="scenario"><option value="inventory-lock-contention">Inventory lock contention + retry storm</option></select><small>Two HTTP services, database-pool pressure and a closed circuit breaker.</small></div>
        <div class="form-pair"><div class="field"><label for="baseline">Baseline requests</label><input id="baseline" type="number" min="20" max="5000" value="180"></div><div class="field"><label for="incident">Incident requests</label><input id="incident" type="number" min="20" max="5000" value="240"></div></div>
        <div class="field"><label for="concurrency">Concurrency</label><input id="concurrency" type="number" min="1" max="64" value="24"></div>
        <div class="actions"><button id="run-simulation" ${state.running ? 'disabled' : ''}>Run simulation</button><button class="secondary" id="build-capsule" ${!incident || state.running ? 'disabled' : ''}>Build capsule</button><button class="secondary" id="reset-live" ${state.running ? 'disabled' : ''}>Reset view</button></div>
        ${state.error ? `<p class="notice" style="border-color:var(--red);background:var(--red-bg);color:var(--red)">${safe(state.error)}</p>` : ''}
      </div></section>
      <div class="stack">
        <section class="phase-strip">${phaseStrip(phases)}</section>
        <section class="sheet"><div class="sheet-head"><h2>Live incident</h2><span class="eyebrow">${state.running ? 'Updating' : incident ? 'Captured' : 'No run'}</span></div><div class="metrics-line">${metricTiles(live)}</div><div class="sheet-body"><div class="grid-2"><div><h2 style="font-size:14px;margin-top:0">Fault sequence</h2>${alertSequence(live)}</div><div><h2 style="font-size:14px;margin-top:0">Data policy</h2><p>FM events, PM series and logs are captured for analysis. Trace access is probed during the incident; raw spans remain in the source buffer and are not retained.</p>${live.trace_access ? `<div class="source-row"><span class="source on">TRACE ${safe(live.trace_access.probe_status)}</span><span class="source">${live.trace_access.source_retention_seconds}s source window</span><span class="source">0 spans retained</span></div>` : ''}</div></div></div></section>
        <section class="sheet"><div class="sheet-head"><h2>Activity</h2><span class="eyebrow">Newest first</span></div><div class="event-list">${eventList(state.events)}</div></section>
      </div>
    </div>`;
  document.querySelector('#run-simulation').addEventListener('click', runSimulation);
  document.querySelector('#build-capsule').addEventListener('click', buildCapsule);
  document.querySelector('#reset-live').addEventListener('click', resetLive);
}

function phaseStrip(phases) {
  const defs = [['services','Services'],['baseline','Baseline'],['injection','Injection'],['alerts','FM alerts'],['capsule','Capsule']];
  return defs.map(([id,label]) => { const phase = phases[id] || {status:'waiting',message:'Waiting'}; return `<div class="phase ${safe(phase.status)}"><b>${label}</b><span>${safe(phase.message)}</span></div>`; }).join('');
}
function metricTiles(live) {
  const values = [
    ['Requests', live.baseline_requests || live.total || '--'],
    ['Incident requests', live.incident_requests || '--'],
    ['Logs', live.log_count || live.captured_logs || '--'],
    ['PM series', live.metric_series_count || '--'],
    ['Retry factor', typeof live.retry_amplification === 'number' ? live.retry_amplification.toFixed(2) + 'x' : live.retry_amplification || '--'],
    ['Error rate', typeof live.error_rate === 'number' ? pct(live.error_rate) : '--']
  ];
  return values.map(([label,value]) => `<div class="metric"><span>${label}</span><strong>${safe(value)}</strong></div>`).join('');
}
function alertSequence(live) {
  const alerts = live.alerts || [];
  if (!alerts.length) return '<p class="empty" style="padding:18px 0;text-align:left">Alert sequence will appear after thresholds are crossed.</p>';
  const labels = {CheckoutRetryAmplification:'Retry amplification',InventoryPoolSaturation:'Pool saturation',CheckoutErrorBudgetBurn:'Error budget burn'};
  return `<div class="alert-sequence">${alerts.map((name,index) => `<div class="alert-item"><strong>${safe(labels[name] || name)}</strong><small>${index === 0 ? 'Upstream symptom detected' : index === 1 ? 'Dependency capacity exhausted' : 'User impact confirmed'}</small></div>`).join('')}</div>`;
}
function eventList(events) {
  if (!events.length) return '<div class="empty">No activity yet.</div>';
  return events.slice().reverse().map(item => `<div class="event"><time>${shortTime(item.time * 1000)}</time><b>${safe(item.phase)}</b><span>${safe(item.message)}</span></div>`).join('');
}
async function runSimulation() {
  const payload = {app_name:document.querySelector('#app-name').value,app_id:document.querySelector('#app-id').value,scenario:document.querySelector('#scenario').value,baseline_requests:Number(document.querySelector('#baseline').value),incident_requests:Number(document.querySelector('#incident').value),concurrency:Number(document.querySelector('#concurrency').value)};
  const response = await fetch('/api/simulations', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); if (!response.ok) { alert((await response.json()).error); } await refresh();
}
async function buildCapsule() { const response = await fetch('/api/capsules', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({incident_id:lastState.current_incident_id})}); if (!response.ok) alert((await response.json()).error); await refresh(); }
async function resetLive() { const response = await fetch('/api/reset', {method:'POST'}); if (!response.ok) alert((await response.json()).error); await refresh(); }

async function refresh() {
  try {
    const response = await fetch('/api/state', {cache:'no-store'}); const state = await response.json(); lastState = state; setSystem(state);
    if (view === 'console' && requestedReportId && !selectedReport) {
      const reportResponse = await fetch('/api/incidents/' + encodeURIComponent(requestedReportId) + '/report');
      if (reportResponse.ok) selectedReport = await reportResponse.json();
    }
    view === 'lab' ? renderLab(state) : renderConsole(state);
  } catch (error) { document.querySelector('#system-state').textContent = 'Disconnected'; }
}
refresh();
setInterval(() => {
  if (view === 'lab' || lastState?.running) refresh();
}, 1000);
"""


class FCAPSuleHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], control_plane: ControlPlane) -> None:
        self.control_plane = control_plane
        super().__init__(address, FCAPSuleHandler)


class FCAPSuleHandler(BaseHTTPRequestHandler):
    server: FCAPSuleHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, body: str, content_type: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/console")
            self.end_headers()
            return
        if path in {"/console", "/lab"}:
            self._text(HTML, "text/html; charset=utf-8")
            return
        if path == "/assets/app.css":
            self._text(CSS, "text/css; charset=utf-8")
            return
        if path == "/assets/app.js":
            self._text(JS, "text/javascript; charset=utf-8")
            return
        if path == "/api/state":
            self._json(self.server.control_plane.snapshot())
            return
        if path.startswith("/api/incidents/") and path.endswith("/report"):
            incident_id = unquote(path.removeprefix("/api/incidents/").removesuffix("/report").rstrip("/"))
            payload = self.server.control_plane.incident_report_payload(incident_id)
            if payload is None:
                self._json({"error": "Incident not found"}, HTTPStatus.NOT_FOUND)
            else:
                self._json(payload)
            return
        if path.startswith("/api/capsules/"):
            capsule_id = unquote(path.removeprefix("/api/capsules/"))
            payload = self.server.control_plane.capsule_payload(capsule_id)
            if payload is None:
                self._json({"error": "Capsule not found"}, HTTPStatus.NOT_FOUND)
            else:
                self._json(payload)
            return
        if path.startswith("/artifacts/"):
            self._artifact(path)
            return
        self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def _artifact(self, path: str) -> None:
        parts = path.strip("/").split("/", 2)
        if len(parts) != 3:
            self._json({"error": "Invalid artifact path"}, HTTPStatus.BAD_REQUEST)
            return
        _, capsule_id, name = parts
        record = self.server.control_plane.store.get_capsule(unquote(capsule_id))
        if not record or Path(name).name != name:
            self._json({"error": "Artifact not found"}, HTTPStatus.NOT_FOUND)
            return
        artifact = Path(record["output_dir"]) / name
        if not artifact.is_file():
            self._json({"error": "Artifact not found"}, HTTPStatus.NOT_FOUND)
            return
        body = artifact.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/api/simulations":
                if not self.server.control_plane.start_simulation(self._payload()):
                    self._json({"error": "Another job is already running"}, HTTPStatus.CONFLICT)
                else:
                    self._json({"ok": True}, HTTPStatus.ACCEPTED)
                return
            if path == "/api/capsules":
                payload = self._payload()
                if not self.server.control_plane.start_capsule(payload.get("incident_id")):
                    self._json({"error": "Another job is already running"}, HTTPStatus.CONFLICT)
                else:
                    self._json({"ok": True}, HTTPStatus.ACCEPTED)
                return
            if path == "/api/reset":
                self.server.control_plane.reset_live()
                self._json({"ok": True})
                return
            if path.startswith("/api/models/"):
                model_id = unquote(path.removeprefix("/api/models/"))
                payload = self._payload()
                profile = self.server.control_plane.store.update_model_profile(
                    model_id,
                    bool(payload.get("enabled")),
                    int(payload.get("max_tokens", 2400)),
                )
                self._json({"ok": True, "profile": profile})
                return
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)


def create_app_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    state_dir: str | Path = ".fcapsule",
) -> FCAPSuleHTTPServer:
    return FCAPSuleHTTPServer((host, port), ControlPlane(state_dir))


def serve_app(host: str = "127.0.0.1", port: int = 8765, state_dir: str | Path = ".fcapsule") -> None:
    server = create_app_server(host, port, state_dir)
    print(f"FCAPSule is running at http://{host}:{server.server_port}/console")
    print(f"Incident Lab: http://{host}:{server.server_port}/lab")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
