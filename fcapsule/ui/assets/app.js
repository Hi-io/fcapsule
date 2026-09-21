const view = location.pathname.startsWith('/settings') ? 'settings' : location.pathname.startsWith('/targets') ? 'targets' : location.pathname.startsWith('/patterns') ? 'patterns' : 'console';
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
document.title = ({console:'Operations', targets:'Targets', patterns:'Patterns', settings:'Settings'})[view] + ' | FCAPSule';
let lastState = null;
let selectedReport = null;
let selectedEpisodeId = null;
let showArchived;
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
  const params = new URLSearchParams();
  if (selectedEpisodeId) params.set('episode', selectedEpisodeId);
  if (reportId()) params.set('incident', reportId());
  if (showArchived) params.set('archived', '1');
  Object.entries(queueFilters).forEach(([key, value]) => { if (value) params.set(key, value); });
  history.replaceState(null, '', '/console' + (params.size ? '?' + params.toString() : ''));
}
let requestedReportId = new URLSearchParams(location.search).get('incident');
let requestedEpisodeId = new URLSearchParams(location.search).get('episode');
const initialParams = new URLSearchParams(location.search);
showArchived = initialParams.get('archived') === '1';
const queueFilters = {
  namespace: initialParams.get('namespace') || '',
  scope: initialParams.get('scope') || '',
  status: initialParams.get('status') || '',
  period: initialParams.get('period') || '',
  query: initialParams.get('query') || '',
};

function setSystem(state) {
  const node = document.querySelector('.system-state');
  const label = document.querySelector('#system-state');
  node.className = 'system-state' + (state.error ? ' error' : state.running ? ' busy' : '');
  label.textContent = state.error ? 'Attention' : state.running ? (state.active_job || 'Working') : 'Ready';
}

function status(value) { return `<span class="status ${safe(value)}">${safe(value)}</span>`; }
function capability(capability) {
  const item = capability || {status:'not_configured'};
  const labels = {ready:'Ready',not_configured:'Not configured',not_validated:'Needs validation',validating:'Validating',invalid_credentials:'Credential rejected',insufficient_credit:'Credit unavailable',unsupported_model:'Unsupported model',temporarily_unavailable:'Temporarily unavailable'};
  return `<span class="capability ${safe(item.status)}" title="${safe(item.message || '')}">${safe(labels[item.status] || item.status)}</span>`;
}
function sources(config) {
  const active = value => ['connected','observed','available'].includes(value);
  const names = {faults:'Alerts',metrics:'Metrics',logs:'Logs',configuration:'Config',traces:'Traces'};
  return Object.keys(names).map(key => `<span class="source ${active(config?.[key]?.status) ? 'on' : ''}" title="${safe(names[key] + ': ' + (config?.[key]?.status || 'not configured'))}">${names[key]}</span>`).join('');
}

