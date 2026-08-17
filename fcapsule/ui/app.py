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
.stack { display:grid; gap:14px; }
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
.lab-grid { display:grid; grid-template-columns:320px minmax(0, 1fr); gap:14px; align-items:start; }
.control-panel { position:sticky; top:14px; }
.field { margin-bottom:12px; }
.field label { display:block; font-size:12px; font-weight:650; margin-bottom:5px; }
.field small { display:block; color:var(--muted); margin-top:4px; }
.form-pair { display:grid; grid-template-columns:1fr 1fr; gap:9px; }
.actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:15px; }
.phase-strip { display:grid; grid-template-columns:repeat(6, 1fr); border:1px solid var(--line); background:#fff; margin-bottom:14px; }
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
.boot { color:var(--muted); padding:40px; text-align:center; }
.mono { font-family:"Cascadia Mono", Consolas, monospace; font-size:12px; }
.right { text-align:right; }
@media (max-width:1050px) { .grid-2, .lab-grid { grid-template-columns:1fr; } .control-panel { position:static; } .phase-strip { grid-template-columns:repeat(3, 1fr); } .metrics-line { grid-template-columns:repeat(3, 1fr); } }
@media (max-width:700px) { .product-bar { height:auto; min-height:58px; padding:10px 14px; grid-template-columns:1fr auto; } nav { grid-column:1/-1; order:3; margin-top:8px; } nav a { min-height:40px; } .system-state { justify-self:end; } main { width:min(100% - 18px, 1460px); margin-top:12px; } .page-head { align-items:start; flex-direction:column; } .kpis, .phase-strip, .metrics-line { grid-template-columns:1fr 1fr; } .kpi:nth-child(2) { border-right:0; } .kpi { border-bottom:1px solid var(--line); } .event { grid-template-columns:62px 1fr; } .event span { display:none; } .form-pair { grid-template-columns:1fr; } .table-wrap { overflow:auto; } }
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
  const data = state.overview; const totals = data.totals; const apps = data.applications; const capsules = data.capsules;
  app.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Control plane</div><h1>Operations</h1><p>Tracked applications and retained incident evidence.</p></div><a href="/lab"><button>Open Incident Lab</button></a></div>
    <section class="kpis">
      <div class="kpi"><span>Applications</span><strong>${fmt.format(totals.applications)}</strong><small>${totals.degraded_applications} degraded</small></div>
      <div class="kpi"><span>Incidents observed</span><strong>${fmt.format(totals.incidents)}</strong><small>FM-triggered windows</small></div>
      <div class="kpi"><span>Raw data inspected</span><strong>${bytes(totals.raw_bytes_observed)}</strong><small>Referenced at source</small></div>
      <div class="kpi"><span>Capsule data retained</span><strong>${bytes(totals.capsule_bytes_retained)}</strong><small>Derived evidence only</small></div>
    </section>
    <div class="grid-2">
      <div class="stack">
        <section class="sheet"><div class="sheet-head"><h2>Application inventory</h2><span class="eyebrow">${apps.length} registered</span></div><div class="table-wrap">${applicationTable(apps)}</div></section>
        <section class="sheet"><div class="sheet-head"><h2>Evidence capsules</h2><span class="eyebrow">Latest first</span></div><div class="table-wrap">${capsuleTable(capsules)}</div></section>
      </div>
      <div class="stack">
        <section class="sheet"><div class="sheet-head"><h2>Model profiles</h2><span class="eyebrow">${state.api_key_available ? 'Provider ready' : 'Key not loaded'}</span></div><div class="sheet-body" id="models">${modelProfiles(data.models)}</div></section>
        <section class="sheet"><div class="sheet-head"><h2>Capsule inspector</h2></div><div class="sheet-body capsule-detail" id="capsule-detail"><p>Select a capsule to inspect its strongest evidence.</p></div></section>
        <div class="notice">Trace sources are checked on demand. FCAPSule records availability and derived evidence, not raw spans.</div>
      </div>
    </div>`;
  document.querySelectorAll('[data-capsule]').forEach(button => button.addEventListener('click', () => inspectCapsule(button.dataset.capsule)));
  document.querySelectorAll('[data-save-model]').forEach(button => button.addEventListener('click', () => saveModel(button.dataset.saveModel)));
}

function applicationTable(items) {
  if (!items.length) return '<div class="empty">No applications registered. Use Incident Lab to create the first source.</div>';
  return `<table><thead><tr><th>State</th><th>Application</th><th>Environment</th><th>Signal sources</th><th>Last incident</th><th class="right">Capsules</th></tr></thead><tbody>${items.map(item => `<tr><td>${status(item.status)}</td><td><strong>${safe(item.name)}</strong><br><span class="mono">${safe(item.app_id)}</span></td><td>${safe(item.environment)}<br><small>${safe(item.cluster)} / ${safe(item.namespace)}</small></td><td><div class="source-row">${sources(item.source_config)}</div></td><td>${item.last_incident_at ? shortTime(item.last_incident_at) : '--'}</td><td class="right">${item.capsule_count || 0}</td></tr>`).join('')}</tbody></table>`;
}

function capsuleTable(items) {
  if (!items.length) return '<div class="empty">No capsules retained yet.</div>';
  return `<table><thead><tr><th>Status</th><th>Incident</th><th>Size</th><th>Log reduction</th><th>Signal retained</th><th>Model</th><th></th></tr></thead><tbody>${items.map(item => `<tr><td>${status(item.status)}</td><td><span class="mono">${safe(item.incident_id)}</span><br><small>${shortTime(item.created_at)}</small></td><td>${bytes(item.size_bytes)}</td><td>${pct(item.compression)}</td><td>${pct(item.signal_preservation)}</td><td>${safe(item.model_winner || 'Deterministic')}</td><td><button class="secondary" data-capsule="${safe(item.capsule_id)}">Inspect</button></td></tr>`).join('')}</tbody></table>`;
}

function modelProfiles(items) {
  return items.map(item => `<div class="model-row"><div><strong>${safe(item.model_id)}</strong><small>${safe(item.provider)}</small></div><label class="toggle"><input id="enabled-${safe(item.model_id)}" type="checkbox" ${item.enabled ? 'checked' : ''}> Enabled</label><div><input id="tokens-${safe(item.model_id)}" type="number" min="256" max="16000" value="${item.max_tokens}"><button class="secondary" data-save-model="${safe(item.model_id)}" style="margin-top:5px;width:100%">Save</button></div></div>`).join('');
}

async function inspectCapsule(id) {
  const response = await fetch('/api/capsules/' + encodeURIComponent(id)); const data = await response.json();
  const node = document.querySelector('#capsule-detail');
  if (!response.ok) { node.innerHTML = `<p>${safe(data.error)}</p>`; return; }
  const capsule = data.capsule; const evaluation = capsule.evaluation || {}; const evidence = capsule.selected_evidence || [];
  node.innerHTML = `<div class="eyebrow">${safe(capsule.case.case_id)}</div><h3>${safe(capsule.case.case_title)}</h3><p>${evidence.length} evidence items, ${pct(evaluation.log_compression_ratio)} log reduction, ${pct(evaluation.important_signal_preservation)} signal retained.</p><h3>Strongest evidence</h3><ul class="evidence-list">${evidence.slice(0,5).map(item => `<li><code>${safe(item.evidence_id)}</code> ${safe(item.title)}</li>`).join('')}</ul><div class="actions"><a href="/artifacts/${encodeURIComponent(id)}/capsule.md" target="_blank"><button class="secondary">Open report</button></a><a href="/artifacts/${encodeURIComponent(id)}/dashboard.html" target="_blank"><button class="secondary">Open dashboard</button></a></div>`;
}

async function saveModel(id) {
  const payload = {enabled: document.querySelector('#enabled-' + CSS.escape(id)).checked, max_tokens: Number(document.querySelector('#tokens-' + CSS.escape(id)).value)};
  const response = await fetch('/api/models/' + encodeURIComponent(id), {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  if (!response.ok) { const data = await response.json(); alert(data.error); }
  await refresh();
}

function renderLab(state) {
  const live = state.live || {}; const phases = state.phases; const incident = state.current_incident_id;
  app.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Controlled test environment</div><h1>Incident Lab</h1><p>Run a real local failure and feed its evidence into FCAPSule.</p></div><span>${incident ? status('firing') : status('ready')}</span></div>
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
        <section class="sheet"><div class="sheet-head"><h2>Activity</h2><span class="eyebrow">Newest last</span></div><div class="event-list">${eventList(state.events)}</div></section>
      </div>
    </div>`;
  document.querySelector('#run-simulation').addEventListener('click', runSimulation);
  document.querySelector('#build-capsule').addEventListener('click', buildCapsule);
  document.querySelector('#reset-live').addEventListener('click', resetLive);
}

