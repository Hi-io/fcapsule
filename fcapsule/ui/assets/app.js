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
document.querySelector(`[data-nav="${view}"]`)?.setAttribute('aria-current', 'page');
document.body.dataset.view = view;
let lastState = null;
let selectedReport = null;
let selectedEpisodeId = null;
let showArchived = false;
let reportTab = 'overview';
let reportLoading = false;
let reportError = '';
let requestSequence = 0;
let refreshing = false;
let animateEpisodeId = null;
let lastConsoleSignature = '';
const openDisclosures = new Set();
const closedNamespaces = new Set();

function renderPreservingFocus(render) {
  const focused = document.activeElement;
  const focusId = focused?.id;
  const selection = focused instanceof HTMLInputElement && focused.type === 'text' ? [focused.selectionStart, focused.selectionEnd] : null;
  document.querySelectorAll('details[data-disclosure]').forEach(node => {
    if (node.open) openDisclosures.add(node.dataset.disclosure);
    else openDisclosures.delete(node.dataset.disclosure);
  });
  render();
  document.querySelectorAll('details[data-disclosure]').forEach(node => { node.open = openDisclosures.has(node.dataset.disclosure); });
  if (focusId) {
    const replacement = document.getElementById(focusId);
    replacement?.focus({preventScroll:true});
    if (selection && replacement instanceof HTMLInputElement) replacement.setSelectionRange(...selection);
  }
}
function disclosure(id, title, content, count = '') {
  const domainIcon = {'domain-alerts':'bell-ring','domain-logs':'logs','domain-metrics':'chart-no-axes-combined','domain-config':'file-code-2','domain-coverage':'layers','diagnostics':'settings-2'}[id];
  return `<details class="evidence-disclosure" data-disclosure="${safe(id)}" ${openDisclosures.has(id) ? 'open' : ''}><summary id="disclosure-${safe(id)}"><span class="disclosure-title">${domainIcon ? icon(domainIcon) : ''}<span>${safe(title)}</span></span>${count !== '' ? `<span class="detail-count">${safe(count)}</span>` : ''}</summary><div class="disclosure-body">${content}</div></details>`;
}
function icon(name) { return `<span class="ui-icon" data-icon="${safe(name)}" aria-hidden="true"></span>`; }
function quantity(count, noun) { return `${count} ${noun}${count === 1 ? '' : 's'}`; }
function reportId() { return selectedReport?.incident?.incident_id || selectedReport?.report?.incident?.incident_id; }
function allEpisodes(state = lastState) { return [...state.overview.episodes, ...state.overview.archived_episodes]; }
function updateLocation() {
  const query = selectedEpisodeId ? '?episode=' + encodeURIComponent(selectedEpisodeId) + (reportId() ? '&incident=' + encodeURIComponent(reportId()) : '') : '';
  history.replaceState(null, '', '/console' + query);
}
let requestedReportId = new URLSearchParams(location.search).get('incident');
let requestedEpisodeId = new URLSearchParams(location.search).get('episode');

function setSystem(state) {
  const node = document.querySelector('.system-state');
  const label = document.querySelector('#system-state');
  node.className = 'system-state' + (state.error ? ' error' : state.running ? ' busy' : '');
  label.textContent = state.error ? 'Attention' : state.running ? (state.active_job || 'Working') : 'Ready';
}

function status(value) { return `<span class="status ${safe(value)}">${safe(value)}</span>`; }
function sources(config) {
  const active = value => ['connected','observed','available'].includes(value);
  const names = {faults:'Alerts',metrics:'Metrics',logs:'Logs',configuration:'Config',traces:'Traces'};
  return Object.keys(names).map(key => `<span class="source ${active(config?.[key]?.status) ? 'on' : ''}" title="${safe(names[key] + ': ' + (config?.[key]?.status || 'not configured'))}">${names[key]}</span>`).join('');
}