function consoleSignature(state) {
  return JSON.stringify([state.overview.episodes, state.overview.archived_episodes, state.overview.triage, state.overview.related_groups, selectedReport, selectedEpisodeId, showArchived, queueFilters, reportTab, reportLoading, reportError, state.running]);
}
function resourceLabel(item, application) {
  const resource = item.resource || {};
  if (resource.kind === 'node' && resource.name) return 'Node ' + resource.name;
  if (resource.kind === 'pod' && resource.name) return 'Pod ' + resource.name;
  if (resource.kind === 'workload' && resource.name) return 'Workload ' + resource.name;
  return application?.name || resource.name || item.app_id;
}
function recurrenceLabel(recurrence) {
  const count = Number(recurrence?.previous_count || 0);
  return count ? `Repeated · ${count} prior ${count === 1 ? 'episode' : 'episodes'}` : '';
}
function intervalLabel(seconds) {
  if (!Number.isFinite(Number(seconds)) || Number(seconds) <= 0) return '';
  const value = Number(seconds);
  const [amount, unit] = value >= 86400 ? [Math.round(value / 86400), 'day'] : value >= 3600 ? [Math.round(value / 3600), 'hour'] : [Math.round(value / 60), 'minute'];
  return `about every ${amount} ${unit}${amount === 1 ? '' : 's'}`;
}
function filteredEpisodes(items, applications) {
  const cutoff = {day:86400000, week:604800000, month:2592000000}[queueFilters.period];
  const now = Date.now();
  const needle = queueFilters.query.trim().toLowerCase();
  return items.filter(item => {
    const application = applications.get(item.app_id);
    const target = resourceLabel(item, application);
    if (queueFilters.namespace && application?.namespace !== queueFilters.namespace) return false;
    if (queueFilters.scope && target !== queueFilters.scope) return false;
    if (queueFilters.status && item.status !== queueFilters.status) return false;
    if (cutoff && now - new Date(item.started_at).getTime() > cutoff) return false;
    if (!needle) return true;
    const references = (item.signals || []).flatMap(signal => [signal.reference, signal.incident_id]);
    return [item.reference, item.episode_id, item.primary_incident_id, ...references, item.title, target, application?.name, application?.namespace, item.status]
      .some(value => String(value || '').toLowerCase().includes(needle));
  });
}
function queueFiltersPanel(items, applications) {
  const namespaces = [...new Set(items.map(item => applications.get(item.app_id)?.namespace).filter(Boolean))].sort();
  const scopes = [...new Set(items.map(item => resourceLabel(item, applications.get(item.app_id))).filter(Boolean))].sort();
  const option = (value, label, selected) => `<option value="${safe(value)}" ${value === selected ? 'selected' : ''}>${safe(label)}</option>`;
  return `<form class="queue-filters" aria-label="Filter incident queue">
    <label><span>Namespace</span><select data-queue-filter="namespace">${option('', 'All namespaces', queueFilters.namespace)}${namespaces.map(value => option(value, value, queueFilters.namespace)).join('')}</select></label>
    <label><span>Target</span><select data-queue-filter="scope">${option('', 'All targets', queueFilters.scope)}${scopes.map(value => option(value, value, queueFilters.scope)).join('')}</select></label>
    <label><span>Status</span><select data-queue-filter="status">${option('', 'Any status', queueFilters.status)}${option('active', 'Active', queueFilters.status)}${option('resolved', 'Resolved', queueFilters.status)}</select></label>
    <label><span>Time</span><select data-queue-filter="period">${option('', 'All time', queueFilters.period)}${option('day', 'Last 24 hours', queueFilters.period)}${option('week', 'Last 7 days', queueFilters.period)}${option('month', 'Last 30 days', queueFilters.period)}</select></label>
    <label class="queue-search"><span>Search</span><input data-queue-filter="query" value="${safe(queueFilters.query)}" placeholder="ID or text"></label>
    <button class="secondary filter-clear" type="button" data-clear-filters ${Object.values(queueFilters).some(Boolean) ? '' : 'disabled'}>Clear</button>
  </form>`;
}
function triageStrip(items) {
  if (!items.length || showArchived) return '';
  return `<section class="triage" aria-label="Needs attention"><div class="triage-head"><h2>Needs attention</h2><span>Focused from retained incident history</span></div><div class="triage-list">${items.map(item => `<button class="triage-item" data-triage-episode="${safe(item.episode_id)}"><span class="triage-kind ${safe(item.kind)}">${safe(item.label)}</span><span><strong>${safe(item.title)}</strong><small>${safe(item.detail)}</small></span><span class="triage-open">View</span></button>`).join('')}</div></section>`;
}
function relatedActivity(groups) {
  if (!groups.length || showArchived) return '';
  const basisText = basis => (basis || []).filter(item => item.kind !== 'time_window').slice(0,2).map(item => {
    const label = item.kind === 'same_node' ? 'Shared node' : item.kind === 'shared_dependency' ? 'Shared dependency' : 'Shared alert';
    return label + ': ' + item.value;
  }).join(' · ');
  return `<section class="related-activity" aria-label="Potential shared conditions"><div class="related-activity-head"><div><h2>Potential shared conditions</h2><p>Separate episodes are linked only when their retained alert and operating context agree.</p></div><span>${groups.length} cue${groups.length === 1 ? '' : 's'}</span></div><div class="related-group-list">${groups.slice(0,3).map(group => `<article class="related-group"><div class="related-group-main"><span class="related-reference">${safe(group.reference)}</span><strong>${safe(group.title)}</strong><small>${safe(basisText(group.basis))}</small></div><div class="related-group-episodes">${(group.episodes || []).map(episode => `<span><button class="related-open" data-related-open="${safe(episode.episode_id)}">${safe(episode.reference)}</button><small>${safe(episode.title)}</small><button class="related-separate" data-related-group="${safe(group.group_id)}" data-related-separate="${safe(episode.episode_id)}" title="Keep this episode separate">Separate</button></span>`).join('')}</div></article>`).join('')}</div></section>`;
}
function renderConsole(state) {
  lastConsoleSignature = consoleSignature(state);
  const data = state.overview;
  const allQueueEpisodes = [...(showArchived ? data.archived_episodes : data.episodes)];
  const apps = new Map(data.applications.map(item => [item.app_id, item]));
  const episodes = filteredEpisodes(allQueueEpisodes, apps).sort((a,b) => Number(b.status === 'active') - Number(a.status === 'active') || b.last_activity_at.localeCompare(a.last_activity_at));
  const active = data.episodes.filter(item => item.status === 'active').length;
  app.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Incident workspace</div><h1>Operations</h1><p>${active} active · ${data.episodes.length - active} resolved</p></div><a class="button-link" href="/targets">Manage targets</a></div>
    ${triageStrip(data.triage || [])}
    ${relatedActivity(data.related_groups || [])}
    <section class="queue"><div class="sheet-head"><h2>${showArchived ? 'Archived episodes' : 'Incident queue'}</h2><button class="secondary" id="queue-mode">${showArchived ? 'Back to queue' : `Archived (${data.archived_episodes.length})`}</button></div>
    ${queueFiltersPanel(allQueueEpisodes, apps)}
    ${episodeTable(episodes, data.capsules, showArchived)}</section>`;
  document.querySelector('#queue-mode').addEventListener('click', () => {
    selectedReport = null; selectedEpisodeId = null; requestSequence++; showArchived = !showArchived;
    updateLocation(); renderConsole(lastState);
  });
  document.querySelectorAll('[data-queue-filter]').forEach(field => field.addEventListener(field.tagName === 'INPUT' ? 'input' : 'change', () => {
    queueFilters[field.dataset.queueFilter] = field.value; selectedReport = null; selectedEpisodeId = null; requestSequence++;
    updateLocation(); renderPreservingFocus(() => renderConsole(lastState));
  }));
  document.querySelector('[data-clear-filters]')?.addEventListener('click', () => {
    Object.keys(queueFilters).forEach(key => { queueFilters[key] = ''; });
    selectedReport = null; selectedEpisodeId = null; requestSequence++; updateLocation(); renderConsole(lastState);
  });
  document.querySelectorAll('[data-triage-episode]').forEach(button => button.addEventListener('click', () => {
    const episode = data.episodes.find(item => item.episode_id === button.dataset.triageEpisode);
    if (episode) openEpisodeReport(episode.episode_id, episode.primary_incident_id);
  }));
  document.querySelectorAll('[data-related-open]').forEach(button => button.addEventListener('click', () => {
    const episode = data.episodes.find(item => item.episode_id === button.dataset.relatedOpen);
    if (episode) openEpisodeReport(episode.episode_id, episode.primary_incident_id);
  }));
  document.querySelectorAll('[data-related-separate]').forEach(button => button.addEventListener('click', () => {
    separateRelatedEpisode(button.dataset.relatedGroup, button.dataset.relatedSeparate);
  }));
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
  document.querySelectorAll('[data-investigate]').forEach(button => button.addEventListener('click', () => startInvestigation(button.dataset.investigate)));
  document.querySelectorAll('[data-add-evidence]').forEach(button => button.addEventListener('click', () => showEvidenceDialog(button.dataset.addEvidence)));
  document.querySelectorAll('[data-update-evidence-investigation]').forEach(button => button.addEventListener('click', () => updateWithEvidence(button.dataset.updateEvidenceInvestigation)));
  document.querySelectorAll('[data-correct-evidence]').forEach(button => button.addEventListener('click', () => correctEvidence(button.dataset.correctEvidence)));
  document.querySelectorAll('[data-source-review]').forEach(button => button.addEventListener('click', () => askSourceReview(button.dataset.sourceReview)));
  document.querySelectorAll('[data-copy-incident-link]').forEach(button => button.addEventListener('click', () => copyIncidentLink(button)));
  document.querySelectorAll('[data-investigation-ref]').forEach(button => button.addEventListener('click', () => {
    reportTab = 'evidence';
    const id = button.dataset.investigationRef;
    openDisclosures.add('agent-evidence'); openDisclosures.add('agent-' + id);
    renderConsole(lastState);
    const target = document.getElementById('disclosure-agent-' + id);
    target?.scrollIntoView({block:'center', behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    target?.focus({preventScroll:true});
  }));
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
    <p id="target-notice" class="notice" role="status" ${window.targetNotice ? '' : 'hidden'}>${safe(window.targetNotice || '')}</p>
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
  const media = state.media || {provider:'openrouter',api_key_configured:false,vision:{model:'',capability:{}},audio:{model:'',capability:{}}};
  const options = ai.models.map(item => `<option value="${safe(item.model_id)}">${safe(item.model_id)}</option>`).join('');
  const keyState = capability(ai.capability);
  app.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Runtime configuration</div><h1>Settings</h1><p>Retention, investigation limits, and optional evidence models.</p></div></div>
    <div class="settings-layout"><section class="sheet"><div class="sheet-head"><h2>${icon('archive')}Incident lifecycle</h2><span class="queue-note">Automatic cleanup</span></div><div class="sheet-body">
      <div class="field"><label for="retention-days">Incident retention (days)</label><input id="retention-days" type="number" min="1" max="3650" value="${safe(state.settings.incident_retention_days)}"><small>Active and archived incidents older than this are permanently removed with their managed reports and capsules. Default: 30 days.</small></div>
      ${window.generalSettingsNotice ? `<p class="notice">${safe(window.generalSettingsNotice)}</p>` : ''}
      <div class="actions"><button id="save-general-settings">Save retention</button></div>
    </div></section><section class="sheet"><div class="sheet-head"><h2>${icon('activity')}Episode investigation</h2>${keyState}</div><div class="sheet-body">
      <div class="field"><label for="ai-provider">Provider</label><input id="ai-provider" value="DeepSeek-compatible" disabled></div>
      <div class="field"><label for="ai-model">Model ID</label><input id="ai-model" list="ai-model-options" value="${safe(ai.model)}"><datalist id="ai-model-options">${options}</datalist><small>Configured models are available here; a compatible model ID may also be entered.</small></div>
      <div class="settings-number-grid"><div class="field"><label for="ai-max-tokens">Output per call</label><input id="ai-max-tokens" type="number" min="256" max="6000" value="${safe(ai.max_tokens)}"></div><div class="field"><label for="ai-total-tokens">Investigation budget</label><input id="ai-total-tokens" type="number" min="4000" max="100000" value="${safe(ai.max_total_tokens)}"></div><div class="field"><label for="ai-prompt-tokens">Input per call</label><input id="ai-prompt-tokens" type="number" min="1600" max="12000" value="${safe(ai.max_prompt_tokens)}"><small>Includes instructions, tool catalogue, and selected evidence.</small></div><div class="field"><label for="ai-max-checks">Additional checks</label><input id="ai-max-checks" type="number" min="0" max="4" value="${safe(ai.max_checks)}"></div></div>
      <div class="field"><label for="ai-key">API key</label><input id="ai-key" type="password" autocomplete="new-password" placeholder="${ai.api_key_configured ? 'Leave blank to keep the saved key' : 'Paste a key to enable investigation'}"><small>New credentials are checked before they replace a working local credential.</small></div>
      ${window.settingsNotice ? `<p class="notice">${safe(window.settingsNotice)}</p>` : ''}
      <div class="actions"><button id="save-ai-settings">Save investigation settings</button><button class="secondary" id="validate-ai-settings">Validate model</button></div>
    </div></section><section id="evidence-models" class="sheet"><div class="sheet-head"><h2>${icon('file-code-2')}Evidence models</h2>${capability(media.core_investigator?.capability)}</div><div class="sheet-body">
      <div class="model-state"><span>Image extraction ${capability(media.vision?.capability)}</span><span>Audio transcription ${capability(media.audio?.capability)}</span></div>
      <div class="field"><label for="media-key">OpenRouter API key</label><input id="media-key" type="password" autocomplete="new-password" placeholder="${media.api_key_configured ? 'Leave blank to keep the saved key' : 'Paste an optional key'}"><small>Image and audio evidence remain unavailable until the core investigator and the selected specialist are validated.</small></div>
      <div class="field"><label for="vision-model">Image model</label><input id="vision-model" value="${safe(media.vision?.model)}"></div>
      <div class="field"><label for="asr-model">Audio model</label><input id="asr-model" value="${safe(media.audio?.model)}"></div>
      ${window.mediaSettingsNotice ? `<p class="notice">${safe(window.mediaSettingsNotice)}</p>` : ''}
      <div class="actions"><button id="save-media-settings">Validate and save media</button><button class="secondary" id="validate-media-settings">Recheck media</button></div>
    </div></section></div>`;
  document.querySelector('#save-general-settings').addEventListener('click', saveGeneralSettings);
  document.querySelector('#save-ai-settings').addEventListener('click', saveAiSettings);
  document.querySelector('#validate-ai-settings').addEventListener('click', validateAiSettings);
  document.querySelector('#save-media-settings').addEventListener('click', saveMediaSettings);
  document.querySelector('#validate-media-settings').addEventListener('click', validateMediaSettings);
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
  const button = document.querySelector('#test-targets');
  const notice = document.querySelector('#target-notice');
  button.disabled = true;
  notice.hidden = false;
  notice.textContent = 'Testing saved source connections...';
  try {
    const response = await fetch('/api/sources/test', {method:'POST'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Unable to test connections.');
    lastState.sources.targets = result.targets || {};
    window.targetNotice = result.ok ? 'Saved source connections are healthy.' : 'One or more saved targets could not be reached.';
    await refresh();
  } catch (error) {
    window.targetNotice = error.message || 'Unable to test connections.';
  } finally {
    notice.textContent = window.targetNotice;
    button.disabled = false;
  }
}

async function syncTargets() {
  const response = await fetch('/api/sources/sync', {method:'POST'}); const result = await response.json();
  if (!response.ok) { alert(result.error || 'Unable to synchronize sources'); return; }
  window.targetNotice = 'Source synchronization started.'; document.querySelector('#sync-targets').disabled = true; await refresh(); document.querySelector('#sync-targets').disabled = false;
}

function episodeTable(items, capsules, archived = false) {
  if (!items.length) return `<div class="empty">${archived ? 'No archived episodes match these filters.' : 'No incidents match these filters.'}</div>`;
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
        <span class="episode-identity"><strong>${safe(item.title)}</strong><small>${safe(resourceLabel(item, application))} · ${safe(application?.namespace || '')}</small>${recurrenceLabel(item.recurrence) ? `<em class="recurrence-badge">${safe(recurrenceLabel(item.recurrence))}</em>` : ''}</span>
        <span class="episode-count">${item.signal_count} alert${item.signal_count === 1 ? '' : 's'}<small>${['queued','running'].includes(item.investigation?.status) ? 'Investigating' : item.investigation?.status === 'ready' ? 'Assessment ready' : item.report_count + ' reports'}</small></span>
        <time class="episode-time" datetime="${safe(item.started_at)}" title="${safe(formatDate(item.started_at))}">${relativeTime(item.started_at)}</time>
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
  const signal = episode.signals.find(item => item.incident_id === id) || episode.signals[0] || {};
  const identity = `<span class="incident-reference">${safe(signal.reference || episode.reference || id)}</span>`;
  const lifecycle = episode.status === 'resolved' && episode.investigation?.finished_at ? `Assessment saved ${safe(relativeTime(episode.investigation.finished_at))} · currently resolved` : episode.status === 'active' ? 'Currently active' : 'Currently resolved';
  if (reportTab === 'overview') return `<span class="queue-note">${identity} · ${episode.signal_count} captured alert${episode.signal_count === 1 ? '' : 's'} · ${lifecycle}</span><button class="icon-button" data-copy-incident-link title="Copy direct incident link" aria-label="Copy direct incident link">${icon('copy')}</button>`;
  if (episode.signals.length === 1) return '<span class="queue-note">Investigation</span>';
  return `<div class="signal-selector"><label for="signal-report">Alert report</label><select id="signal-report" data-signal-select>${episode.signals.map((signal, index) => `<option value="${safe(signal.incident_id)}" ${id === signal.incident_id ? 'selected' : ''}>${index + 1}. ${safe(signal.summary || signal.scenario)} · ${shortTime(signal.started_at)}</option>`).join('')}</select></div>`;
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '--';
}

function relativeTime(value) {
  const seconds = (new Date(value).getTime() - Date.now()) / 1000;
  if (!Number.isFinite(seconds)) return '--';
  const [unit, divisor] = Math.abs(seconds) >= 86400 ? ['day',86400] : Math.abs(seconds) >= 3600 ? ['hour',3600] : ['minute',60];
  return new Intl.RelativeTimeFormat('en', {numeric:'auto'}).format(Math.round(seconds / divisor), unit);
}

async function startInvestigation(id) {
  try {
    const response = await fetch('/api/episodes/' + encodeURIComponent(id) + '/investigation', {method:'POST'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Unable to start investigation');
    if (selectedEpisodeId === id && selectedReport) { selectedReport.investigation = result; renderPreservingFocus(() => renderConsole(lastState)); }
  } catch (error) { alert(error.message); }
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

function attachmentSummary(item) {
  const extraction = item.extraction || {};
  if (item.kind === 'audio') return extraction.transcript || extraction.limitation || 'Transcription pending.';
  const first = extraction.observations?.[0]?.fact || extraction.visible_text?.[0];
  return first || extraction.limitation || 'Visual extraction pending.';
}

function mediaEvidenceAvailability(media) {
  const config = media || (typeof lastState === 'object' ? lastState?.media : null) || {};
  const coreReady = config.core_investigator?.capability?.status === 'ready';
  return {
    image: coreReady && config.vision?.capability?.status === 'ready',
    audio: coreReady && config.audio?.capability?.status === 'ready',
  };
}

function mediaEvidencePanel(payload) {
  const episodeId = selectedEpisodeId || payload.investigation?.episode_id;
  const items = payload.media_evidence || [];
  const ready = items.some(item => item.status === 'ready');
  const available = mediaEvidenceAvailability();
  const addControl = available.image || available.audio
    ? `<button class="secondary" data-add-evidence="${safe(episodeId || '')}">Add evidence</button>`
    : '<a class="secondary button-link" href="/settings#evidence-models">Set up evidence models</a>';
  const rows = items.map(item => `<article class="media-evidence-row"><div><strong>${safe(item.filename)}</strong><small>${safe(item.kind)} · ${safe(item.status)}${item.observed_at ? ' · observed ' + safe(formatDate(item.observed_at)) : ''}</small><p>${safe(attachmentSummary(item))}</p></div><div class="media-evidence-actions"><a class="secondary button-link" href="${safe(item.artifact_url)}" target="_blank" rel="noopener">View</a>${item.status === 'ready' ? `<button class="secondary" data-correct-evidence="${safe(item.attachment_id)}">Correct</button>` : ''}</div></article>`).join('');
  const revision = payload.investigation_revisions || [];
  const history = revision.length > 1 ? disclosure('assessment-history', 'Assessment history', revision.map(item=>`<div class="revision-row"><span>${safe(item.reason.replaceAll('_',' '))}</span><strong>${safe(item.summary?.summary || item.status.replaceAll('_',' '))}</strong><small>${safe(formatDate(item.completed_at || item.created_at))}</small></div>`).join(''), revision.length) : '';
  const empty = available.image || available.audio
    ? 'No operator-supplied image or audio evidence.'
    : 'Optional image and audio evidence is not enabled.';
  return `<section class="media-evidence"><div class="section-heading"><h3>Additional evidence</h3><div class="row-actions">${addControl}${ready ? `<button data-update-evidence-investigation="${safe(episodeId || '')}">Update investigation</button>` : ''}</div></div>${rows || '<p class="queue-note">' + empty + '</p>'}${history}</section>`;
}

function sourceReviewPanel(payload) {
  const episodeId = selectedEpisodeId || payload.investigation?.episode_id;
  if (!episodeId) return '';
  const reviews = payload.source_disconnected_reviews || [];
  const rows = reviews.slice(0, 3).map(item => {
    const result = item.result || {};
    const usage = item.usage || {};
    const tokenText = Number.isFinite(Number(usage.total_tokens)) && Number(usage.total_tokens) > 0
      ? ' · ' + (usage.complete === false ? 'at least ' : '') + Number(usage.total_tokens).toLocaleString('en') + ' tokens'
      : '';
    return `<article class="source-review-row"><div><strong>${safe(item.question)}</strong><small>${safe(String(item.status || '').replaceAll('_',' '))} · ${safe(formatDate(item.completed_at || item.created_at))}${tokenText}</small>${result.answer ? `<p>${safe(result.answer)}</p><p class="queue-note">${safe(String(result.sufficiency || '').replaceAll('_',' '))} · ${safe(result.missing_discriminator || '')}</p>` : ''}</div></article>`;
  }).join('');
  const body = '<p class="queue-note">Ask a narrow question from preserved records only. Live sources are not queried.</p><button class="secondary" data-source-review="' + safe(episodeId) + '">Ask retained-capsule question</button>' + (rows ? '<div class="source-review-list">' + rows + '</div>' : '');
  return disclosure('source-disconnected-review', 'Retained-capsule review', body, reviews.length || '');
}

function fileToBase64(file) {
  return file.arrayBuffer().then(buffer => {
    const data = new Uint8Array(buffer); let text = '';
    for (let index = 0; index < data.length; index += 0x8000) text += String.fromCharCode(...data.subarray(index, index + 0x8000));
    return btoa(text);
  });
}

function showEvidenceDialog(episodeId) {
  if (!episodeId) return;
  const available = mediaEvidenceAvailability();
  if (!available.image && !available.audio) {
    location.assign('/settings#evidence-models');
    return;
  }
  const accepted = [
    ...(available.image ? ['image/png,image/jpeg,image/webp'] : []),
    ...(available.audio ? ['audio/wav,audio/mpeg,audio/ogg,audio/webm,audio/mp4,audio/x-m4a'] : []),
  ].join(',');
  const typeLabel = available.image && available.audio ? 'Image or short audio from the investigation.' : available.image ? 'Image from the investigation.' : 'Short audio from the investigation.';
  let state = {file:null, stream:null, recorder:null, objectUrl:null};
  const dialog = document.createElement('dialog');
  dialog.className = 'evidence-dialog';
  dialog.innerHTML = `<form method="dialog"><header><div><h2>Add evidence</h2><p>${typeLabel}</p></div><button class="icon-button" value="cancel" aria-label="Close">×</button></header><div class="dialog-body"><div class="field"><label for="evidence-file">File</label><input id="evidence-file" type="file" accept="${accepted}"><small>${available.image ? 'Images up to 6 MiB. ' : ''}${available.audio ? 'Audio up to 8 MiB.' : ''}</small></div>${available.audio ? '<div class="record-row"><button class="secondary" type="button" id="record-evidence">Record audio</button><span id="record-status" class="queue-note"></span></div>' : ''}<div id="evidence-preview" class="evidence-preview" hidden></div><div class="field"><label for="evidence-observed-at">Observed at</label><input id="evidence-observed-at" type="datetime-local"><small>Leave blank when the time is not known.</small></div><div class="field"><label for="evidence-note">Context</label><input id="evidence-note" maxlength="1000" placeholder="Optional note for the investigation"></div><label class="toggle"><input id="evidence-redacted" type="checkbox">This copy has been redacted where needed</label><p id="evidence-error" class="notice" hidden></p></div><footer><button class="secondary" value="cancel">Cancel</button><button type="button" id="submit-evidence" disabled>Analyze evidence</button></footer></form>`;
  document.body.append(dialog);
  const fileInput = dialog.querySelector('#evidence-file'); const preview = dialog.querySelector('#evidence-preview');
  const error = dialog.querySelector('#evidence-error'); const submit = dialog.querySelector('#submit-evidence'); const record = dialog.querySelector('#record-evidence'); const recordStatus = dialog.querySelector('#record-status');
  const stopTracks = () => { state.stream?.getTracks().forEach(track => track.stop()); state.stream = null; state.recorder = null; };
  const setFile = file => {
    if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
    state.file = file || null; preview.hidden = !file; preview.innerHTML = '';
    if (file) { state.objectUrl = URL.createObjectURL(file); preview.innerHTML = file.type.startsWith('image/') ? `<img src="${safe(state.objectUrl)}" alt="Selected evidence preview">` : `<audio controls src="${safe(state.objectUrl)}"></audio><span>${safe(file.name)} · ${bytes(file.size)}</span>`; }
    submit.disabled = !state.file;
  };
  fileInput.addEventListener('change', () => setFile(fileInput.files?.[0]));
  record?.addEventListener('click', async () => {
    if (state.recorder?.state === 'recording') { state.recorder.stop(); return; }
    try {
      if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error('Recording is unavailable in this browser context.');
      state.stream = await navigator.mediaDevices.getUserMedia({audio:true}); const mime = ['audio/webm','audio/ogg'].find(value => MediaRecorder.isTypeSupported(value)) || '';
      const chunks = []; state.recorder = new MediaRecorder(state.stream, mime ? {mimeType:mime} : undefined);
      state.recorder.addEventListener('dataavailable', event => { if (event.data.size) chunks.push(event.data); });
      state.recorder.addEventListener('stop', () => { const type = state.recorder?.mimeType || mime || 'audio/webm'; setFile(new File([new Blob(chunks,{type})], 'operator-observation.webm', {type})); stopTracks(); record.textContent = 'Record audio'; recordStatus.textContent = 'Recording ready.'; });
      state.recorder.start(); record.textContent = 'Stop recording'; recordStatus.textContent = 'Recording…';
    } catch (exception) { recordStatus.textContent = exception.message || 'Unable to start recording.'; stopTracks(); }
  });
  submit.addEventListener('click', async () => {
    if (!state.file) return; submit.disabled = true; error.hidden = true;
    try {
      const kind = state.file.type.startsWith('image/') ? 'image' : state.file.type.startsWith('audio/') ? 'audio' : '';
      if (!kind) throw new Error('Choose an image or audio file.');
      if ((kind === 'image' && !available.image) || (kind === 'audio' && !available.audio)) throw new Error('This evidence type is not enabled. Validate its model in Settings.');
      const localTime = dialog.querySelector('#evidence-observed-at').value;
      const payload = {kind, filename:state.file.name, content_base64:await fileToBase64(state.file), observed_at:localTime ? new Date(localTime).toISOString() : '', context_note:dialog.querySelector('#evidence-note').value, source_redacted:dialog.querySelector('#evidence-redacted').checked};
      const response = await fetch('/api/episodes/' + encodeURIComponent(episodeId) + '/evidence', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}); const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Unable to submit evidence.');
      if (selectedReport) selectedReport.media_evidence = [result, ...(selectedReport.media_evidence || [])];
      dialog.close(); renderPreservingFocus(() => renderConsole(lastState)); await refresh();
    } catch (exception) { error.textContent = exception.message || 'Unable to submit evidence.'; error.hidden = false; submit.disabled = false; }
  });
  dialog.addEventListener('close', () => { stopTracks(); if (state.objectUrl) URL.revokeObjectURL(state.objectUrl); dialog.remove(); });
  dialog.showModal();
}

async function updateWithEvidence(episodeId) {
  const response = await fetch('/api/episodes/' + encodeURIComponent(episodeId) + '/investigation/update', {method:'POST'}); const result = await response.json();
  if (!response.ok) { alert(result.error || 'Unable to update investigation.'); return; }
  if (selectedReport) { selectedReport.investigation = result; renderPreservingFocus(() => renderConsole(lastState)); }
}

async function askSourceReview(episodeId) {
  const question = window.prompt('Question for the retained capsule');
  if (question === null || !question.trim()) return;
  const response = await fetch('/api/episodes/' + encodeURIComponent(episodeId) + '/source-review', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({question:question.trim()})});
  const result = await response.json();
  if (!response.ok) { alert(result.error || 'Unable to start the retained-capsule review.'); return; }
  if (selectedReport) {
    selectedReport.source_disconnected_reviews = [result, ...(selectedReport.source_disconnected_reviews || [])];
    renderPreservingFocus(() => renderConsole(lastState));
  }
}

async function correctEvidence(attachmentId) {
  const correction = window.prompt('Correction or missing context');
  if (correction === null) return;
  const response = await fetch('/api/evidence/' + encodeURIComponent(attachmentId) + '/correction', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({correction})}); const result = await response.json();
  if (!response.ok) { alert(result.error || 'Unable to save correction.'); return; }
  if (selectedReport) { selectedReport.media_evidence = (selectedReport.media_evidence || []).map(item=>item.attachment_id === attachmentId ? result : item); renderPreservingFocus(() => renderConsole(lastState)); }
}

async function saveAiSettings() {
  const payload = {model:document.querySelector('#ai-model').value, max_tokens:Number(document.querySelector('#ai-max-tokens').value), max_total_tokens:Number(document.querySelector('#ai-total-tokens').value), max_prompt_tokens:Number(document.querySelector('#ai-prompt-tokens').value), max_checks:Number(document.querySelector('#ai-max-checks').value), api_key:document.querySelector('#ai-key').value};
  const response = await fetch('/api/settings/ai', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const result = await response.json();
  if (!response.ok) { alert(result.error || 'Unable to save settings'); return; }
  window.settingsNotice = 'Settings saved locally.';
  lastState.ai = result;
  renderSettings(lastState);
}

async function validateAiSettings() {
  const button = document.querySelector('#validate-ai-settings'); button.disabled = true;
  try {
    const response = await fetch('/api/settings/ai/validate', {method:'POST'}); const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Unable to validate the model');
    lastState.ai = result; window.settingsNotice = result.capability?.status === 'ready' ? 'Core model validated.' : (result.capability?.message || 'Validation did not complete.');
  } catch (error) { window.settingsNotice = error.message || 'Unable to validate the model.'; }
  finally { renderSettings(lastState); }
}

async function saveMediaSettings() {
  const button = document.querySelector('#save-media-settings'); button.disabled = true;
  const payload = {vision_model:document.querySelector('#vision-model').value, asr_model:document.querySelector('#asr-model').value, api_key:document.querySelector('#media-key').value};
  try {
    const response = await fetch('/api/settings/media', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}); const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Unable to save media settings');
    lastState.media = result; window.mediaSettingsNotice = 'Media settings saved locally.';
  } catch (error) { window.mediaSettingsNotice = error.message || 'Unable to save media settings.'; }
  finally { renderSettings(lastState); }
}

async function validateMediaSettings() {
  const button = document.querySelector('#validate-media-settings'); button.disabled = true;
  try {
    const response = await fetch('/api/settings/media/validate', {method:'POST'}); const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Unable to validate media models');
    lastState.media = result; window.mediaSettingsNotice = 'Media validation finished.';
  } catch (error) { window.mediaSettingsNotice = error.message || 'Unable to validate media models.'; }
  finally { renderSettings(lastState); }
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

async function separateRelatedEpisode(groupId, episodeId) {
  if (!confirm('Keep this episode separate from the automatic related-condition cue?')) return;
  const response = await fetch(`/api/related-groups/${encodeURIComponent(groupId)}/episodes/${encodeURIComponent(episodeId)}/separate`, {method:'POST'});
  const result = await response.json();
  if (!response.ok) { alert(result.error || 'Unable to separate the episode.'); return; }
  await refresh();
}

async function copyIncidentLink(button) {
  const link = location.href;
  try {
    await navigator.clipboard.writeText(link);
    button.classList.add('copied');
    button.title = 'Link copied';
    setTimeout(() => { button.classList.remove('copied'); button.title = 'Copy direct incident link'; }, 1600);
  } catch (_) {
    window.prompt('Copy this incident link:', link);
  }
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
  const run = payload.investigation || {status:'not_started', episode_id:selectedEpisodeId};
  const assessment = run.assessment;
  const loading = ['queued','running','waiting'].includes(run.status);
  const saved = run.finished_at ? 'Assessment saved ' + formatDate(run.finished_at) : '';
  const header = '<div class="section-heading"><h3>Episode assessment</h3><span class="queue-note">' + safe([run.model, saved].filter(Boolean).join(' · ')) + '</span></div>';
  if (!assessment) return '<section class="briefing">' + header + '<div class="analysis-state" role="status" aria-live="polite">' +
    (loading ? '<span class="spinner"></span>' : '') + '<div><strong>' + (loading ? 'Investigating' : run.status === 'not_configured' ? 'Provider key required' : 'No validated conclusion yet') + '</strong><p>' + safe(run.message || 'Retained evidence is available below.') + '</p>' +
    (!loading ? run.status === 'not_configured' ? '<a href="/settings">Open Settings</a>' : '<button class="secondary" data-investigate="' + safe(run.episode_id) + '">Investigate episode</button>' : '') + '</div></div></section>';
  const hypotheses = (assessment.hypotheses || []).map(item => '<article class="hypothesis-row"><div class="section-heading"><h4>' + safe(item.explanation) + '</h4><span class="hypothesis-state ' + safe(item.status) + '">' + safe(item.status) + '</span></div><p>' + safe(item.reason) + '</p><div class="citations">' + investigationRefs(run, item.evidence_ids) + '</div></article>').join('');
  const name = id => run.context?.alerts?.find(item=>item.incident_id === id)?.title || id;
  const connections = (assessment.connections || []).map(item=>'<article class="hypothesis-row"><h4>' + safe(name(item.from)) + ' / ' + safe(name(item.to)) + '</h4><small>' + safe(item.relationship.replaceAll('_',' ')) + '</small><p>' + safe(item.reason) + '</p><div class="citations">' + investigationRefs(run,item.evidence_ids) + '</div></article>').join('');
  const history = assessment.historical_comparison;
  const historyCandidate = run.context?.historical_candidates?.find(item => item.episode_id === history?.episode_id);
  const historyHtml = history ? '<section class="history-comparison"><div><span class="text-label">Related history</span><h4>' + safe(history.status.replaceAll('_',' ')) + '</h4><p>' + safe(history.summary) + '</p></div><div><span class="incident-reference">' + safe(historyCandidate?.reference || history.episode_id) + '</span><div class="citations">' + investigationRefs(run, history.evidence_ids) + '</div></div></section>' : '';
  const findings = (run.findings || []).map((item, index) => '<article class="finding ' + (index === 0 ? 'primary' : '') + '"><div class="section-heading"><div><span class="text-label">' + safe(String(item.state || 'observed').replaceAll('_',' ')) + '</span><h4>' + safe(item.title) + '</h4></div><span class="finding-category">' + safe(String(item.category || '').replaceAll('_',' ')) + '</span></div><p>' + safe(item.summary) + '</p><div class="citations">' + investigationRefs(run, item.evidence_ids) + '</div><p class="finding-next"><strong>Verify next:</strong> ' + safe(item.next_check) + '</p>' + (item.observations?.length ? disclosure('finding-' + safe(item.id), 'Observed details', item.observations.map(observation => '<pre class="compact-data">' + safe(JSON.stringify(observation, null, 2)) + '</pre>').join(''), item.observations.length) : '') + '</article>').join('');
  return '<section class="briefing">' + header + (findings ? '<div class="findings">' + findings + '</div>' : '') + '<p class="brief-lead">' + safe(assessment.summary) + '</p><h4>Likely explanation</h4><p>' + safe(assessment.likely_mechanism) + '</p>' +
    '<div class="next-check"><h4>Next action</h4><p><strong>' + safe(assessment.next_action) + '</strong></p><p><span class="text-label">What would confirm it</span>' + safe(assessment.expected_finding) + '</p></div>' +
    '<p class="uncertainty"><strong>Still unconfirmed:</strong> ' + safe(assessment.uncertainty) + '</p><div class="citations">' + investigationRefs(run,assessment.evidence_ids) + '</div>' +
    historyHtml +
    disclosure('competing-explanations','Explanations considered',hypotheses,assessment.hypotheses?.length) +
    (connections ? disclosure('alert-connections','How the alerts relate',connections,assessment.connections.length) : '') + '</section>';
}

function investigationRefs(run, ids = []) {
  const labels = new Map();
  const checks = {workload_state:'Runtime snapshot',resource_history:'Resource history',search_logs:'Source logs',compare_baseline:'Baseline comparison',database_pressure:'Database metrics',dependency_evidence:'Dependency evidence',review_omitted:'Omitted log patterns',historical_episode:'Prior episode',alert_rule_logic:'Alert logic',scrape_discovery:'Target discovery'};
  return ids.map(id => {
    const item = [...(run.checks || []), ...(run.context?.evidence || [])].find(item=>item.id === id);
    const label = checks[item?.tool] || (item?.domain === 'log_template' ? logLabel(item.title) : item?.title) || item?.question || id;
    const occurrence = (labels.get(label) || 0) + 1; labels.set(label,occurrence);
    return '<button class="evidence-link" data-investigation-ref="' + safe(id) + '" title="' + safe(item?.question || label) + '">' + safe(label) + (occurrence > 1 ? ' · additional capture' : '') + '</button>';
  }).join('');
}

function investigationProgress(run = {}) {
  const checks = run.checks || [];
  const usage = run.usage;
  const budget = run.token_budget || {};
  const tokenText = usage ? (usage.complete ? '' : 'At least ') + Number(usage.total_tokens || 0).toLocaleString('en') + (run.status === 'running' ? ' reported tokens' : ' tokens') : 'Usage pending';
  const rows = checks.map(item => '<li class="agent-step ' + safe(item.status) + '"><span class="step-marker" aria-hidden="true">' + (item.status === 'running' ? '<span class="spinner"></span>' : icon(item.status === 'completed' ? 'activity' : 'bell-ring')) + '</span><div><strong>' + safe(item.question) + '</strong><small>' + safe(item.status) + (item.finished_at ? ' · ' + formatDate(item.finished_at) : '') + '</small></div></li>').join('');
  const details = '<dl class="coverage-details"><div><dt>Input tokens</dt><dd>' + Number(usage?.prompt_tokens || 0).toLocaleString('en') + '</dd></div><div><dt>Output tokens</dt><dd>' + Number(usage?.completion_tokens || 0).toLocaleString('en') + '</dd></div><div><dt>Safety reserve</dt><dd>' + Number(budget.accounted_total_tokens || 0).toLocaleString('en') + (budget.maximum_total_tokens ? ' / ' + Number(budget.maximum_total_tokens).toLocaleString('en') : '') + '</dd></div><div><dt>Budget remaining</dt><dd>' + Number(budget.remaining_tokens || 0).toLocaleString('en') + '</dd></div><div><dt>Model calls</dt><dd>' + (run.calls?.length || 0) + '</dd></div><div><dt>Earlier attempts</dt><dd>' + Number(run.lifetime_usage?.total_tokens || 0).toLocaleString('en') + ' tokens</dd></div></dl><p class="queue-note">' + (usage?.complete ? 'Provider-reported usage is shown alongside a pre-call safety reserve.' : 'Provider usage is incomplete; the safety reserve prevents unbounded follow-up calls.') + '</p>';
  return '<aside class="agent-progress"><h3>Investigation activity</h3>' + (rows ? '<ol class="agent-steps">' + rows + '</ol>' : '<p class="queue-note">No checks recorded yet.</p>') +
    '<div class="usage-note">' + disclosure('token-usage',tokenText,details) + '</div>' +
    (run.lifetime_usage?.complete === false ? '<p class="queue-note">Earlier-attempt usage is a lower bound; an interrupted request has no final count.</p>' : '') +
    (['ready','incomplete'].includes(run.status) ? '<button class="secondary" data-investigate="' + safe(run.episode_id) + '">' + icon('refresh-cw') + ' Reassess</button>' : '') + '</aside>';
}

function investigationResult(result = {}, checkId = '') {
  const labels = {pod_cpu_cores:'CPU used',pod_cpu_limit_cores:'CPU limit',pod_memory_working_set_bytes:'Memory working set',pod_memory_limit_bytes:'Memory limit',pod_cpu_throttled_ratio:'CPU periods throttled',pod_container_restarts_total:'Restart counter',pod_ready:'Pod readiness',pod_oom_terminated:'Last termination was OOM',mysql_global_status_threads_connected:'MySQL connections',mysql_global_status_threads_running:'MySQL active threads',mysql_global_variables_max_connections:'MySQL connection limit'};
  const numeric = (value, metric) => {
    if (metric.endsWith('_bytes')) return (Number(value)/1048576).toLocaleString('en',{maximumFractionDigits:2}) + ' MiB';
    if (metric.endsWith('_cores')) return (Number(value)*1000).toLocaleString('en',{maximumFractionDigits:2}) + ' mCPU';
    if (metric.endsWith('_ratio')) return (Number(value)*100).toLocaleString('en',{maximumFractionDigits:1}) + '%';
    if (metric === 'pod_ready') return Number(value) >= 1 ? 'Ready' : Number(value) === 0 ? 'Not ready' : 'Mixed samples';
    return Number(value).toLocaleString('en',{maximumSignificantDigits:5});
  };
  const series = result.observations?.filter(item=>item.metric) || result.affected || [];
  let body = '<p class="queue-note">' + safe(result.source || '') + (result.pod ? ' · ' + safe(result.pod) : '') + '</p>';
  if (series.length) body += '<div class="table-scroll"><table class="check-metrics"><thead><tr><th>Metric</th><th>Min</th><th>Max</th><th>Median</th>' + (result.reference ? '<th>Reference median</th>' : '') + '</tr></thead><tbody>' + series.map(item=>{
    const reference = result.reference?.find(other=>other.metric === item.metric);
    return '<tr><th scope="row" title="' + safe(item.metric) + '">' + safe(labels[item.metric] || item.metric) + '<small>' + formatDate(item.start) + ' to ' + formatDate(item.end) + '</small></th><td>' + numeric(item.min,item.metric) + '</td><td>' + numeric(item.max,item.metric) + '</td><td>' + numeric(item.median,item.metric) + '</td>' + (result.reference ? '<td>' + (reference ? numeric(reference.median,item.metric) : 'No samples') + '</td>' : '') + '</tr>';
  }).join('') + '</tbody></table></div>';
  if (result.reference) body += '<p class="queue-note">Reference: ' + safe(result.method?.replaceAll('_',' ')) + ' · ' + safe(result.reference_pod) + '</p>';
  const patterns = result.patterns || result.observations?.filter(item=>item.pattern) || [];
  if (patterns.length) body += patterns.map((item,index)=>disclosure('check-pattern-' + checkId + '-' + index, logLabel(item.pattern),'<pre class="log-lines">' + safe((item.examples || []).map(line=>typeof line === 'string' ? line : line.timestamp + ' ' + line.message).join('\n')) + '</pre>',item.count)).join('');
  const configs = result.observations?.filter(item=>item.kind) || [];
  if (configs.length) body += '<ul class="snapshot-list">' + configs.map(item=>'<li><strong>' + safe(item.kind) + ' · ' + safe(item.name) + '</strong><small>' + safe(item.phase || (item.keys || []).join(', ')) + '</small>' +
    (item.resources || []).map(container=>'<p>' + safe(container.name) + ' limits: <code>' + safe(JSON.stringify(container.limits)) + '</code></p>').join('') +
    (item.container_states || []).map(container=>'<p>' + safe(container.name) + ': ' + safe(container.restart_count) + ' restarts' + (container.last_state?.terminated ? ' · ' + safe(container.last_state.terminated.reason) + ' · exit ' + safe(container.last_state.terminated.exitCode) + ' · ' + formatDate(container.last_state.terminated.finishedAt) : '') + '</p>').join('') + '</li>').join('') + '</ul>';
  if (result.episode) {
    const historic = result.episode;
    const prior = historic.prior_hypothesis || historic.assessment || {};
    body += '<section class="historical-observation"><span class="incident-reference">' + safe(historic.reference || historic.episode_id) + '</span><strong>' + safe(historic.title) + '</strong><small>' + formatDate(historic.started_at) + ' · ' + safe(historic.status) + '</small>' +
      (prior.summary ? '<p><span class="text-label">Earlier hypothesis, not proof</span>' + safe(prior.summary) + '</p>' : '<p>No earlier model hypothesis was retained.</p>') +
      '<small>' + safe((historic.captured_evidence || []).length) + ' retained incident capture(s) available for comparison.</small></section>';
  }
  if (!series.length && !patterns.length && !configs.length) body += '<p>No observations returned.</p>';
  return body + '<p class="uncertainty">' + safe(result.limitation || result.comparability || '') + '</p><details class="raw-observation"><summary>Observation JSON</summary><pre class="log-lines">' + safe(JSON.stringify(result,null,2)) + '</pre></details>';
}

function investigationEvidence(run = {}) {
  const cited = new Set([...(run.assessment?.evidence_ids || []), ...(run.assessment?.hypotheses || []).flatMap(item=>item.evidence_ids), ...(run.assessment?.connections || []).flatMap(item=>item.evidence_ids), ...(run.assessment?.historical_comparison?.evidence_ids || [])]);
  const checks = (run.checks || []).map(item=>disclosure('agent-' + item.id,item.question,'<p>' + safe(item.distinguishes) + '</p><p class="queue-note">' + formatDate(item.started_at) + ' · ' + safe(item.status) + '</p>' + investigationResult(item.result,item.id),item.id)).join('');
  const exampleText = examples => examples.map(item => typeof item === 'string' ? item : JSON.stringify(item)).join('\n');
  const evidence = (run.context?.evidence || []).filter(item=>cited.has(item.id)).map(item=>disclosure('agent-' + item.id, item.domain === 'log_template' ? logLabel(item.title) : item.title,
    '<p>' + safe(item.summary) + '</p><p class="queue-note">' + safe(item.domain) + ' · ' + formatDate(item.time_range?.start) + '</p>' +
    (item.examples?.length ? '<pre class="log-lines">' + safe(exampleText(item.examples)) + '</pre>' : '') +
    (item.configuration || item.alert ? '<pre class="log-lines">' + safe(JSON.stringify(item.configuration || item.alert,null,2)) + '</pre>' : '') +
    '<small>Captured in ' + (item.provenance || []).map(ref=>safe(ref.incident_id)).join(', ') + '</small>')).join('');
  return disclosure('agent-evidence','Investigation evidence',checks + evidence || '<p>No investigation evidence yet.</p>', (run.checks?.length || 0) + (run.context?.evidence || []).filter(item=>cited.has(item.id)).length);
}

function investigationTimeline(run = {}) {
  const rows = (run.checks || []).map(item=>'<li><time>' + formatDate(item.started_at) + '</time><div><strong>' + safe(item.question) + '</strong><small>' + safe(item.status) + ' · ' + safe(item.tool) + '</small><button class="evidence-link" data-investigation-ref="' + safe(item.id) + '">View observation</button></div></li>').join('');
  const review = (run.calls || []).find(item=>item.phase === 'evidence_review');
  return '<section class="timeline-view"><h3>Agent activity</h3><ol class="event-timeline">' + rows +
    (review ? '<li><time>' + formatDate(review.started_at) + '</time><div><strong>Conclusion checked against evidence</strong><small>' + safe(run.review?.status || review.status) + '</small></div></li>' : '') +
    (run.finished_at ? '<li><time>' + formatDate(run.finished_at) + '</time><div><strong>' + (run.status === 'ready' ? 'Assessment saved' : 'Stopped without a validated conclusion') + '</strong></div></li>' : '') + '</ol></section>';
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
  const signals = (episode?.signals || []).map(signal=>'<li><time>' + formatDate(signal.started_at) + '</time><div><strong>' + safe(signal.summary || signal.scenario) + '</strong><small>' + safe(signal.severity) + ' · ' + safe(signal.status) + (signal.ended_at && signal.status === 'resolved' ? ' ' + formatDate(signal.ended_at) : '') + '</small></div></li>').join('');
  return '<section class="timeline-view"><h3>Episode alerts</h3><ol class="event-timeline">' + signals + '</ol>' +
    disclosure('retained-sequence', 'Captured evidence sequence', '<ol class="event-timeline">' + (report.timeline || []).map(item=>'<li><time>' + formatDate(item.timestamp) + '</time><div><strong>' + safe(item.title) + '</strong><small>' + safe(item.description) + '</small></div></li>').join('') + '</ol>') +
    (report.topology?.length ? '<p class="queue-note">Dependencies: ' + report.topology.map(item=>safe(item.from) + ' → ' + safe(item.to)).join(' · ') + '</p>' : '') + '</section>';
}
function capsuleExport(payload) {
  const capsule = payload.record;
  if (!capsule) return '';
  const storage = payload.storage || {};
  const root = '/artifacts/' + encodeURIComponent(capsule.capsule_id) + '/';
  const link = (file, title, size) => '<a class="button-link" href="' + root + encodeURIComponent(file) + '" download><span>' + title + '</span><small>' + (size == null ? '' : bytes(size)) + '</small></a>';
  return '<details class="export-menu" data-disclosure="exports"><summary id="export-summary">Export</summary><div class="export-panel">' +
    (storage.archive_bytes !== null ? link('fcapsule_' + payload.report.incident.incident_id + '.zip', 'Capsule archive', storage.archive_bytes) : '<p>Archive unavailable</p>') +
    link('incident_report.json', 'Report JSON', storage.report_bytes) +
    (['ready','incomplete'].includes(payload.investigation?.status) ? link('episode_investigation.json','Investigation JSON',null) : '') +
    '<p>Retained evidence and analysis. Not a full telemetry backup.</p>' +
    (storage.expires_at ? '<p><strong>Eligible for cleanup ' + formatDate(storage.expires_at) + '</strong>' + storage.retention_days + '-day retention, including archived incidents.</p>' : '') +
    (storage.directory ? '<details><summary>Storage location</summary><code>' + safe(storage.directory) + '</code></details>' : '') + '</div></details>';
}
function reportPanel(payload) {
  if (!payload.report) return '<section class="report-empty"><h3>Evidence captured</h3><p>The report is ' + (lastState?.running ? 'being prepared.' : 'not built yet.') + '</p><button data-build-capsule="' + safe(payload.incident.incident_id) + '" ' + (lastState?.running ? 'disabled' : '') + '>Build report</button></section>';
  const report = payload.report;
  const incident = report.incident;
  const exports = capsuleExport(payload);
  const ai = payload.investigation;
  const fallback = ai?.status !== 'ready' ? '<section class="observed-summary"><h3>Alert context</h3><p>' + safe(report.fault_alerts?.[0]?.description || incident.summary) + '</p></section>' : '';
  const d = report.engineering_diagnostics;
  const diagnostics = disclosure('diagnostics', 'Engineering diagnostics', '<dl class="coverage-details"><div><dt>Selected evidence</dt><dd>' + d.selected_evidence + '</dd></div><div><dt>Log reduction</dt><dd>' + pct(d.log_compression_ratio) + '</dd></div><div><dt>Signal preservation</dt><dd>' + pct(d.important_signal_preservation) + '</dd></div><div><dt>Grounding</dt><dd>' + pct(d.hypothesis_grounding_score) + '</dd></div><div><dt>Runtime</dt><dd>' + Number(d.runtime_seconds || 0).toFixed(2) + 's</dd></div></dl><p class="queue-note">Rule-based hypothesis: ' + safe(report.primary_hypothesis.statement) + '</p>');
  const tabs = ['overview','evidence','timeline'];
  let content;
  if (reportTab === 'evidence') content = investigationEvidence(ai || {}) + '<h3 class="capture-heading">Selected alert capture</h3>' + evidencePanel(report);
  else if (reportTab === 'timeline') content = timelinePanel(report) + investigationTimeline(ai || {});
  else content = '<div class="overview-layout"><div>' + briefingPanel(payload) + fallback + mediaEvidencePanel(payload) + sourceReviewPanel(payload) + '</div>' + investigationProgress(ai || {}) + '</div>';
  return '<section id="incident-report"><div class="report-navigation"><div role="tablist" aria-label="Investigation views">' +
    tabs.map(name=>'<button id="tab-' + name + '" role="tab" data-report-tab="' + name + '" aria-selected="' + (name === reportTab) + '" aria-controls="investigation-panel" tabindex="' + (name === reportTab ? 0 : -1) + '">' + name[0].toUpperCase() + name.slice(1) + '</button>').join('') +
    '</div>' + exports + '</div><div id="investigation-panel" role="tabpanel" aria-labelledby="tab-' + reportTab + '" tabindex="0">' + content + '</div><div class="report-technical">' + diagnostics + '</div></section>';
}

function renderPatterns(state) {
  const applications = new Map(state.overview.applications.map(item => [item.app_id, item]));
  const patterns = state.overview.patterns || [];
  const rows = patterns.map(pattern => {
    const application = applications.get(pattern.app_id);
    const target = resourceLabel({app_id:pattern.app_id, resource:pattern.resource}, application);
    const interval = intervalLabel(pattern.observed_interval_seconds);
    return `<article class="pattern-row"><div class="pattern-main"><span class="pattern-reference">${safe(pattern.pattern_id)}</span><h2>${safe(pattern.title)}</h2><p>${safe(target)} · ${safe(application?.namespace || '')}</p></div><dl class="pattern-stats"><div><dt>Occurrences</dt><dd>${safe(pattern.occurrence_count)}</dd></div><div><dt>Observed</dt><dd>${safe(relativeTime(pattern.first_seen_at))} to ${safe(relativeTime(pattern.last_seen_at))}</dd></div><div><dt>Interval</dt><dd>${safe(interval || 'No stable interval')}</dd></div></dl><div class="pattern-episodes"><span>Recent episodes</span>${pattern.episodes.map(episode => `<a href="/console?episode=${encodeURIComponent(episode.episode_id)}">${safe(episode.reference)} · ${safe(relativeTime(episode.started_at))}</a>`).join('')}</div></article>`;
  }).join('');
  app.innerHTML = `<div class="page-head"><div><div class="eyebrow">Incident history</div><h1>Patterns</h1><p>Repeated episodes are kept separate and linked through their retained evidence.</p></div><a class="button-link" href="/console">Open operations</a></div><section class="patterns"><div class="sheet-head"><h2>Recurring issues</h2><span class="queue-note">${patterns.length} recurring pattern${patterns.length === 1 ? '' : 's'}</span></div>${rows || '<div class="empty">No recurring patterns in retained history.</div>'}</section>`;
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
      view === 'targets' ? renderTargets(state) : view === 'patterns' ? renderPatterns(state) : renderSettings(state);
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
    } else if (view === 'patterns') {
      renderPatterns(state);
    }
  } catch (_) {
    document.querySelector('.system-state').className = 'system-state error';
    document.querySelector('#system-state').textContent = 'Disconnected';
  } finally { refreshing = false; }
}
refresh();
setInterval(refresh, 4000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
document.addEventListener('click', event => {
  if (!event.target.closest('.export-menu')) {
    document.querySelector('.export-menu')?.removeAttribute('open');
    openDisclosures.delete('exports');
  }
});
document.addEventListener('keydown', event => {
  const menu = document.querySelector('.export-menu[open]');
  if (event.key === 'Escape' && menu) {
    menu.removeAttribute('open');
    openDisclosures.delete('exports');
    menu.querySelector('summary').focus();
  }
});
