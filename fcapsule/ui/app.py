"""Dependency-light local web application for FCAPSule operations."""

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
    <a class="wordmark" href="/console"><span>FCAPS</span>ule</a>
    <nav aria-label="Primary">
      <a href="/console" data-nav="console">Operations</a>
      <a href="/targets" data-nav="targets">Targets</a>
      <a href="/settings" data-nav="settings">Settings</a>
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
.field { margin-bottom:12px; }
.field label { display:block; font-size:12px; font-weight:650; margin-bottom:5px; }
.field small { display:block; color:var(--muted); margin-top:4px; }
.form-pair { display:grid; grid-template-columns:1fr 1fr; gap:9px; }
.actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:15px; }
.notice { border-left:3px solid var(--blue); background:var(--blue-bg); padding:10px 12px; color:#234e6b; }
.target-grid { display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); border:1px solid var(--line); background:#fff; margin-bottom:14px; }
.target { min-height:128px; padding:14px; border-right:1px solid var(--line); }
.target:last-child { border-right:0; }
.target h3 { margin:0 0 8px; font-size:14px; display:flex; justify-content:space-between; gap:8px; }
.target dl { display:grid; grid-template-columns:1fr auto; gap:4px 10px; margin:10px 0 0; font-size:12px; }
.target dt { color:var(--muted); }
.target dd { margin:0; font-weight:650; }
.target-error { color:var(--red); font-size:12px; overflow-wrap:anywhere; }
.pod-list { display:grid; gap:5px; min-width:220px; }
.pod-line { display:flex; align-items:center; justify-content:space-between; gap:10px; font-size:12px; }
.pod-line code { overflow-wrap:anywhere; }
.capsule-detail h3 { font-size:14px; margin:14px 0 6px; }
.capsule-detail p { color:var(--muted); margin:0; }
.evidence-list { margin:8px 0 0; padding:0; list-style:none; }
.evidence-list li { padding:7px 0; border-bottom:1px solid #e8ecef; }
.evidence-list code { color:var(--blue); }
.queue-note { color:var(--muted); font-size:12px; }
.incident-title { font-weight:700; display:block; }
.impact-line { color:var(--muted); font-size:12px; margin-top:3px; }
.incident-row.open > td { background:#f3f8f5; }
.report-row > td { padding:0; background:#fbfcfc; }
.report { margin:0; border:0; border-top:3px solid var(--green); }
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
.ai-briefing { border-left:3px solid var(--green); background:var(--green-bg); padding:12px 13px; }
.ai-briefing p { margin:0 0 8px; }
.ai-briefing .briefing-action { font-weight:700; }
.ai-briefing .citations { color:#386052; font-size:12px; }
.impact-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); border:1px solid var(--line); }
.impact-item { padding:12px; border-right:1px solid var(--line); border-bottom:1px solid var(--line); }
.impact-item:nth-child(2n) { border-right:0; }
.impact-item:last-child:nth-child(odd) { border-right:0; }
.impact-item:nth-last-child(-n + 2) { border-bottom:0; }
.impact-item span, .timeline-item small { display:block; color:var(--muted); font-size:11px; }
.impact-item strong { display:block; margin:3px 0; font-size:18px; }
.impact-item p { color:var(--muted); font-size:12px; margin:6px 0 0; }
.action-list { margin:0; padding:0; list-style:none; display:grid; gap:7px; }
.action-list li { border-bottom:1px solid var(--line); padding:0 0 9px 28px; position:relative; background:transparent; }
.action-list li:last-child { border-bottom:0; }
.action-list li:before { content:attr(data-step); position:absolute; left:0; top:0; color:var(--muted); font-size:12px; font-weight:750; }
.action-list li.urgent:before { color:var(--red); }
.action-list b { display:block; font-size:12px; margin-bottom:3px; color:var(--amber); }
.action-list li.urgent b { color:var(--red); }
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
.report-tools { display:flex; gap:7px; flex-wrap:wrap; margin-top:11px; }
.row-actions { display:flex; gap:6px; justify-content:flex-end; flex-wrap:wrap; min-width:190px; }
.row-actions button { min-height:32px; padding:5px 9px; font-size:12px; }
.queue-tabs { display:flex; gap:7px; align-items:center; }
.alert-rule { margin-top:8px; border-top:1px solid #ead9bc; padding-top:6px; }
.alert-rule summary { cursor:pointer; color:#6d4b22; font-size:12px; font-weight:650; }
.alert-rule code { display:block; margin-top:7px; padding:7px; background:#fff; border:1px solid #ead9bc; overflow-wrap:anywhere; font-size:11px; }
.button-link { display:inline-flex; align-items:center; min-height:32px; padding:6px 9px; border:1px solid #aeb8be; background:#fff; color:var(--ink); text-decoration:none; font-size:12px; font-weight:650; }
.button-link:hover { border-color:#65737d; background:#f5f7f8; }
.evidence-domain { margin:0 0 23px; }
.evidence-domain:last-child { margin-bottom:0; }
.evidence-domain h3 { display:flex; align-items:center; gap:8px; }
.domain-mark { font-size:10px; color:#53616b; text-transform:uppercase; letter-spacing:.05em; border:1px solid #bdc7cd; padding:2px 5px; font-weight:700; }
.alert-records { display:grid; gap:7px; }
.alert-record { border-left:3px solid var(--red); padding:8px 10px; background:#fffafa; }
.alert-record.warning { border-left-color:var(--amber); background:#fffaf1; }
.alert-record strong { display:block; }
.alert-record small { color:var(--muted); display:block; margin-top:2px; }
.pm-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); border:1px solid var(--line); }
.pm-signal { padding:12px; border-right:1px solid var(--line); border-bottom:1px solid var(--line); }
.pm-signal:nth-child(2n) { border-right:0; }
.pm-signal:nth-last-child(-n + 2) { border-bottom:0; }
.pm-signal h4 { margin:0; font-size:13px; }
.pm-signal p { color:var(--muted); font-size:12px; margin:5px 0 7px; }
.pm-values { display:flex; gap:12px; color:var(--muted); font-size:11px; }
.pm-values b { display:block; color:var(--ink); font-size:14px; }
.spark { display:block; width:100%; height:72px; margin:8px 0; overflow:visible; }
.spark .axis { stroke:#d9e0e3; stroke-width:1; }
.spark .baseline { stroke:#8da0aa; stroke-dasharray:3 3; stroke-width:1.25; }
.spark .series { fill:none; stroke:var(--blue); stroke-width:2.5; }
.spark .incident { stroke:var(--red); stroke-width:1.25; stroke-dasharray:3 3; }
.log-pattern { border-top:1px solid var(--line); }
.log-pattern:first-of-type { border-top:0; }
.log-pattern summary { cursor:pointer; padding:9px 0; list-style:none; }
.log-pattern summary::-webkit-details-marker { display:none; }
.log-pattern summary:before { content:'+'; display:inline-block; width:18px; color:var(--blue); font-weight:750; }
.log-pattern[open] summary:before { content:'−'; }
.log-pattern strong { font-family:"Cascadia Mono", Consolas, monospace; font-size:12px; overflow-wrap:anywhere; }
.log-pattern p { margin:0 0 8px 18px; color:var(--muted); font-size:12px; }
.log-lines { margin:0 0 10px 18px; padding:9px; overflow:auto; background:#171f24; color:#dfe9ed; font:11px/1.5 "Cascadia Mono", Consolas, monospace; white-space:pre-wrap; }
.report-note { color:var(--muted); font-size:12px; margin:0 0 9px; }
.boot { color:var(--muted); padding:40px; text-align:center; }
.mono { font-family:"Cascadia Mono", Consolas, monospace; font-size:12px; }
.right { text-align:right; }
@media (max-width:1050px) { .grid-2, .report-layout, .target-grid { grid-template-columns:1fr; } .target { border-right:0; border-bottom:1px solid var(--line); } .target:last-child { border-bottom:0; } .report-main { border-right:0; border-bottom:1px solid var(--line); } }
@media (max-width:700px) { .product-bar { height:auto; min-height:58px; padding:10px 14px; grid-template-columns:1fr auto; } nav { grid-column:1/-1; order:3; margin-top:8px; } nav a { min-height:40px; } .system-state { justify-self:end; } main { width:calc(100% - 18px); margin-top:12px; } .page-head, .report-banner { align-items:start; flex-direction:column; } .kpis, .impact-grid { grid-template-columns:1fr 1fr; } .kpi:nth-child(2) { border-right:0; } .kpi { border-bottom:1px solid var(--line); } .impact-item:nth-child(2n) { border-right:0; } .form-pair, .model-compare { grid-template-columns:1fr; } .incident-table .wide-only { display:none; } .incident-table td, .incident-table th { padding:9px 7px; } .incident-table button { padding:6px 8px; font-size:12px; } }
"""


JS = r"""
const view = location.pathname.startsWith('/settings') ? 'settings' : location.pathname.startsWith('/targets') ? 'targets' : 'console';
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
let showArchived = false;
const requestedReportId = new URLSearchParams(location.search).get('incident');

function setSystem(state) {
  const node = document.querySelector('.system-state');
  const label = document.querySelector('#system-state');
  node.className = 'system-state' + (state.error ? ' error' : state.running ? ' busy' : '');
  label.textContent = state.error ? 'Attention' : state.running ? (state.active_job || 'Working') : 'Ready';
}

function status(value) { return `<span class="status ${safe(value)}">${safe(value)}</span>`; }
function sources(config) {
  const active = value => ['connected','observed','available'].includes(value);
  return ['faults','metrics','logs','configuration','traces'].map(key => `<span class="source ${active(config?.[key]?.status) ? 'on' : ''}" title="${safe(config?.[key]?.status || 'not configured')}">${key === 'faults' ? 'FM' : key === 'metrics' ? 'PM' : key === 'logs' ? 'LOG' : key === 'configuration' ? 'CFG' : 'TRACE'}</span>`).join('');
}

function renderConsole(state) {
  const data = state.overview;
  const incidents = showArchived ? data.archived_incidents : data.incidents;
  app.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Incident workspace</div><h1>Operations</h1><p>Captured incidents and the evidence needed to investigate them.</p></div><a href="/targets"><button class="secondary">Manage targets</button></a></div>
    <section class="sheet"><div class="sheet-head"><div><h2>${showArchived ? 'Archived incidents' : 'Incident queue'}</h2><span class="queue-note">${incidents.length} incident${incidents.length === 1 ? '' : 's'}${showArchived ? ' hidden from the active queue' : ' requiring or retaining investigation context'}</span></div><div class="queue-tabs"><button class="secondary" id="queue-mode">${showArchived ? `Active (${data.incidents.length})` : `Archived (${data.archived_incidents.length})`}</button></div></div><div class="table-wrap">${incidentTable(incidents, data.capsules, showArchived)}</div></section>`;
  document.querySelector('#queue-mode').addEventListener('click', () => { selectedReport = null; showArchived = !showArchived; renderConsole(lastState); });
  document.querySelectorAll('[data-incident-report]').forEach(button => button.addEventListener('click', () => openReport(button.dataset.incidentReport)));
  document.querySelectorAll('[data-build-capsule]').forEach(button => button.addEventListener('click', () => buildCapsule(button.dataset.buildCapsule)));
  document.querySelectorAll('[data-ai-briefing]').forEach(button => button.addEventListener('click', () => generateAiBriefing(button.dataset.aiBriefing)));
  document.querySelectorAll('[data-archive]').forEach(button => button.addEventListener('click', () => changeIncidentState(button.dataset.archive, 'archive')));
  document.querySelectorAll('[data-restore]').forEach(button => button.addEventListener('click', () => changeIncidentState(button.dataset.restore, 'restore')));
  document.querySelectorAll('[data-delete]').forEach(button => button.addEventListener('click', () => deleteIncident(button.dataset.delete)));
}

function renderTargets(state) {
  const config = state.sources.configuration; const targets = state.sources.targets || {};
  const observedApps = state.overview.applications.filter(item => item.status !== 'not_observed');
  const targetCard = (name, label) => {
    const item = targets[name] || {}; const ok = item.ok === true;
    const details = name === 'prometheus' ? [['Healthy targets', `${item.healthy_targets ?? '--'} / ${item.active_targets ?? '--'}`], ['Version', item.version || '--']]
      : name === 'opensearch' ? [['Indexed documents', item.documents != null ? fmt.format(item.documents) : '--'], ['Version', item.version || '--']]
      : [['Authentication', item.authentication || 'ServiceAccount'], ['Version', item.version || '--']];
    return `<article class="target"><h3>${label}${status(item.ok == null ? 'not tested' : ok ? 'healthy' : 'error')}</h3>${item.error ? `<p class="target-error">${safe(item.error)}</p>` : `<dl>${details.map(row => `<dt>${safe(row[0])}</dt><dd>${safe(row[1])}</dd>`).join('')}</dl>`}</article>`;
  };
  app.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Source connections</div><h1>Targets</h1><p>Connect the cluster data used for discovery and incident capture.</p></div><div class="actions"><button class="secondary" id="test-targets">Test connections</button><button id="sync-targets" ${state.running ? 'disabled' : ''}>Sync now</button></div></div>
    <section class="target-grid">${targetCard('prometheus','Prometheus')}${targetCard('opensearch','OpenSearch')}${targetCard('kubernetes','Kubernetes API')}</section>
    <div class="grid-2"><section class="sheet"><div class="sheet-head"><h2>Connection settings</h2><span class="queue-note">${config.enabled ? 'Automatic polling enabled' : 'Manual synchronization'}</span></div><div class="sheet-body">
      <div class="field"><label for="prometheus-url">Prometheus URL</label><input id="prometheus-url" value="${safe(config.prometheus_url)}"></div>
      <div class="field"><label for="opensearch-url">OpenSearch URL</label><input id="opensearch-url" value="${safe(config.opensearch_url)}"></div>
      <div class="form-pair"><div class="field"><label for="opensearch-index">Log index pattern</label><input id="opensearch-index" value="${safe(config.opensearch_index)}"></div><div class="field"><label for="cluster-name">Cluster name</label><input id="cluster-name" value="${safe(config.cluster_name)}"></div></div>
      <div class="field"><label for="kubernetes-url">Kubernetes API URL</label><input id="kubernetes-url" value="${safe(config.kubernetes_url || '')}" placeholder="In-cluster ServiceAccount"><small>Leave blank inside Kubernetes. FCAPSule uses the mounted ServiceAccount and cluster CA.</small></div>
      <div class="field"><label for="source-namespaces">Observed namespaces</label><input id="source-namespaces" value="${safe(config.namespaces.join(', '))}" placeholder="default, production"><small>Only these namespaces can create incidents. Leave blank to observe all namespaces allowed by RBAC.</small></div>
      <div class="form-pair"><div class="field"><label for="poll-interval">Poll interval (seconds)</label><input id="poll-interval" type="number" min="10" max="3600" value="${safe(config.poll_interval_seconds)}"></div><div class="field"><label for="window-minutes">Incident window (minutes)</label><input id="window-minutes" type="number" min="2" max="120" value="${safe(config.incident_window_minutes)}"></div></div>
      <label class="toggle"><input id="source-enabled" type="checkbox" ${config.enabled ? 'checked' : ''}>Poll sources and capture new firing alerts automatically</label>
      <label class="toggle" style="margin-top:8px"><input id="auto-reports" type="checkbox" ${config.auto_build_reports ? 'checked' : ''}>Build a responder report after capture</label>
      ${window.targetNotice ? `<p class="notice">${safe(window.targetNotice)}</p>` : ''}
      <div class="actions"><button id="save-targets">Save settings</button></div>
    </div></section>
    <section class="sheet"><div class="sheet-head"><h2>Discovery</h2><span class="queue-note">${state.sources.last_sync_at ? formatDate(state.sources.last_sync_at) : 'Not synchronized'}</span></div><div class="sheet-body">
      <div class="kpis" style="grid-template-columns:repeat(3,1fr);margin:0"><div class="kpi"><span>Pods visible</span><strong>${state.sources.pods_visible || 0}</strong></div><div class="kpi"><span>Applications</span><strong>${state.sources.applications_visible || 0}</strong></div><div class="kpi"><span>Active alerts</span><strong>${state.sources.active_alerts || 0}</strong></div></div>
      ${state.sources.error ? `<p class="target-error">${safe(state.sources.error)}</p>` : '<p class="queue-note" style="margin-top:12px">Discovery maps Kubernetes pods to Prometheus metrics, OpenSearch logs, and referenced configuration.</p>'}
    </div></section></div>
    <section class="sheet" style="margin-top:14px"><div class="sheet-head"><h2>Application coverage</h2><span class="queue-note">${observedApps.length} currently observed application${observedApps.length === 1 ? '' : 's'}</span></div><div class="table-wrap">${applicationTable(observedApps)}</div></section>`;
  document.querySelector('#save-targets').addEventListener('click', saveTargets);
  document.querySelector('#test-targets').addEventListener('click', testTargets);
  document.querySelector('#sync-targets').addEventListener('click', syncTargets);
}

function renderSettings(state) {
  const ai = state.ai;
  const options = ai.models.map(item => `<option value="${safe(item.model_id)}">${safe(item.model_id)}</option>`).join('');
  const keyState = ai.api_key_configured ? 'Configured locally' : 'Not configured';
  app.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Runtime configuration</div><h1>Settings</h1><p>Manage incident lifecycle and optional AI-assisted briefings.</p></div></div>
    <div class="grid-2"><section class="sheet"><div class="sheet-head"><h2>Incident lifecycle</h2><span class="queue-note">Automatic cleanup</span></div><div class="sheet-body">
      <div class="field"><label for="retention-days">Incident retention (days)</label><input id="retention-days" type="number" min="1" max="3650" value="${safe(state.settings.incident_retention_days)}"><small>Active and archived incidents older than this are permanently removed with their managed reports and capsules. Default: 30 days.</small></div>
      ${window.generalSettingsNotice ? `<p class="notice">${safe(window.generalSettingsNotice)}</p>` : ''}
      <div class="actions"><button id="save-general-settings">Save retention</button></div>
    </div></section><section class="sheet"><div class="sheet-head"><h2>AI briefing</h2><span class="queue-note">${safe(keyState)}</span></div><div class="sheet-body">
      <div class="field"><label for="ai-provider">Provider</label><input id="ai-provider" value="DeepSeek-compatible" disabled></div>
      <div class="field"><label for="ai-model">Model ID</label><input id="ai-model" list="ai-model-options" value="${safe(ai.model)}"><datalist id="ai-model-options">${options}</datalist><small>Configured models are available here; a compatible model ID may also be entered.</small></div>
      <div class="field"><label for="ai-max-tokens">Maximum completion tokens</label><input id="ai-max-tokens" type="number" min="256" max="16000" value="${safe(ai.max_tokens)}"></div>
      <div class="field"><label for="ai-key">API key</label><input id="ai-key" type="password" autocomplete="new-password" placeholder="${ai.api_key_configured ? 'Leave blank to keep the saved key' : 'Paste a key to enable briefings'}"><small>Saved only to the local .env file. It is never shown in this console or saved in the database.</small></div>
      ${window.settingsNotice ? `<p class="notice">${safe(window.settingsNotice)}</p>` : ''}
      <div class="actions"><button id="save-ai-settings">Save AI settings</button></div>
    </div></section></div>`;
  document.querySelector('#save-general-settings').addEventListener('click', saveGeneralSettings);
  document.querySelector('#save-ai-settings').addEventListener('click', saveAiSettings);
}

function applicationTable(items) {
  if (!items.length) return '<div class="empty">No applications are registered yet.</div>';
  return `<table><thead><tr><th>State</th><th>Application</th><th>Environment</th><th>Observed pods</th><th>Coverage</th><th>Latest incident</th><th class="right">Reports</th></tr></thead><tbody>${items.map(item => `<tr><td>${status(item.status)}</td><td><strong>${safe(item.name)}</strong><br><span class="mono">${safe(item.app_id)}</span></td><td>${safe(item.environment)}<br><small>${safe(item.cluster)} / ${safe(item.namespace)}</small></td><td><div class="pod-list">${(item.source_config?.pods || []).map(pod => `<span class="pod-line"><code>${safe(pod.name)}</code>${status(pod.ready ? 'ready' : pod.phase || 'unknown')}</span>`).join('') || '--'}</div></td><td><div class="source-row">${sources(item.source_config)}</div></td><td>${item.last_incident_at ? shortTime(item.last_incident_at) : '--'}</td><td class="right">${item.capsule_count || 0}</td></tr>`).join('')}</tbody></table>`;
}

async function saveTargets() {
  const payload = {prometheus_url:document.querySelector('#prometheus-url').value, opensearch_url:document.querySelector('#opensearch-url').value, opensearch_index:document.querySelector('#opensearch-index').value, kubernetes_url:document.querySelector('#kubernetes-url').value, cluster_name:document.querySelector('#cluster-name').value, namespaces:document.querySelector('#source-namespaces').value, poll_interval_seconds:Number(document.querySelector('#poll-interval').value), incident_window_minutes:Number(document.querySelector('#window-minutes').value), enabled:document.querySelector('#source-enabled').checked, auto_build_reports:document.querySelector('#auto-reports').checked};
  const response = await fetch('/api/settings/sources', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}); const result = await response.json();
  if (!response.ok) { alert(result.error || 'Unable to save targets'); return; }
  window.targetNotice = 'Target settings saved.'; lastState.sources.configuration = result; renderTargets(lastState);
}

async function testTargets() {
  window.targetNotice = 'Testing source connections...'; renderTargets(lastState);
  const response = await fetch('/api/sources/test', {method:'POST'}); const result = await response.json();
  lastState.sources.targets = result.targets || {}; window.targetNotice = result.ok ? 'All source connections are healthy.' : 'One or more targets could not be reached.'; renderTargets(lastState);
}

async function syncTargets() {
  const response = await fetch('/api/sources/sync', {method:'POST'}); const result = await response.json();
  if (!response.ok) { alert(result.error || 'Unable to synchronize sources'); return; }
  window.targetNotice = 'Source synchronization started.'; await refresh();
}

function incidentTable(items, capsules, archived = false) {
  if (!items.length) return '<div class="empty">No incidents have been captured yet.</div>';
  const reports = new Set(capsules.map(item => item.incident_id));
  const openId = selectedReport?.incident?.incident_id || selectedReport?.report?.incident?.incident_id;
  return `<table class="incident-table"><thead><tr><th>Severity</th><th>Incident</th><th class="wide-only">Application</th><th class="wide-only">Started</th><th>Captured context</th><th></th></tr></thead><tbody>${items.map(item => {
    const ready = reports.has(item.incident_id);
    const isOpen = openId === item.incident_id;
    const reportRow = isOpen ? `<tr class="report-row"><td colspan="6">${reportPanel(selectedReport)}</td></tr>` : '';
    const reportAction = ready ? `<button class="secondary" data-incident-report="${safe(item.incident_id)}" aria-expanded="${isOpen}">${isOpen ? 'Close report' : 'Open report'}</button>` : archived ? '' : `<button data-build-capsule="${safe(item.incident_id)}" ${lastState?.running ? 'disabled' : ''}>Build report</button>`;
    const lifecycleAction = archived ? `<button class="secondary" data-restore="${safe(item.incident_id)}">Restore</button><button class="danger" data-delete="${safe(item.incident_id)}">Delete</button>` : `<button class="secondary" data-archive="${safe(item.incident_id)}">Archive</button>`;
    return `<tr class="incident-row ${isOpen ? 'open' : ''}"><td>${status(item.severity)}</td><td><span class="incident-title">${safe(item.summary || item.scenario)}</span><small class="mono">${safe(item.incident_id)}</small></td><td class="wide-only">${safe(item.app_id)}</td><td class="wide-only">${shortTime(item.started_at)}</td><td><span class="impact-line">${safe(item.alert_count)} FM alerts · ${fmt.format(item.log_count)} logs captured · ${fmt.format(item.metric_series_count)} PM series</span></td><td><div class="row-actions">${reportAction}${lifecycleAction}</div></td></tr>${reportRow}`;
  }).join('')}</tbody></table>`;
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '--';
}

async function openReport(id) {
  const currentId = selectedReport?.incident?.incident_id || selectedReport?.report?.incident?.incident_id;
  if (currentId === id) {
    selectedReport = null;
    history.replaceState(null, '', '/console');
    renderConsole(lastState);
    return;
  }
  const response = await fetch('/api/incidents/' + encodeURIComponent(id) + '/report'); const data = await response.json();
  if (!response.ok) { alert(data.error || 'Unable to load incident report'); return; }
  selectedReport = data;
  history.replaceState(null, '', '/console?incident=' + encodeURIComponent(id));
  renderConsole(lastState);
}

async function generateAiBriefing(id) {
  window.aiBriefingLoading = true;
  renderConsole(lastState);
  try {
    const response = await fetch('/api/incidents/' + encodeURIComponent(id) + '/briefing', {method:'POST'});
    const result = await response.json();
    if (!selectedReport) return;
    selectedReport.ai_briefing = result;
  } catch (error) {
    if (selectedReport) selectedReport.ai_briefing = {status:'unavailable', message:'The AI briefing service could not be reached.'};
  } finally {
    window.aiBriefingLoading = false;
    renderConsole(lastState);
  }
}

async function buildCapsule(id) {
  const response = await fetch('/api/capsules', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({incident_id:id})});
  const result = await response.json();
  if (!response.ok) { alert(result.error || 'Unable to build report'); return; }
  await refresh();
}

async function saveAiSettings() {
  const payload = {model:document.querySelector('#ai-model').value, max_tokens:Number(document.querySelector('#ai-max-tokens').value), api_key:document.querySelector('#ai-key').value};
  const response = await fetch('/api/settings/ai', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const result = await response.json();
  if (!response.ok) { alert(result.error || 'Unable to save settings'); return; }
  window.settingsNotice = 'Settings saved locally.';
  lastState.ai = result;
  renderSettings(lastState);
}

async function saveGeneralSettings() {
  const payload = {incident_retention_days:Number(document.querySelector('#retention-days').value)};
  const response = await fetch('/api/settings/general', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const result = await response.json();
  if (!response.ok) { alert(result.error || 'Unable to save retention'); return; }
  lastState.settings = result; window.generalSettingsNotice = 'Retention policy saved.'; renderSettings(lastState);
}

async function changeIncidentState(id, action) {
  const response = await fetch(`/api/incidents/${encodeURIComponent(id)}/${action}`, {method:'POST'});
  const result = await response.json();
  if (!response.ok) { alert(result.error || `Unable to ${action} incident`); return; }
  selectedReport = null; await refresh();
}

async function deleteIncident(id) {
  if (!confirm('Permanently delete this incident, report, and capsule?')) return;
  const response = await fetch(`/api/incidents/${encodeURIComponent(id)}`, {method:'DELETE'});
  const result = await response.json();
  if (!response.ok) { alert(result.error || 'Unable to delete incident'); return; }
  selectedReport = null; await refresh();
}

function sparkline(signal) {
  const values = signal.values || [];
  if (values.length < 2) return '';
  const width = 320; const height = 72; const pad = 5;
  const numbers = values.map(point => Number(point.value));
  const floor = Math.min(...numbers, Number(signal.baseline_value || 0));
  const ceiling = Math.max(...numbers, Number(signal.baseline_value || 0));
  const range = Math.max(ceiling - floor, 0.01);
  const point = (value, index) => `${(index / (values.length - 1) * (width - pad * 2) + pad).toFixed(1)},${(height - pad - ((Number(value) - floor) / range * (height - pad * 2))).toFixed(1)}`;
  const path = values.map((item, index) => `${index ? 'L' : 'M'}${point(item.value, index)}`).join(' ');
  const baselineY = (height - pad - ((Number(signal.baseline_value || 0) - floor) / range * (height - pad * 2))).toFixed(1);
  const peakIndex = numbers.indexOf(Math.max(...numbers));
  const peakX = (peakIndex / (values.length - 1) * (width - pad * 2) + pad).toFixed(1);
  return `<svg class="spark" viewBox="0 0 ${width} ${height}" role="img" aria-label="${safe(signal.label)} across the incident window"><line class="axis" x1="0" y1="${height - pad}" x2="${width}" y2="${height - pad}"/><line class="baseline" x1="0" y1="${baselineY}" x2="${width}" y2="${baselineY}"/><line class="incident" x1="${peakX}" y1="0" x2="${peakX}" y2="${height}"/><path class="series" d="${path}"/></svg>`;
}

function reportPanel(payload) {
  if (!payload.report) return `<section id="incident-report" class="sheet report"><div class="sheet-body"><h2>Report is not built yet</h2><p class="queue-note">The incident is captured. Build the report to preserve the selected evidence before source telemetry expires.</p><div class="report-tools"><button data-build-capsule="${safe(payload.incident.incident_id)}" ${lastState?.running ? 'disabled' : ''}>Build report</button></div></div></section>`;
  const report = payload.report; const incident = report.incident; const hypothesis = report.primary_hypothesis;
  const impact = report.impact.length ? report.impact.map(item => `<div class="impact-item"><span>${safe(item.label)}</span><strong>${safe(item.value)}</strong><small>${safe(item.component || 'service')} · ${safe(item.baseline_text)}</small><p>${safe(item.meaning)}</p></div>`).join('') : '<p class="queue-note">No material metric anomalies were retained.</p>';
  const timeline = report.timeline.length ? report.timeline.map(item => `<li class="timeline-item ${safe(item.severity)}"><strong>${safe(item.title)}</strong><small>${formatDate(item.timestamp)} · ${safe(item.description || '')}</small></li>`).join('') : '<li class="queue-note">No ordered incident events are available.</li>';
  const actions = report.actions.length ? report.actions.slice(0,6).map((item, index) => `<li class="${safe(item.priority)}" data-step="${index + 1}"><b>${item.priority === 'urgent' ? 'Preserve now' : index === 0 ? 'Start here' : 'Then'}</b>${safe(item.action)}${item.reason ? `<small class="queue-note">${safe(item.reason)}</small>` : ''}</li>`).join('') : '<li>No follow-up action was generated.</li>';
  const faultAlerts = report.fault_alerts.map(item => `<div class="alert-record ${safe(item.severity)}"><strong>${safe(item.name)}</strong><small>${formatDate(item.timestamp)}${item.service ? ` · ${safe(item.service)}` : ''}</small><span>${safe(item.description)}</span>${item.rule?.query ? `<details class="alert-rule"><summary>Why this alert fired</summary><code>${safe(item.rule.query)}</code><p class="queue-note">Condition must hold for ${safe(item.rule.duration || 0)} seconds · rule health: ${safe(item.rule.health || 'unknown')}</p></details>` : ''}</div>`).join('');
  const pmSignals = report.pm_signals.map(item => `<article class="pm-signal"><h4>${safe(item.label)}</h4><p>${safe(item.meaning)}</p>${sparkline(item)}<div class="pm-values"><span>Typical<b>${safe(item.baseline)}</b></span><span>Peak<b>${safe(item.peak)}</b></span></div></article>`).join('');
  const logPatterns = report.log_patterns.map(item => `<details class="log-pattern"><summary><strong>${safe(item.pattern)}</strong><span class="queue-note"> · ${safe(item.summary)}</span></summary><p>${safe(formatDate(item.first_seen))}${item.last_seen ? ` to ${safe(formatDate(item.last_seen))}` : ''} · retained pattern ${safe(item.evidence_id)}</p>${item.examples?.length ? `<pre class="log-lines">${safe(item.examples.join('\n'))}</pre>` : ''}</details>`).join('');
  const configuration = (report.configuration_evidence || []).map(item => `<div class="alert-record"><strong>${safe(item.kind)} · ${safe(item.name)}</strong><small>${safe(item.namespace)}${item.content_hash ? ` · snapshot ${safe(item.content_hash)}` : ''}</small><span>${safe(item.summary)}</span>${item.images?.length ? `<p class="queue-note">Images: ${safe(item.images.join(', '))}</p>` : ''}${item.keys?.length ? `<p class="queue-note">Config keys: ${safe(item.keys.join(', '))}</p>` : ''}</div>`).join('');
  const ai = payload.ai_briefing;
  const aiBriefing = ai?.status === 'ready' ? `<div class="ai-briefing"><p>${safe(ai.briefing.operator_brief)}</p><p class="briefing-action">First action: ${safe(ai.briefing.first_action)}</p><small>${safe(ai.briefing.why_this_first)}</small><p class="citations">Grounded in ${safe(ai.briefing.evidence_ids.join(', '))} · ${safe(ai.model)}</p></div>` : ai ? `<p class="queue-note">AI briefing unavailable: ${safe(ai.message || 'The response was not accepted.')}</p>` : `<div class="report-tools"><button class="secondary" data-ai-briefing="${safe(incident.incident_id)}" ${window.aiBriefingLoading ? 'disabled' : ''}>${window.aiBriefingLoading ? 'Generating briefing…' : 'Generate AI briefing'}</button></div>`;
  const coverage = report.coverage.map(item => `<div class="coverage-item"><span>${safe(item.domain)}</span><span>${safe(item.detail)}</span></div>`).join('');
  const topology = report.topology.map(item => `${safe(item.from)} → ${safe(item.to)}`).join(' · ');
  const retentionClass = report.retention.trace_available && report.retention.source_retention_seconds ? 'retention urgent' : 'retention';
  const capsuleId = payload.record?.capsule_id;
  const archiveName = `fcapsule_${incident.incident_id}.zip`;
  const archiveUrl = capsuleId ? `/artifacts/${encodeURIComponent(capsuleId)}/${encodeURIComponent(archiveName)}` : '';
  const reportUrl = capsuleId ? `/artifacts/${encodeURIComponent(capsuleId)}/incident_report.json` : '';
  return `<section id="incident-report" class="report"><div class="report-banner"><div><div class="eyebrow">Incident report · ${safe(incident.incident_id)}</div><h2>${safe(incident.title)}</h2><p>${safe(incident.summary)}</p></div><div>${status(incident.severity)}<br><span class="queue-note">${safe(incident.service)} · ${safe(incident.cluster)} / ${safe(incident.namespace)}<br>${formatDate(incident.started_at)}</span></div></div><div class="report-layout"><div class="report-main"><section class="report-section"><h3>Observed impact</h3><div class="impact-grid">${impact}</div></section><section class="report-section"><h3>FCAPSule assessment <span class="domain-mark">FM + PM + logs + config</span></h3><div class="hypothesis"><p>${safe(hypothesis.statement)}</p><small>${hypothesis.confidence != null ? `${Math.round(Number(hypothesis.confidence) * 100)}% confidence` : 'Confidence unavailable'} · ${safe(hypothesis.verdict)} · investigation path, not final root cause</small></div>${hypothesis.uncertainty.length ? `<p class="queue-note" style="margin-top:9px">To confirm: ${safe(hypothesis.uncertainty.join('; '))}</p>` : ''}</section><section class="report-section"><h3>AI incident briefing <span class="domain-mark">Cited response</span></h3>${aiBriefing}</section><section class="evidence-domain"><h3>Fault management <span class="domain-mark">FM alerts</span></h3><p class="report-note">Alerts that established the incident window and user impact.</p><div class="alert-records">${faultAlerts || '<span class="queue-note">No fault events were retained.</span>'}</div></section><section class="evidence-domain"><h3>Performance management <span class="domain-mark">PM time series</span></h3><p class="report-note">Trend lines compare normal behaviour with the incident window. The red marker identifies the observed peak.</p><div class="pm-grid">${pmSignals || '<span class="queue-note">No PM trend qualified for chart display. Retained PM evidence is reflected in Observed impact.</span>'}</div>${report.pm_coverage_note ? `<p class="queue-note" style="margin-top:8px">${safe(report.pm_coverage_note)}</p>` : ''}</section><section class="evidence-domain"><h3>Log evidence <span class="domain-mark">Anonymized patterns</span></h3><p class="report-note">Only the selected patterns and representative lines are retained. Expand a pattern to inspect the examples used in the assessment.</p>${logPatterns || '<span class="queue-note">No selected log patterns were retained.</span>'}</section><section class="evidence-domain"><h3>Configuration at incident time <span class="domain-mark">Kubernetes</span></h3><p class="report-note">Pod images and referenced ConfigMaps are captured without reading Secrets. Sensitive-looking ConfigMap keys are masked.</p><div class="alert-records">${configuration || '<span class="queue-note">No referenced configuration was available.</span>'}</div></section><section class="report-section"><h3>Incident sequence</h3><ul class="timeline">${timeline}</ul>${topology ? `<p class="queue-note" style="margin-top:10px">Observed dependency path: ${topology}</p>` : ''}</section></div><aside class="report-side"><section class="report-section"><h3>Recommended follow-up</h3><ul class="action-list">${actions}</ul></section><section class="report-section"><h3>Trace availability</h3><div class="${retentionClass}"><strong>${report.retention.trace_available ? 'Trace window available' : 'Trace window unavailable'}</strong><br><span>${safe(report.retention.message)}</span>${report.retention.source_retention_seconds ? `<br><small>Source window: ${safe(report.retention.source_retention_seconds)} seconds · raw traces retained by FCAPSule: no</small>` : ''}</div></section><section class="report-section"><h3>Incident package</h3><p class="report-note">The report and curated evidence are retained in the FCAPSule archive. Raw logs and traces remain in their source systems.</p><div class="report-tools">${reportUrl ? `<a class="button-link" href="${reportUrl}" download>Report JSON</a>` : ''}${archiveUrl ? `<a class="button-link" href="${archiveUrl}" download>Capsule archive</a>` : ''}</div></section><section class="report-section"><h3>Coverage</h3><div class="coverage-list">${coverage}</div></section><details class="engineering"><summary>Engineering diagnostics</summary><div class="diagnostics"><dl><dt>Derived evidence retained</dt><dd>${report.engineering_diagnostics.selected_evidence}</dd><dt>Log reduction</dt><dd>${pct(report.engineering_diagnostics.log_compression_ratio)}</dd><dt>Signal preservation</dt><dd>${pct(report.engineering_diagnostics.important_signal_preservation)}</dd><dt>Hypothesis grounding</dt><dd>${pct(report.engineering_diagnostics.hypothesis_grounding_score)}</dd><dt>Pipeline runtime</dt><dd>${Number(report.engineering_diagnostics.runtime_seconds || 0).toFixed(2)}s</dd></dl></div></details></aside></div></section>`;
}

async function refresh() {
  try {
    const response = await fetch('/api/state', {cache:'no-store'}); const state = await response.json(); lastState = state; setSystem(state);
    if (view === 'console' && requestedReportId && !selectedReport) {
      const reportResponse = await fetch('/api/incidents/' + encodeURIComponent(requestedReportId) + '/report');
      if (reportResponse.ok) selectedReport = await reportResponse.json();
    }
    view === 'settings' ? renderSettings(state) : view === 'targets' ? renderTargets(state) : renderConsole(state);
  } catch (error) { document.querySelector('#system-state').textContent = 'Disconnected'; }
}
refresh();
setInterval(() => {
  if (lastState?.running || view === 'targets') refresh();
}, 1000);
"""


class FCAPSuleHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], control_plane: ControlPlane) -> None:
        self.control_plane = control_plane
        super().__init__(address, FCAPSuleHandler)

    def server_close(self) -> None:
        self.control_plane.stop_live_monitoring()
        super().server_close()


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
        if path in {"/console", "/targets", "/settings"}:
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
        if path == "/healthz":
            self._json({"status": "ok"})
            return
        if path == "/api/settings/ai":
            self._json(self.server.control_plane.ai_configuration())
            return
        if path == "/api/settings/general":
            self._json(self.server.control_plane.general_configuration())
            return
        if path == "/api/settings/sources":
            self._json(self.server.control_plane.source_configuration())
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
            if path == "/api/capsules":
                payload = self._payload()
                if not self.server.control_plane.start_capsule(payload.get("incident_id")):
                    self._json({"error": "Another job is already running"}, HTTPStatus.CONFLICT)
                else:
                    self._json({"ok": True}, HTTPStatus.ACCEPTED)
                return
            if path == "/api/settings/ai":
                self._json(self.server.control_plane.update_ai_configuration(self._payload()))
                return
            if path == "/api/settings/general":
                self._json(self.server.control_plane.update_general_configuration(self._payload()))
                return
            if path == "/api/settings/sources":
                self._json(self.server.control_plane.update_source_configuration(self._payload()))
                return
            if path == "/api/sources/test":
                result = self.server.control_plane.test_source_connections()
                self._json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if path == "/api/sources/sync":
                if not self.server.control_plane.start_source_sync():
                    self._json({"error": "Another job is already running"}, HTTPStatus.CONFLICT)
                else:
                    self._json({"ok": True}, HTTPStatus.ACCEPTED)
                return
            if path.startswith("/api/incidents/") and path.endswith("/briefing"):
                incident_id = unquote(path.removeprefix("/api/incidents/").removesuffix("/briefing").rstrip("/"))
                result = self.server.control_plane.generate_ai_briefing(incident_id)
                status = HTTPStatus.OK if result.get("status") == "ready" else HTTPStatus.SERVICE_UNAVAILABLE
                self._json(result, status)
                return
            if path.startswith("/api/incidents/") and path.endswith("/archive"):
                incident_id = unquote(path.removeprefix("/api/incidents/").removesuffix("/archive").rstrip("/"))
                self._json(self.server.control_plane.set_incident_archived(incident_id, True))
                return
            if path.startswith("/api/incidents/") and path.endswith("/restore"):
                incident_id = unquote(path.removeprefix("/api/incidents/").removesuffix("/restore").rstrip("/"))
                self._json(self.server.control_plane.set_incident_archived(incident_id, False))
                return
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/incidents/"):
                incident_id = unquote(path.removeprefix("/api/incidents/").rstrip("/"))
                self.server.control_plane.delete_incident(incident_id)
                self._json({"ok": True})
                return
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            self._json({"error": str(exc)}, HTTPStatus.NOT_FOUND)


def create_app_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    state_dir: str | Path = ".fcapsule",
) -> FCAPSuleHTTPServer:
    control_plane = ControlPlane(state_dir)
    server = FCAPSuleHTTPServer((host, port), control_plane)
    control_plane.start_live_monitoring()
    return server


def serve_app(host: str = "127.0.0.1", port: int = 8765, state_dir: str | Path = ".fcapsule") -> None:
    server = create_app_server(host, port, state_dir)
    print(f"FCAPSule is running at http://{host}:{server.server_port}/console")
    print(f"Source targets: http://{host}:{server.server_port}/targets")
    print(f"Settings: http://{host}:{server.server_port}/settings")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