function consoleSignature(state) {
  return JSON.stringify([state.overview.episodes, state.overview.archived_episodes, selectedReport, selectedEpisodeId, showArchived, reportTab, reportLoading, reportError, state.running]);
}
function renderConsole(state) {
  lastConsoleSignature = consoleSignature(state);
  const data = state.overview;
  const episodes = [...(showArchived ? data.archived_episodes : data.episodes)].sort((a,b) => Number(b.status === 'active') - Number(a.status === 'active') || b.last_activity_at.localeCompare(a.last_activity_at));
  const active = data.episodes.filter(item => item.status === 'active').length;
  app.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Incident workspace</div><h1>Operations</h1><p>${active} active · ${data.episodes.length - active} resolved</p></div><a class="button-link" href="/targets">Manage targets</a></div>
    <section class="queue"><div class="sheet-head"><h2>${showArchived ? 'Archived episodes' : 'Incident queue'}</h2><button class="secondary" id="queue-mode">${showArchived ? 'Back to queue' : `Archived (${data.archived_episodes.length})`}</button></div>
    ${episodeTable(episodes, data.capsules, showArchived)}</section>`;
  document.querySelector('#queue-mode').addEventListener('click', () => {
    selectedReport = null; selectedEpisodeId = null; requestSequence++; showArchived = !showArchived;
    updateLocation(); renderConsole(lastState);
  });
  document.querySelectorAll('[data-episode-toggle]').forEach(summary => summary.addEventListener('click', event => {
    event.preventDefault();
    const episode = episodes.find(item => item.episode_id === summary.dataset.episodeToggle);
    openEpisodeReport(episode.episode_id, episode.primary_incident_id);
  }));
  document.querySelectorAll('[data-signal-select]').forEach(select => select.addEventListener('change', () => openEpisodeReport(selectedEpisodeId, select.value, true)));
  document.querySelectorAll('[data-report-tab]').forEach(button => button.addEventListener('click', () => {
    reportTab = button.dataset.reportTab;
    renderPreservingFocus(() => renderConsole(lastState));
    document.getElementById('tab-' + reportTab)?.focus({preventScroll:true});
  }));
  document.querySelector('[role="tablist"]')?.addEventListener('keydown', event => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const names = ['overview','evidence','timeline'];
    reportTab = event.key === 'Home' ? names[0] : event.key === 'End' ? names[2] : names[(names.indexOf(reportTab) + (event.key === 'ArrowRight' ? 1 : 2)) % 3];
    renderPreservingFocus(() => renderConsole(lastState));
    document.getElementById('tab-' + reportTab)?.focus({preventScroll:true});
  });
  document.querySelectorAll('[data-evidence-link]').forEach(button => button.addEventListener('click', () => {
    reportTab = 'evidence';
    const id = button.dataset.evidenceLink;
    openDisclosures.add('evidence-' + id);
    openDisclosures.add('domain-' + button.dataset.domain);
    renderConsole(lastState);
    const target = document.getElementById('evidence-' + id);
    target?.scrollIntoView({block:'center', behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    target?.focus({preventScroll:true});
  }));
  document.querySelectorAll('[data-build-capsule]').forEach(button => button.addEventListener('click', () => buildCapsule(button.dataset.buildCapsule)));
  document.querySelectorAll('[data-ai-briefing]').forEach(button => button.addEventListener('click', () => generateAiBriefing(button.dataset.aiBriefing)));
  document.querySelectorAll('[data-archive]').forEach(button => button.addEventListener('click', () => changeEpisodeState(button.dataset.archive, 'archive')));
  document.querySelectorAll('[data-restore]').forEach(button => button.addEventListener('click', () => changeEpisodeState(button.dataset.restore, 'restore')));
  document.querySelectorAll('[data-delete]').forEach(button => button.addEventListener('click', () => deleteEpisode(button.dataset.delete)));
  const animated = animateEpisodeId && document.querySelector('.episode.is-open .episode-body');
  if (animated && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    animated.animate([{opacity:0, transform:'translateY(-6px)'},{opacity:1, transform:'translateY(0)'}], {duration:180, easing:'ease-out'});
  }
  animateEpisodeId = null;
}

function renderTargets(state) {
  const config = state.sources.configuration; const targets = state.sources.targets || {};
  const observedApps = state.overview.applications.filter(item => item.status !== 'not_observed');
  const targetCard = (name, label) => {
    const item = targets[name] || {}; const ok = item.ok === true;
    const details = name === 'prometheus' ? [['Healthy targets', `${item.healthy_targets ?? '--'} / ${item.active_targets ?? '--'}`], ['Version', item.version || '--']]
      : name === 'opensearch' ? [['Indexed documents', item.documents != null ? fmt.format(item.documents) : '--'], ['Version', item.version || '--']]
      : [['Authentication', item.authentication || 'ServiceAccount'], ['Version', item.version || '--']];
    return `<article class="target"><h3>${label}<span id="target-status-${name}">${status(item.ok == null ? 'not tested' : ok ? 'healthy' : 'error')}</span></h3>${item.error ? `<p class="target-error">${safe(item.error)}</p>` : `<dl>${details.map(row => `<dt>${safe(row[0])}</dt><dd>${safe(row[1])}</dd>`).join('')}</dl>`}</article>`;
  };
  app.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Source connections</div><h1>Targets</h1><p>Connect the cluster data used for discovery and incident capture.</p></div><div class="actions"><button class="secondary" id="configure-targets">${icon('settings-2')}Configure</button><button class="secondary" id="test-targets">Test connections</button><button id="sync-targets" ${state.running ? 'disabled' : ''}>Sync now</button></div></div>
    <div class="targets-workspace"><section class="target-grid">${targetCard('prometheus','Prometheus')}${targetCard('opensearch','OpenSearch')}${targetCard('kubernetes','Kubernetes API')}</section>
    <details class="connection-settings" data-disclosure="connections" ${openDisclosures.has('connections') || window.targetNotice || !config.prometheus_url ? 'open' : ''}><summary><span class="disclosure-title">${icon('settings-2')}<span>Connection settings</span></span><span class="queue-note">${config.enabled ? 'Automatic polling enabled' : 'Manual synchronization'}</span></summary><section class="sheet"><div class="sheet-body">
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
    </div></section></details>
    <section class="sheet discovery-section"><div class="sheet-head"><h2>Discovery</h2><span class="queue-note" id="discovery-time">${state.sources.last_sync_at ? formatDate(state.sources.last_sync_at) : 'Not synchronized'}</span></div><div class="sheet-body">
      <div class="kpis" style="grid-template-columns:repeat(3,1fr);margin:0"><div class="kpi"><span>Pods visible</span><strong id="pods-visible">${state.sources.pods_visible || 0}</strong></div><div class="kpi"><span>Applications</span><strong id="applications-visible">${state.sources.applications_visible || 0}</strong></div><div class="kpi"><span>Active alerts</span><strong id="active-alerts">${state.sources.active_alerts || 0}</strong></div></div>
      ${state.sources.error ? `<p class="target-error">${safe(state.sources.error)}</p>` : '<p class="queue-note" style="margin-top:12px">Discovery maps Kubernetes pods to Prometheus metrics, OpenSearch logs, and referenced configuration.</p>'}
    </div></section>
    <section class="sheet coverage-section"><div class="sheet-head"><h2>Application coverage</h2><span class="queue-note" id="coverage-count">${observedApps.length} observed applications</span></div><div id="coverage-content">${applicationTable(observedApps)}</div></section></div>`;
  document.querySelector('.targets-workspace').append(document.querySelector('.connection-settings'));
  document.querySelector('#configure-targets').addEventListener('click', () => {
    const settings = document.querySelector('.connection-settings');
    settings.open = true;
    openDisclosures.add('connections');
    settings.scrollIntoView({block:'start', behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    settings.querySelector('summary').focus({preventScroll:true});
  });
  document.querySelector('#save-targets').addEventListener('click', saveTargets);
  document.querySelector('#test-targets').addEventListener('click', testTargets);
  document.querySelector('#sync-targets').addEventListener('click', syncTargets);
}

function renderSettings(state) {
  const ai = state.ai;
  const options = ai.models.map(item => `<option value="${safe(item.model_id)}">${safe(item.model_id)}</option>`).join('');
  const keyState = ai.api_key_configured ? 'Automatic analysis enabled' : 'Provider key required';
  app.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Runtime configuration</div><h1>Settings</h1><p>Manage retention and automatic incident analysis.</p></div></div>
    <div class="settings-layout"><section class="sheet"><div class="sheet-head"><h2>${icon('archive')}Incident lifecycle</h2><span class="queue-note">Automatic cleanup</span></div><div class="sheet-body">
      <div class="field"><label for="retention-days">Incident retention (days)</label><input id="retention-days" type="number" min="1" max="3650" value="${safe(state.settings.incident_retention_days)}"><small>Active and archived incidents older than this are permanently removed with their managed reports and capsules. Default: 30 days.</small></div>
      ${window.generalSettingsNotice ? `<p class="notice">${safe(window.generalSettingsNotice)}</p>` : ''}
      <div class="actions"><button id="save-general-settings">Save retention</button></div>
    </div></section><section class="sheet"><div class="sheet-head"><h2>${icon('activity')}AI briefing</h2><span class="queue-note">${safe(keyState)}</span></div><div class="sheet-body">
      <div class="field"><label for="ai-provider">Provider</label><input id="ai-provider" value="DeepSeek-compatible" disabled></div>
      <div class="field"><label for="ai-model">Model ID</label><input id="ai-model" list="ai-model-options" value="${safe(ai.model)}"><datalist id="ai-model-options">${options}</datalist><small>Configured models are available here; a compatible model ID may also be entered.</small></div>
      <div class="field"><label for="ai-max-tokens">Maximum completion tokens</label><input id="ai-max-tokens" type="number" min="256" max="16000" value="${safe(ai.max_tokens)}"></div>
      <div class="field"><label for="ai-key">API key</label><input id="ai-key" type="password" autocomplete="new-password" placeholder="${ai.api_key_configured ? 'Leave blank to keep the saved key' : 'Paste a key to enable briefings'}"><small>Stored locally. New reports are analysed automatically when a key is configured.</small></div>
      ${window.settingsNotice ? `<p class="notice">${safe(window.settingsNotice)}</p>` : ''}
      <div class="actions"><button id="save-ai-settings">Save AI settings</button></div>
    </div></section></div>`;
  document.querySelector('#save-general-settings').addEventListener('click', saveGeneralSettings);
  document.querySelector('#save-ai-settings').addEventListener('click', saveAiSettings);
}

function applicationTable(items) {
  if (!items.length) return '<div class="empty">No applications are currently observed.</div>';
  const groups = new Map();
  items.forEach(item => {
    const key = item.cluster + ' / ' + item.namespace;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  });
  return [...groups].sort(([a],[b]) => a.localeCompare(b)).map(([key, apps]) => `
    <details class="namespace-group" data-namespace="${safe(key)}" ${closedNamespaces.has(key) ? '' : 'open'}>
    <summary><span class="namespace-heading">${icon('layers')}<span><small>${safe(apps[0].cluster)}</small><strong>${safe(apps[0].namespace)}</strong></span></span><span>${quantity(apps.length, 'app')} · ${quantity(apps.reduce((n,item)=>n+(item.source_config?.pods?.length || 0),0), 'pod')}</span></summary>
    <div class="table-wrap"><table><thead><tr><th>Application</th><th>State</th><th>Pods</th><th>Telemetry</th><th>Latest capture</th></tr></thead><tbody>${apps.map(item => `
    <tr><td><strong>${safe(item.name)}</strong><small class="cell-note">${safe(item.environment)}</small></td><td>${status(item.status)}</td>
    <td>${disclosure('pods-' + item.app_id, (item.source_config?.pods?.length || 0) + ' observed', `<div class="pod-list">${(item.source_config?.pods || []).map(pod=>`<span class="pod-line"><code>${safe(pod.name)}</code>${status(pod.ready ? 'ready' : pod.phase || 'unknown')}</span>`).join('')}</div>`)}</td>
    <td><div class="source-row">${sources(item.source_config)}</div></td><td>${formatDate(item.last_incident_at)}</td></tr>`).join('')}</tbody></table></div></details>`).join('');
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
  window.targetNotice = 'Source synchronization started.'; document.querySelector('#sync-targets').disabled = true; await refresh(); document.querySelector('#sync-targets').disabled = false;
}

function episodeTable(items, capsules, archived = false) {
  if (!items.length) return `<div class="empty">${archived ? 'No archived episodes.' : 'No incidents captured.'}</div>`;
  const apps = new Map(lastState.overview.applications.map(item => [item.app_id, item]));
  return items.map(item => {
    const isOpen = selectedEpisodeId === item.episode_id;
    const application = apps.get(item.app_id);
    const lifecycle = archived ? `<button class="secondary" data-restore="${safe(item.episode_id)}">Restore</button><button class="danger" data-delete="${safe(item.episode_id)}">Delete episode</button>` : `<button class="secondary" data-archive="${safe(item.episode_id)}">Archive</button>`;
    return `<article class="episode ${isOpen ? 'is-open' : ''}">
      <details class="episode-expander" ${isOpen ? 'open' : ''}>
      <summary id="toggle-${safe(item.episode_id)}" data-episode-toggle="${safe(item.episode_id)}" aria-controls="body-${safe(item.episode_id)}" aria-expanded="${isOpen}">
      <span class="episode-summary">
        <span class="episode-state">${status(item.severity)}${status(item.status)}</span>
        <span class="episode-identity"><strong>${safe(item.title)}</strong><small>${safe(application?.name || item.app_id)} · ${safe(application?.namespace || '')}</small></span>
        <span class="episode-count">${item.signal_count} alert${item.signal_count === 1 ? '' : 's'}<small>${item.report_count} report${item.report_count === 1 ? '' : 's'}</small></span>
        <time class="episode-time" datetime="${safe(item.started_at)}">${formatDate(item.started_at)}</time>
      </span></summary>
      <div class="episode-body" id="body-${safe(item.episode_id)}">
        ${isOpen ? `
        <div class="investigation-toolbar">${episodeContext(item)}<div class="row-actions">${lifecycle}</div></div>
        ${reportLoading ? '<div class="analysis-state" role="status"><span class="spinner"></span>Opening retained evidence…</div>' : reportError ? `<p class="notice" role="alert">${safe(reportError)}</p>` : selectedReport ? reportPanel(selectedReport) : ''}
        <footer class="episode-footer"><span>${safe(application?.cluster || '')} / ${safe(application?.namespace || '')} · ${item.status === 'resolved' ? 'Resolved ' + formatDate(item.ended_at) : 'Last alert ' + formatDate(item.last_activity_at)}</span><span>Grouped by workload and time</span></footer>
        ` : ''}
      </div></details></article>`;
  }).join('');
}

function episodeContext(episode) {
  const id = reportId() || episode.primary_incident_id;
  if (episode.signals.length === 1) return '<span class="queue-note">Investigation</span>';
  return `<div class="signal-selector"><label for="signal-report">Alert report</label><select id="signal-report" data-signal-select>${episode.signals.map((signal, index) => `<option value="${safe(signal.incident_id)}" ${id === signal.incident_id ? 'selected' : ''}>${index + 1}. ${safe(signal.summary || signal.scenario)} · ${shortTime(signal.started_at)}</option>`).join('')}</select></div>`;
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '--';
}

async function openEpisodeReport(episodeId, incidentId, keepOpen = false) {
  const sequence = ++requestSequence;
  if (selectedEpisodeId === episodeId && !keepOpen) {
    const body = document.querySelector('.episode.is-open .episode-body');
    if (body && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      await body.animate([{height:body.offsetHeight + 'px',opacity:1},{height:'0px',opacity:0}], {duration:150,easing:'ease-in',fill:'forwards'}).finished;
    }
    if (sequence !== requestSequence) return;
    selectedReport = null; selectedEpisodeId = null; reportLoading = false;
    updateLocation(); renderConsole(lastState);
    document.getElementById('toggle-' + episodeId)?.focus({preventScroll:true});
    return;
  }
  selectedEpisodeId = episodeId; selectedReport = null; reportLoading = true; reportError = '';
  if (!keepOpen) { reportTab = 'overview'; openDisclosures.clear(); animateEpisodeId = episodeId; }
  renderConsole(lastState);
  try {
    const response = await fetch('/api/incidents/' + encodeURIComponent(incidentId) + '/report');
    const data = await response.json();
    if (sequence !== requestSequence) return;
    if (!response.ok) throw new Error(data.error || 'Unable to load report');
    selectedReport = data;
  } catch (error) {
    if (sequence !== requestSequence) return;
    reportError = error.message;
  } finally {
    if (sequence === requestSequence) {
      reportLoading = false; updateLocation(); renderPreservingFocus(() => renderConsole(lastState));
      document.getElementById(keepOpen ? 'signal-report' : 'toggle-' + episodeId)?.focus({preventScroll:true});
    }
  }
}

async function generateAiBriefing(id) {
  try {
    const response = await fetch('/api/incidents/' + encodeURIComponent(id) + '/briefing', {method:'POST'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Unable to start analysis');
    if (reportId() === id) { selectedReport.ai_briefing = result; renderPreservingFocus(() => renderConsole(lastState)); }
  } catch (error) {
    if (reportId() === id) { selectedReport.ai_briefing = {status:'unavailable', message:error.message}; renderConsole(lastState); }
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

async function changeEpisodeState(id, action) {
  const response = await fetch(`/api/episodes/${encodeURIComponent(id)}/${action}`, {method:'POST'});
  const result = await response.json();
  if (!response.ok) { alert(result.error || `Unable to ${action} episode`); return; }
  selectedReport = null; selectedEpisodeId = null; requestSequence++; updateLocation(); await refresh();
}

async function deleteEpisode(id) {
  if (!confirm('Permanently delete this episode and every related report and capsule?')) return;
  const response = await fetch(`/api/episodes/${encodeURIComponent(id)}`, {method:'DELETE'});
  const result = await response.json();
  if (!response.ok) { alert(result.error || 'Unable to delete episode'); return; }
  selectedReport = null; selectedEpisodeId = null; await refresh();
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
  const peakIndex = numbers.indexOf(signal.metric === 'pod_ready' ? Math.min(...numbers) : Math.max(...numbers));
  const peakX = (peakIndex / (values.length - 1) * (width - pad * 2) + pad).toFixed(1);
  return `<svg class="spark" viewBox="0 0 ${width} ${height}" role="img" aria-label="${safe(signal.label)} across the incident window"><line class="axis" x1="0" y1="${height - pad}" x2="${width}" y2="${height - pad}"/><line class="baseline" x1="0" y1="${baselineY}" x2="${width}" y2="${baselineY}"/><line class="incident" x1="${peakX}" y1="0" x2="${peakX}" y2="${height}"/><path class="series" d="${path}"/></svg>`;
}

function evidenceReferences(report, ids) {
  const groups = [
    ['alerts', report.fault_alerts || [], item=>item.name],
    ['metrics', report.pm_signals || [], item=>item.label],
    ['logs', report.log_patterns || [], item=>logLabel(item.pattern)],
    ['config', report.configuration_evidence || [], item=>item.name],
  ];
  return ids.map(id => {
    for (const [domain, items, label] of groups) {
      const item = items.find(entry=>entry.evidence_id === id);
      if (item) return '<button class="evidence-link" data-evidence-link="' + safe(id) + '" data-domain="' + domain + '">' + safe(label(item)) + '</button>';
    }
    return '<span class="queue-note">' + safe(id) + '</span>';
  }).join('');
}

function logLabel(pattern) {
  const caption = (message, level) => level ? `${level}: ${message}` : message;
  try { const parsed = JSON.parse(pattern); return caption(parsed.message || parsed.msg || pattern, parsed.level); } catch (_) {
    // Numeric placeholders can invalidate JSON without changing its quoted message.
    const message = pattern.match(/"(?:message|msg)"\s*:\s*("(?:\\.|[^"\\])*")/);
    if (message) {
      const level = pattern.match(/"level"\s*:\s*("(?:\\.|[^"\\])*")/);
      try { return caption(JSON.parse(message[1]), level ? JSON.parse(level[1]) : ''); } catch (_) { /* Keep the original pattern below. */ }
    }
    return pattern.length > 140 ? pattern.slice(0, 137) + '...' : pattern;
  }
}
function briefingPanel(payload) {
  const ai = payload.ai_briefing;
  const report = payload.report;
  if (ai?.status === 'ready') {
    const brief = ai.briefing;
    return '<section class="briefing"><div class="section-heading"><h3>AI assessment</h3><span class="queue-note">' + safe(ai.model) + '</span></div>' +
      '<p class="brief-lead">' + safe(brief.operator_brief) + '</p>' +
      (brief.likely_mechanism ? '<h4>Likely explanation</h4><p>' + safe(brief.likely_mechanism) + '</p>' : '') +
      '<div class="next-check"><h4>Check first</h4><p><strong>' + safe(brief.first_action) + '</strong></p><p>' + safe(brief.why_this_first) + '</p>' +
      (brief.expected_finding ? '<p><span class="text-label">What to look for</span>' + safe(brief.expected_finding) + '</p>' : '') + '</div>' +
      (brief.mitigation ? '<h4>Possible mitigation</h4><p>' + safe(brief.mitigation) + '</p>' : '') +
      '<p class="uncertainty"><strong>Still unconfirmed:</strong> ' + safe(brief.uncertainty) + '</p>' +
      '<div class="citations"><span>Evidence</span>' + evidenceReferences(report, brief.evidence_ids || []) + '</div></section>';
  }
  const loading = !ai || ['queued','running'].includes(ai.status);
  return '<section class="briefing"><h3>AI assessment</h3><div class="analysis-state" role="status" aria-live="polite">' +
    (loading ? '<span class="spinner"></span><div><strong>' + (ai?.status === 'queued' ? 'Analysis queued' : 'Analysing retained evidence') + '</strong><p>The assessment will appear here. Evidence is available now.</p></div>' :
      '<div><strong>' + (ai.status === 'not_configured' ? 'Automatic analysis is not configured' : 'Analysis unavailable') + '</strong><p>' + safe(ai.message) + '</p>' +
      (ai.status === 'not_configured' ? '<a href="/settings">Open Settings</a>' : '<button class="secondary" data-ai-briefing="' + safe(report.incident.incident_id) + '">Retry analysis</button>') + '</div>') +
    '</div></section>';
}
function metricMatters(item) {
  if (item.label === 'Pod readiness') return item.value !== 'Ready';
  if (item.label === 'Container restart activity') return Number(item.value) > 0;
  return Math.abs(Number(item.change_percent)) >= 15 && item.value !== item.baseline;
}
function impactRows(items) {
  return '<dl class="impact-list">' + items.map(item => '<div><dt>' + safe(item.label) + '<small>' + safe(item.meaning) + '</small></dt><dd>' + safe(item.value) + '<small>' + safe(item.baseline_text) + '</small></dd></div>').join('') + '</dl>';
}
function metricsPanel(report) {
  return '<div class="metrics-grid">' + (report.pm_signals || []).map(item => {
    const points = item.values || [];
    const start = points[0]?.timestamp; const end = points[points.length - 1]?.timestamp;
    const duration = start && end ? Math.round((new Date(end) - new Date(start)) / 60000) : 0;
    return '<article class="metric-chart" id="evidence-' + safe(item.evidence_id) + '" tabindex="-1"><h4>' + safe(item.label) + '</h4><p class="queue-note">' + safe(item.meaning) + '</p>' +
      '<div class="pm-values"><span>Typical<b>' + safe(item.baseline) + '</b></span><span>' + (item.metric === 'pod_ready' ? 'Lowest readiness' : item.metric.endsWith('_total') ? 'Window increase' : 'Peak') + '<b>' + safe(item.peak) + '</b></span></div>' +
      sparkline(item) + '<div class="chart-times"><time>' + shortTime(start) + '</time><span>' + duration + ' min captured</span><time>' + shortTime(end) + '</time></div><small class="queue-note">' + safe(item.component) + '</small></article>';
  }).join('') + '</div>' + (report.pm_coverage_note ? '<p class="queue-note">' + safe(report.pm_coverage_note) + '</p>' : '');
}
function evidencePanel(report) {
  const alertHtml = (report.fault_alerts || []).map(item => '<article class="evidence-record" id="evidence-' + safe(item.evidence_id) + '" tabindex="-1"><div class="section-heading"><h4>' + safe(item.name) + '</h4>' + status(item.severity) + '</div><p>' + safe(item.description) + '</p><small>' + formatDate(item.timestamp) + '</small>' +
    (item.rule?.query ? disclosure('rule-' + item.evidence_id, 'Alert condition', '<pre class="log-lines">' + safe(item.rule.query) + '</pre><p class="queue-note">Must hold for ' + safe(item.rule.duration || 0) + ' seconds</p>') : '') + '</article>').join('');
  const logs = (report.log_patterns || []).map(item => '<div id="evidence-' + safe(item.evidence_id) + '" tabindex="-1">' +
    disclosure('evidence-' + item.evidence_id, logLabel(item.pattern),
      '<p class="queue-note">' + safe(item.summary) + ' · ' + formatDate(item.first_seen) + ' to ' + formatDate(item.last_seen) + '</p>' +
      '<pre class="log-lines">' + safe((item.examples?.length ? item.examples : [item.pattern]).join('\n')) + '</pre><p class="queue-note">Retained, anonymized examples · ' + safe(item.evidence_id) + '</p>') + '</div>').join('');
  const config = (report.configuration_evidence || []).map(item => {
    const supporting = (report.supporting_evidence || []).find(entry=>entry.evidence_id === item.evidence_id);
    return '<article class="evidence-record" id="evidence-' + safe(item.evidence_id) + '" tabindex="-1"><h4>' + safe(item.kind) + ' · ' + safe(item.name) + '</h4><p>' + safe(item.summary) + '</p>' +
      (item.images?.length ? '<p>Images: <code>' + safe(item.images.join(', ')) + '</code></p>' : '') +
      (item.keys?.length ? '<p class="queue-note">Keys: ' + safe(item.keys.join(', ')) + '</p>' : '') +
      (supporting?.configuration?.data ? '<pre class="log-lines">' + safe(JSON.stringify(supporting.configuration.data, null, 2)) + '</pre>' : '') + '</article>';
  }).join('');
  const coverage = '<dl class="coverage-details">' + (report.coverage || []).map(item=>'<div><dt>' + safe(item.domain) + '</dt><dd>' + safe(item.detail) + '</dd></div>').join('') + '</dl><p class="queue-note">' + safe(report.retention.message) + '</p>';
  return '<div class="evidence-view">' +
    disclosure('domain-alerts', 'Alerts', alertHtml || '<p>No fault records retained.</p>', report.fault_alerts?.length || 0) +
    disclosure('domain-logs', 'Log evidence', logs || '<p>No log patterns retained.</p>', report.log_patterns?.length || 0) +
    disclosure('domain-metrics', 'Performance', metricsPanel(report), report.pm_signals?.length || 0) +
    disclosure('domain-config', 'Configuration', config || '<p>No configuration snapshot retained.</p>', report.configuration_evidence?.length || 0) +
    disclosure('domain-coverage', 'Coverage and trace availability', coverage) + '</div>';
}
function timelinePanel(report) {
  const episode = allEpisodes().find(item=>item.episode_id === selectedEpisodeId);
  const signals = (episode?.signals || []).map(signal=>'<li><time>' + formatDate(signal.started_at) + '</time><div><strong>' + safe(signal.summary || signal.scenario) + '</strong><small>' + safe(signal.severity) + ' · ' + safe(signal.status) + (signal.ended_at && signal.status === 'resolved' ? ' · resolved ' + formatDate(signal.ended_at) : '') + '</small></div></li>').join('');
  return '<section class="timeline-view"><h3>Episode alerts</h3><ol class="event-timeline">' + signals + '</ol>' +
    disclosure('retained-sequence', 'Captured evidence sequence', '<ol class="event-timeline">' + (report.timeline || []).map(item=>'<li><time>' + formatDate(item.timestamp) + '</time><div><strong>' + safe(item.title) + '</strong><small>' + safe(item.description) + '</small></div></li>').join('') + '</ol>') +
    (report.topology?.length ? '<p class="queue-note">Dependencies: ' + report.topology.map(item=>safe(item.from) + ' → ' + safe(item.to)).join(' · ') + '</p>' : '') + '</section>';
}
function reportPanel(payload) {
  if (!payload.report) return '<section class="report-empty"><h3>Evidence captured</h3><p>The report is ' + (lastState?.running ? 'being prepared.' : 'not built yet.') + '</p><button data-build-capsule="' + safe(payload.incident.incident_id) + '" ' + (lastState?.running ? 'disabled' : '') + '>Build report</button></section>';
  const report = payload.report;
  const incident = report.incident;
  const capsule = payload.record;
  const relevant = (report.impact || []).filter(metricMatters);
  const others = (report.impact || []).filter(item=>!relevant.includes(item));
  const impact = relevant.length ? impactRows(relevant.slice(0,3)) : '<p class="queue-note">No material change was established in the captured impact metrics.</p>';
  const secondaryImpact = [...relevant.slice(3), ...others];
  const exports = capsule ? '<details class="export-menu" data-disclosure="exports"><summary>Export</summary><div><a class="button-link" href="/artifacts/' + encodeURIComponent(capsule.capsule_id) + '/incident_report.json" download>Report JSON</a><a class="button-link" href="/artifacts/' + encodeURIComponent(capsule.capsule_id) + '/' + encodeURIComponent('fcapsule_' + incident.incident_id + '.zip') + '" download>Capsule archive</a></div></details>' : '';
  const ai = payload.ai_briefing;
  const fallback = ai?.status !== 'ready' ? '<section class="observed-summary"><h3>Observed</h3><p>' + safe(report.fault_alerts?.[0]?.description || incident.summary) + '</p></section>' : '';
  const d = report.engineering_diagnostics;
  const diagnostics = disclosure('diagnostics', 'Engineering diagnostics', '<dl class="coverage-details"><div><dt>Selected evidence</dt><dd>' + d.selected_evidence + '</dd></div><div><dt>Log reduction</dt><dd>' + pct(d.log_compression_ratio) + '</dd></div><div><dt>Signal preservation</dt><dd>' + pct(d.important_signal_preservation) + '</dd></div><div><dt>Grounding</dt><dd>' + pct(d.hypothesis_grounding_score) + '</dd></div><div><dt>Runtime</dt><dd>' + Number(d.runtime_seconds || 0).toFixed(2) + 's</dd></div></dl><p class="queue-note">Rule-based hypothesis: ' + safe(report.primary_hypothesis.statement) + '</p>');
  const tabs = ['overview','evidence','timeline'];
  let content;
  if (reportTab === 'evidence') content = evidencePanel(report);
  else if (reportTab === 'timeline') content = timelinePanel(report);
  else content = '<div class="overview-layout"><div>' + briefingPanel(payload) + fallback + '</div><aside><h3>Observed impact</h3>' + impact +
    (secondaryImpact.length ? disclosure('other-impact','Other captured metrics',impactRows(secondaryImpact),secondaryImpact.length) : '') +
    (report.retention.trace_available && report.retention.source_retention_seconds ? '<p class="retention urgent">' + safe(report.retention.message) + '</p>' : '') +
    '<p class="queue-note impact-scope">Values describe the captured window, not current workload health.</p></aside></div>';
  return '<section id="incident-report"><div class="report-navigation"><div role="tablist" aria-label="Investigation views">' +
    tabs.map(name=>'<button id="tab-' + name + '" role="tab" data-report-tab="' + name + '" aria-selected="' + (name === reportTab) + '" aria-controls="investigation-panel" tabindex="' + (name === reportTab ? 0 : -1) + '">' + name[0].toUpperCase() + name.slice(1) + '</button>').join('') +
    '</div>' + exports + '</div><div id="investigation-panel" role="tabpanel" aria-labelledby="tab-' + reportTab + '" tabindex="0">' + content + '</div><div class="report-technical">' + diagnostics + '</div></section>';
}

async function refresh() {
  if (refreshing || document.hidden) return;
  refreshing = true;
  try {
    const response = await fetch('/api/state', {cache:'no-store'});
    if (!response.ok) throw new Error('State unavailable');
    const state = await response.json();
    const previous = lastState;
    lastState = state; setSystem(state);
    if (view === 'console') {
      if (requestedEpisodeId || requestedReportId) {
        const episode = allEpisodes(state).find(item => item.episode_id === requestedEpisodeId || item.signals.some(signal=>signal.incident_id === requestedReportId));
        const signal = requestedReportId || episode?.primary_incident_id;
        requestedEpisodeId = null; requestedReportId = null;
        if (episode) {
          showArchived = Boolean(episode.archived_at);
          await openEpisodeReport(episode.episode_id, signal);
        }
      }
      if (selectedEpisodeId && !allEpisodes(state).some(item=>item.episode_id === selectedEpisodeId)) {
        selectedEpisodeId = null; selectedReport = null; updateLocation();
      }
      const id = reportId(); const sequence = requestSequence;
      if (id && !reportLoading) {
        const reportResponse = await fetch('/api/incidents/' + encodeURIComponent(id) + '/report', {cache:'no-store'});
        if (reportResponse.ok) {
          const payload = await reportResponse.json();
          if (sequence === requestSequence && id === reportId()) selectedReport = payload;
        }
      }
      if (!reportLoading && consoleSignature(state) !== lastConsoleSignature) renderPreservingFocus(() => renderConsole(state));
    } else if (!previous) {
      view === 'targets' ? renderTargets(state) : renderSettings(state);
    } else if (view === 'targets') {
      // Refresh inventory without replacing editable connection settings.
      document.querySelector('#discovery-time').textContent = formatDate(state.sources.last_sync_at);
      for (const key of ['pods_visible','applications_visible','active_alerts']) document.getElementById(key.replaceAll('_','-')).textContent = state.sources[key] || 0;
      document.querySelector('#sync-targets').disabled = state.running;
      for (const name of ['prometheus','opensearch','kubernetes']) {
        const target = state.sources.targets?.[name];
        document.getElementById('target-status-' + name).innerHTML = status(target?.ok == null ? 'not tested' : target.ok ? 'healthy' : 'error');
      }
      const coverage = document.querySelector('#coverage-content');
      if (coverage) {
        document.querySelectorAll('[data-namespace]').forEach(node => {
          if (node.open) closedNamespaces.delete(node.dataset.namespace);
          else closedNamespaces.add(node.dataset.namespace);
        });
        if (!coverage.contains(document.activeElement)) {
          const observed = state.overview.applications.filter(item=>item.status !== 'not_observed');
          coverage.querySelectorAll('[data-disclosure]').forEach(node => {
            if (node.open) openDisclosures.add(node.dataset.disclosure);
            else openDisclosures.delete(node.dataset.disclosure);
          });
          coverage.innerHTML = applicationTable(observed);
          document.querySelector('#coverage-count').textContent = observed.length + ' observed applications';
        }
      }
    }
  } catch (_) {
    document.querySelector('#system-state').textContent = 'Disconnected';
  } finally { refreshing = false; }
}
refresh();
setInterval(refresh, 4000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