function phaseStrip(phases) {
  const defs = [['services','Services'],['baseline','Baseline'],['injection','Injection'],['alerts','FM alerts'],['capsule','Capsule'],['models','Models']];
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
  return events.map(item => `<div class="event"><time>${shortTime(item.time * 1000)}</time><b>${safe(item.phase)}</b><span>${safe(item.message)}</span></div>`).join('');
}
async function runSimulation() {
  const payload = {app_name:document.querySelector('#app-name').value,app_id:document.querySelector('#app-id').value,scenario:document.querySelector('#scenario').value,baseline_requests:Number(document.querySelector('#baseline').value),incident_requests:Number(document.querySelector('#incident').value),concurrency:Number(document.querySelector('#concurrency').value)};
  const response = await fetch('/api/simulations', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); if (!response.ok) { alert((await response.json()).error); } await refresh();
}
async function buildCapsule() { const response = await fetch('/api/capsules', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({incident_id:lastState.current_incident_id})}); if (!response.ok) alert((await response.json()).error); await refresh(); }
async function resetLive() { const response = await fetch('/api/reset', {method:'POST'}); if (!response.ok) alert((await response.json()).error); await refresh(); }

async function refresh() {
  try {
    const response = await fetch('/api/state', {cache:'no-store'}); const state = await response.json(); lastState = state; setSystem(state); view === 'lab' ? renderLab(state) : renderConsole(state);
  } catch (error) { document.querySelector('#system-state').textContent = 'Disconnected'; }
}
refresh(); setInterval(refresh, 1000);
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
