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
let lastPatternsSignature = '';
let sourceReturn = null;
let patternsMode = new URLSearchParams(location.search).get('view') === 'shared' ? 'shared' : 'recurring';
let pendingSharedAnchor = location.hash.startsWith('#shared-');
const openDisclosures = new Set();
const closedNamespaces = new Set();

function renderPreservingFocus(render) {
  const focused = document.activeElement;
  const focusId = focused?.id;
  const selection = focused instanceof HTMLInputElement && focused.type === 'text' ? [focused.selectionStart, focused.selectionEnd] : null;
  const anchorId = selectedEpisodeId ? 'toggle-' + selectedEpisodeId : null;
  const anchorTop = anchorId ? document.getElementById(anchorId)?.getBoundingClientRect().top : null;
  document.querySelectorAll('details[data-disclosure]').forEach(node => {
    if (node.open) openDisclosures.add(node.dataset.disclosure);
    else openDisclosures.delete(node.dataset.disclosure);
  });
  render();
  document.querySelectorAll('details[data-disclosure]').forEach(node => { node.open = openDisclosures.has(node.dataset.disclosure); });
  if (anchorId && anchorTop != null) {
    const nextTop = document.getElementById(anchorId)?.getBoundingClientRect().top;
    if (nextTop != null && Math.abs(nextTop - anchorTop) > 1) window.scrollBy(0, nextTop - anchorTop);
  }
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
  return JSON.stringify([state.overview.episodes, state.overview.archived_episodes, state.overview.related_groups, selectedReport, selectedEpisodeId, showArchived, queueFilters, reportTab, reportLoading, reportError, state.running]);
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
function intervalLabel(seconds, occurrenceCount = 0) {
  if (!Number.isFinite(Number(seconds)) || Number(seconds) <= 0) return '';
  const value = Number(seconds);
  const [amount, unit] = value >= 86400 ? [Math.round(value / 86400), 'day'] : value >= 3600 ? [Math.round(value / 3600), 'hour'] : value >= 60 ? [Math.round(value / 60), 'minute'] : [Math.round(value), 'second'];
  const gap = `${amount} ${unit}${amount === 1 ? '' : 's'}`;
  return occurrenceCount <= 2 ? `${gap} between two episodes` : `${gap} median of ${occurrenceCount - 1} gaps`;
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
    if (cutoff && now - new Date(item.last_activity_at || item.started_at).getTime() > cutoff) return false;
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
  const advanced = ['scope', 'status', 'period'].filter(key => queueFilters[key]).length;
  const chips = Object.entries(queueFilters).filter(([, value]) => Boolean(value)).map(([key, value]) => {
    const label = {namespace:'Namespace',scope:'Target',status:'Status',period:'Time',query:'Search'}[key];
    const display = key === 'period' ? {day:'Last 24 hours',week:'Last 7 days',month:'Last 30 days'}[value] : value;
    return `<button class="filter-chip" type="button" data-remove-filter="${safe(key)}" aria-label="Remove ${safe(label)} filter: ${safe(display)}">${safe(label)}: ${safe(display)} <span aria-hidden="true">×</span></button>`;
  });
  return `<form class="queue-filters" aria-label="Filter incident queue">
    <label><span>Namespace</span><select id="queue-namespace" data-queue-filter="namespace">${option('', 'All namespaces', queueFilters.namespace)}${namespaces.map(value => option(value, value, queueFilters.namespace)).join('')}</select></label>
    <label class="queue-search"><span>Search</span><input id="queue-search" type="text" data-queue-filter="query" value="${safe(queueFilters.query)}" placeholder="ID or text"></label>
    <details class="queue-more" data-disclosure="queue-advanced" ${openDisclosures.has('queue-advanced') ? 'open' : ''}><summary>Filters${advanced ? ` <span class="detail-count">${advanced}</span>` : ''}</summary>
      <div class="queue-more-fields">
        <label><span>Target</span><select id="queue-scope" data-queue-filter="scope">${option('', 'All targets', queueFilters.scope)}${scopes.map(value => option(value, value, queueFilters.scope)).join('')}</select></label>
        <label><span>Status</span><select id="queue-status" data-queue-filter="status">${option('', 'Any status', queueFilters.status)}${option('active', 'Active', queueFilters.status)}${option('resolved', 'Resolved', queueFilters.status)}</select></label>
        <label><span>Time</span><select id="queue-period" data-queue-filter="period">${option('', 'All time', queueFilters.period)}${option('day', 'Last 24 hours', queueFilters.period)}${option('week', 'Last 7 days', queueFilters.period)}${option('month', 'Last 30 days', queueFilters.period)}</select></label>
      </div></details>
    ${chips.length ? `<div class="active-filters" aria-label="Applied filters">${chips.join('')}<button class="filter-clear" type="button" data-clear-filters>Clear all</button></div>` : ''}
  </form>`;
}
function relatedActivity(groups) {
  if (!groups.length) return '<div class="empty">No shared conditions in retained history.</div>';
  const basisText = basis => (basis || []).filter(item => item.kind !== 'time_window').slice(0,2).map(item => {
    const label = item.kind === 'same_node' ? 'Shared node' : item.kind === 'shared_dependency' ? 'Shared dependency' : 'Shared alert';
    return label + ': ' + item.value;
  }).join(' · ');
  return `<section class="related-activity" aria-label="Potential shared conditions"><div class="related-activity-head"><div><h2>Potential shared conditions</h2><p>These episodes share context. A common cause has not been established.</p></div><span>${groups.length} cue${groups.length === 1 ? '' : 's'}</span></div><div class="related-group-list">${groups.map(group => {
    const count = group.episode_count || group.episodes?.length || 0;
    const state = group.active_count ? `${group.active_count} active` : 'resolved';
    return `<details class="related-group" id="shared-${safe(group.group_id)}"><summary><div class="related-group-main"><span class="related-reference">${safe(group.reference)}</span><strong>${safe(group.title)}</strong><small>${safe(basisText(group.basis))}</small></div><div class="related-group-meta"><span>${safe(quantity(count, 'episode'))}</span><span>${safe(state)}</span><span>${safe(relativeTime(group.last_observed_at || group.updated_at))}</span></div></summary><div class="related-group-body"><p class="queue-note">Review each episode before treating this context as causal.</p><div class="related-group-episodes">${(group.episodes || []).map(episode => `<span><button class="related-open" data-related-open="${safe(episode.episode_id)}">${safe(episode.reference)}</button><small>${safe(episode.title)} · ${safe(relativeTime(episode.last_activity_at || episode.started_at))}</small><button class="related-separate" data-related-group="${safe(group.group_id)}" data-related-separate="${safe(episode.episode_id)}" title="Keep this episode separate">Separate</button></span>`).join('')}</div></div></details>`;
  }).join('')}</div></section>`;
}
function renderConsole(state) {
  lastConsoleSignature = consoleSignature(state);
  const data = state.overview;
  const allQueueEpisodes = [...(showArchived ? data.archived_episodes : data.episodes)];
  const apps = new Map(data.applications.map(item => [item.app_id, item]));
  const episodes = filteredEpisodes(allQueueEpisodes, apps).sort((a,b) => String(b.last_activity_at || b.started_at).localeCompare(String(a.last_activity_at || a.started_at)));
  const active = data.episodes.filter(item => item.status === 'active').length;
  app.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Incident workspace</div><h1>Operations</h1><p>${active} active · ${data.episodes.length - active} resolved</p></div></div>
    <section class="queue"><div class="sheet-head"><div><h2>Incident queue</h2><span class="queue-note">${quantity(episodes.length, 'episode')} shown · newest activity first</span></div><div class="queue-view" role="group" aria-label="Queue view"><button type="button" data-queue-mode="current" aria-pressed="${!showArchived}">In queue</button><button type="button" data-queue-mode="archived" aria-pressed="${showArchived}">Archived${data.archived_episodes.length ? ` (${data.archived_episodes.length})` : ''}</button></div></div>
    ${queueFiltersPanel(allQueueEpisodes, apps)}
    ${episodeTable(episodes, data.capsules, showArchived)}</section>`;
  document.querySelector('.queue-filters')?.addEventListener('submit', event => event.preventDefault());
  document.querySelectorAll('[data-queue-mode]').forEach(button => button.addEventListener('click', () => {
    const next = button.dataset.queueMode === 'archived';
    if (next === showArchived) return;
    selectedReport = null; selectedEpisodeId = null; requestSequence++; showArchived = next;
    updateLocation(); renderConsole(lastState);
  }));
  document.querySelectorAll('[data-queue-filter]').forEach(field => field.addEventListener(field.tagName === 'INPUT' ? 'input' : 'change', () => {
    queueFilters[field.dataset.queueFilter] = field.value; selectedReport = null; selectedEpisodeId = null; requestSequence++;
    updateLocation(); renderPreservingFocus(() => renderConsole(lastState));
  }));
  document.querySelector('[data-clear-filters]')?.addEventListener('click', () => {
    Object.keys(queueFilters).forEach(key => { queueFilters[key] = ''; });
    selectedReport = null; selectedEpisodeId = null; requestSequence++; updateLocation(); renderConsole(lastState);
  });
  document.querySelectorAll('[data-remove-filter]').forEach(button => button.addEventListener('click', () => {
    queueFilters[button.dataset.removeFilter] = '';
    selectedReport = null; selectedEpisodeId = null; requestSequence++;
    updateLocation(); renderPreservingFocus(() => renderConsole(lastState));
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
    sourceReturn = null;
    renderPreservingFocus(() => renderConsole(lastState));
    document.getElementById('tab-' + reportTab)?.focus({preventScroll:true});
  }));
  document.querySelector('[role="tablist"]')?.addEventListener('keydown', event => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const names = ['overview','investigation','evidence','timeline'];
    reportTab = event.key === 'Home' ? names[0] : event.key === 'End' ? names.at(-1) : names[(names.indexOf(reportTab) + (event.key === 'ArrowRight' ? 1 : names.length - 1)) % names.length];
    renderPreservingFocus(() => renderConsole(lastState));
    document.getElementById('tab-' + reportTab)?.focus({preventScroll:true});
  });
  document.querySelectorAll('[data-evidence-link]').forEach(button => button.addEventListener('click', () => {
    sourceReturn = {episodeId:selectedEpisodeId, tab:reportTab, scrollY:window.scrollY, index:[...document.querySelectorAll('[data-evidence-link]')].indexOf(button), kind:'evidence'};
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
    sourceReturn = {episodeId:selectedEpisodeId, tab:reportTab, scrollY:window.scrollY, index:[...document.querySelectorAll('[data-investigation-ref]')].indexOf(button), kind:'investigation'};
    reportTab = 'evidence';
    const id = button.dataset.investigationRef;
    openDisclosures.add('agent-evidence'); openDisclosures.add('agent-' + id);
    renderConsole(lastState);
    const target = document.getElementById('disclosure-agent-' + id);
    target?.scrollIntoView({block:'center', behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
    target?.focus({preventScroll:true});
  }));
  document.querySelector('[data-return-source]')?.addEventListener('click', () => {
    const previous = sourceReturn;
    if (!previous || previous.episodeId !== selectedEpisodeId) return;
    sourceReturn = null;
    reportTab = previous.tab;
    renderConsole(lastState);
    window.scrollTo({top:previous.scrollY, behavior:'instant'});
    const selector = previous.kind === 'evidence' ? '[data-evidence-link]' : '[data-investigation-ref]';
    document.querySelectorAll(selector)[previous.index]?.focus({preventScroll:true});
  });
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
  if (!items.length) return `<div class="empty">${archived && !lastState.overview.archived_episodes.length ? 'No archived episodes yet.' : Object.values(queueFilters).some(Boolean) ? 'No episodes match these filters.' : 'No incidents in the queue.'}</div>`;
  const apps = new Map(lastState.overview.applications.map(item => [item.app_id, item]));
  return items.map(item => {
    const isOpen = selectedEpisodeId === item.episode_id;
    const application = apps.get(item.app_id);
    const related = (lastState.overview.related_groups || []).filter(group => (group.episodes || []).some(member => member.episode_id === item.episode_id));
    const lifecycle = archived ? `<button class="secondary" data-restore="${safe(item.episode_id)}">Restore</button><button class="danger" data-delete="${safe(item.episode_id)}">Delete episode</button>` : `<button class="secondary" data-archive="${safe(item.episode_id)}">Archive</button>`;
    return `<article class="episode ${isOpen ? 'is-open' : ''}">
      <details class="episode-expander" ${isOpen ? 'open' : ''}>
      <summary id="toggle-${safe(item.episode_id)}" data-episode-toggle="${safe(item.episode_id)}" aria-controls="body-${safe(item.episode_id)}" aria-expanded="${isOpen}">
      <span class="episode-summary">
        <span class="episode-state">${status(item.severity)}${status(item.status)}</span>
        <span class="episode-identity"><strong>${safe(item.title)}</strong><small>${safe(resourceLabel(item, application))} · ${safe(application?.namespace || '')}</small>${recurrenceLabel(item.recurrence) ? `<em class="recurrence-badge">${safe(recurrenceLabel(item.recurrence))}</em>` : ''}</span>
        <span class="episode-count">${item.signal_count} alert${item.signal_count === 1 ? '' : 's'}<small>${['queued','running'].includes(item.investigation?.status) ? 'Investigating' : item.investigation?.status === 'ready' ? 'Assessment ready' : item.report_count + ' reports'}</small></span>
        <time class="episode-time" datetime="${safe(item.last_activity_at || item.started_at)}" title="Last activity: ${safe(formatExactDate(item.last_activity_at || item.started_at))}">${relativeTime(item.last_activity_at || item.started_at)}</time>
      </span></summary>
      <div class="episode-body" id="body-${safe(item.episode_id)}">
        ${isOpen ? `
        <div class="investigation-toolbar">${episodeContext(item)}<div class="row-actions">${lifecycle}</div></div>
        ${reportLoading ? '<div class="analysis-state" role="status"><span class="spinner"></span>Opening retained evidence…</div>' : reportError ? `<p class="notice" role="alert">${safe(reportError)}</p>` : selectedReport ? reportPanel(selectedReport) : ''}
        ${related.length ? `<div class="episode-related"><span>Potential shared context · ${safe(quantity(related.length, 'cue'))}</span><div>${related.map(group => `<a href="/patterns?view=shared#shared-${encodeURIComponent(group.group_id)}" title="Review possible shared condition, not a confirmed common cause">${safe(group.reference || group.title)}${icon('chevron-right')}</a>`).join('')}</div></div>` : ''}
        <footer class="episode-footer"><span>${safe(application?.cluster || '')} / ${safe(application?.namespace || '')} · ${item.status === 'resolved' ? 'Resolved ' + formatDate(item.ended_at) : 'Last alert ' + formatDate(item.last_activity_at)}</span><span>Grouped by workload and time</span></footer>
        ` : ''}
      </div></details></article>`;
  }).join('');
}

function episodeContext(episode) {
  const id = reportId() || episode.primary_incident_id;
  const signal = episode.signals.find(item => item.incident_id === id) || episode.signals[0] || {};
  const identity = `<span class="incident-reference" title="Shareable episode reference">Episode ${safe(episode.reference || episode.episode_id)}</span>`;
  const investigation = episode.investigation || {};
  const assessmentState = investigation.status === 'ready' ? `Assessment ready ${safe(relativeTime(investigation.finished_at))}`
    : investigation.status === 'incomplete' ? `Investigation needs attention ${safe(relativeTime(investigation.finished_at))}`
      : investigation.status === 'running' || investigation.status === 'queued' ? 'Investigation in progress' : '';
  const lifecycle = assessmentState || (episode.status === 'active' ? 'Currently active' : 'Currently resolved');
  const episodeLine = `<div class="episode-context-line">${identity}<button class="icon-button" data-copy-incident-link title="Copy direct episode link" aria-label="Copy direct episode link">${icon('copy')}</button><span class="queue-note">${episode.signal_count} captured alert${episode.signal_count === 1 ? '' : 's'} · ${lifecycle}</span></div>`;
  if (['overview','investigation'].includes(reportTab)) return episodeLine;
  const capture = episode.signals.length === 1
    ? `<span class="queue-note">Selected alert capture · ${safe(signal.reference || id)} · ${safe(formatDate(signal.started_at))}</span>`
    : `<div class="signal-selector"><label for="signal-report">Alert capture</label><select id="signal-report" data-signal-select>${episode.signals.map((item, index) => `<option value="${safe(item.incident_id)}" ${id === item.incident_id ? 'selected' : ''}>${index + 1}. ${safe(item.summary || item.scenario)} · ${shortTime(item.started_at)}</option>`).join('')}</select></div>`;
  return episodeLine + capture;
}

function formatDate(value) {
  return value ? new Date(value).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '--';
}
function formatExactDate(value) {
  return value ? new Date(value).toLocaleString([], {year:'numeric', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', timeZoneName:'short'}) : '--';
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
  if (!keepOpen) { reportTab = 'overview'; sourceReturn = null; openDisclosures.clear(); animateEpisodeId = episodeId; }
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
  if (item.kind === 'text') return extraction.content_text || item.context_note || 'Operator context';
  if (item.kind === 'audio') return extraction.transcript || extraction.limitation || 'Transcription pending.';
  const first = extraction.observations?.[0]?.fact || extraction.visible_text?.[0];
  return first || extraction.limitation || 'Visual extraction pending.';
}

function mediaEvidenceAvailability(media) {
  const config = media || (typeof lastState === 'object' ? lastState?.media : null) || {};
  const coreReady = config.core_investigator?.capability?.status === 'ready';
  return {
    text: coreReady,
    image: coreReady && config.vision?.capability?.status === 'ready',
    audio: coreReady && config.audio?.capability?.status === 'ready',
  };
}

function mediaEvidencePanel(payload, includeHistory = true) {
  const episodeId = selectedEpisodeId || payload.investigation?.episode_id;
  const items = payload.media_evidence || [];
  const visible = new Set((payload.investigation?.calls || []).flatMap(call => call.visible_evidence_ids || []));
  const changed = item => !visible.has('A-' + item.attachment_id) || Date.parse(item.updated_at) > Date.parse(payload.investigation?.started_at || payload.investigation?.created_at);
  const ready = items.some(item => item.status === 'ready' && changed(item));
  const rows = items.map(item => {
    const useState = item.status === 'ready' ? (changed(item) ? 'ready for a new assessment' : 'provided to latest investigation') : '';
    return `<article class="media-evidence-row"><div><strong>${safe(item.filename)}</strong><small>${safe(item.kind)} · ${safe(item.status)}${useState ? ' · ' + safe(useState) : ''}${item.uploaded_at ? ' · uploaded ' + safe(formatDate(item.uploaded_at)) : ''}${item.observed_at ? ' · observed ' + safe(formatDate(item.observed_at)) : ''}</small><p>${safe(attachmentSummary(item))}</p></div><div class="media-evidence-actions"><a class="secondary button-link" href="${safe(item.artifact_url)}" target="_blank" rel="noopener">View</a>${item.status === 'ready' ? `<button class="secondary" data-correct-evidence="${safe(item.attachment_id)}">Correct</button>` : ''}</div></article>`;
  }).join('');
  const revision = includeHistory ? payload.investigation_revisions || [] : [];
  const history = revision.length > 1 ? disclosure('assessment-history', 'Assessment history', revision.map(item=>`<div class="revision-row"><span>${safe(item.reason.replaceAll('_',' '))}</span><strong>${safe(item.summary?.summary || item.status.replaceAll('_',' '))}</strong><small>${safe(formatDate(item.completed_at || item.created_at))}</small></div>`).join(''), revision.length) : '';
  if (!items.length) return history;
  return disclosure('operator-evidence', 'Added context', `<section class="media-evidence">${ready ? `<div class="row-actions"><button data-update-evidence-investigation="${safe(episodeId || '')}">Review new context</button></div>` : ''}${rows}${history}</section>`, items.length);
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
  if (!available.text) {
    location.assign('/settings#evidence-models');
    return;
  }
  const imageTypes = ['image/png','image/jpeg','image/webp'];
  const audioTypes = ['audio/wav','audio/x-wav','audio/mpeg','audio/ogg','audio/webm','audio/mp4','audio/x-m4a'];
  const canRecord = available.audio && !!navigator.mediaDevices?.getUserMedia && !!window.MediaRecorder;
  const state = {file:null, stream:null, recorder:null, objectUrl:null, timer:null, busy:false, closed:false};
  const dialog = document.createElement('dialog');
  dialog.className = 'evidence-dialog';
  dialog.innerHTML = `<form method="dialog"><header><div><h2>Add context</h2><p>An observation, a log excerpt, or evidence from another tool.</p></div><button class="icon-button" value="cancel" aria-label="Close">${icon('x')}</button></header><div class="dialog-body"><div class="context-composer"><label class="text-label" for="evidence-note">What did you observe?</label><textarea id="evidence-note" maxlength="16000" rows="6" placeholder="Add context or paste an image..."></textarea><div id="evidence-preview" class="evidence-preview" hidden></div><div class="composer-tools"><div><button type="button" class="icon-button" id="attach-image" aria-label="Attach image" title="${available.image ? 'Attach image (up to 6 MiB)' : 'Validate the image model in Settings'}" ${available.image ? '' : 'disabled'}>${icon('scan-line')}</button><button type="button" class="icon-button" id="record-evidence" aria-label="Record audio" title="${canRecord ? 'Record audio (up to 60 seconds)' : 'Recording requires HTTPS or localhost and a validated audio model'}" ${canRecord ? '' : 'disabled'}>${icon('mic')}</button><button type="button" class="icon-button" id="attach-file" aria-label="Attach audio or import text" title="Attach audio or import a text/log file">${icon('paperclip')}</button></div><span id="record-status" class="queue-note" role="status">Uploaded time is saved automatically</span></div></div><input id="evidence-file" type="file" hidden><details class="context-metadata"><summary>Source details (optional)</summary><div class="field"><label for="evidence-observed-at">Time of observation</label><input id="evidence-observed-at" type="datetime-local"><small>Unknown when blank; separate from upload time.</small></div><label class="toggle"><input id="evidence-redacted" type="checkbox">I have redacted this copy where needed</label></details><p class="queue-note">Saved as operator-provided evidence. Review sensitive content before uploading.</p><p id="evidence-error" class="notice" role="alert" hidden></p></div><footer><button class="secondary" value="cancel">Cancel</button><button type="button" id="submit-evidence" disabled>Add context</button></footer></form>`;
  document.body.append(dialog);
  const fileInput = dialog.querySelector('#evidence-file'); const preview = dialog.querySelector('#evidence-preview');
  const note = dialog.querySelector('#evidence-note'); const error = dialog.querySelector('#evidence-error'); const submit = dialog.querySelector('#submit-evidence'); const record = dialog.querySelector('#record-evidence'); const recordStatus = dialog.querySelector('#record-status');
  const sync = () => { submit.disabled = state.busy || state.recorder?.state === 'recording' || (!state.file && !note.value.trim()); };
  const fail = message => { error.textContent = message; error.hidden = false; };
  const stopTracks = () => { clearTimeout(state.timer); state.stream?.getTracks().forEach(track => track.stop()); state.stream = null; state.recorder = null; };
  const setFile = async file => {
    if (state.closed || state.busy) return;
    error.hidden = true;
    if (file && (/\.(txt|log)$/i.test(file.name) || file.type === 'text/plain')) {
      if (file.size > 64000) return fail('Text files must be at most 16,000 characters (64 KB).');
      const text = await file.text();
      if (state.closed) return;
      const combined = [note.value.trim(),text].filter(Boolean).join('\n\n');
      if (combined.length > 16000) return fail('Context is limited to 16,000 characters. Choose a focused excerpt.');
      note.value = combined; sync(); return;
    }
    if (file) {
      const type = file.type.split(';')[0];
      const isImage = imageTypes.includes(type); const isAudio = audioTypes.includes(type);
      if ((!isImage && !isAudio) || (isImage && !available.image) || (isAudio && !available.audio)) return fail('This file type is unavailable. Validate its model in Settings, or add text context.');
      if (file.size > (isImage ? 6 : 8) * 1024 * 1024) return fail(isImage ? 'Images are limited to 6 MiB.' : 'Audio is limited to 8 MiB.');
    }
    if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
    state.file = file || null; preview.hidden = !file; preview.innerHTML = '';
    if (file) {
      state.objectUrl = URL.createObjectURL(file);
      preview.innerHTML = (file.type.startsWith('image/') ? `<img src="${safe(state.objectUrl)}" alt="Selected evidence preview">` : `<audio controls src="${safe(state.objectUrl)}"></audio>`) + `<div class="attachment-caption"><span>${safe(file.name)} · ${bytes(file.size)}</span><button type="button" class="icon-button" aria-label="Remove attachment">${icon('x')}</button></div>`;
      preview.querySelector('button').addEventListener('click', () => setFile(null));
    }
    sync();
  };
  note.addEventListener('input', sync);
  note.addEventListener('paste', event => {
    const image = [...(event.clipboardData?.items || [])].find(item => item.kind === 'file' && item.type.startsWith('image/'));
    if (image) { event.preventDefault(); setFile(image.getAsFile()); }
  });
  fileInput.addEventListener('change', () => setFile(fileInput.files?.[0]));
  dialog.querySelector('#attach-image').addEventListener('click', () => { fileInput.accept = imageTypes.join(','); fileInput.value = ''; fileInput.click(); });
  dialog.querySelector('#attach-file').addEventListener('click', () => { fileInput.accept = ['.txt','.log',...(available.audio ? audioTypes : [])].join(','); fileInput.value = ''; fileInput.click(); });
  record.addEventListener('click', async () => {
    if (state.recorder?.state === 'recording') { state.recorder.stop(); return; }
    try {
      record.disabled = true;
      const stream = await navigator.mediaDevices.getUserMedia({audio:true});
      if (state.closed) { stream.getTracks().forEach(track => track.stop()); return; }
      state.stream = stream;
      const mime = ['audio/webm','audio/ogg','audio/mp4'].find(value => MediaRecorder.isTypeSupported(value)) || '';
      const chunks = []; let size = 0;
      const recorder = new MediaRecorder(stream, mime ? {mimeType:mime} : undefined); state.recorder = recorder;
      recorder.addEventListener('dataavailable', event => { if (event.data.size) { chunks.push(event.data); size += event.data.size; if (size > 8 * 1024 * 1024 && recorder.state === 'recording') recorder.stop(); } });
      recorder.addEventListener('stop', () => {
        const type = recorder.mimeType || mime || 'audio/webm'; stopTracks();
        if (state.closed) return;
        setFile(new File(chunks, 'operator-observation.' + (type.includes('ogg') ? 'ogg' : type.includes('mp4') ? 'm4a' : 'webm'), {type}));
        record.innerHTML = icon('mic'); record.setAttribute('aria-label','Record audio'); record.title = 'Record audio (up to 60 seconds)'; recordStatus.textContent = 'Recording ready'; sync();
      });
      recorder.start(1000); state.timer = setTimeout(() => { if (recorder.state === 'recording') recorder.stop(); },60000);
      record.innerHTML = icon('square'); record.setAttribute('aria-label','Stop recording'); record.title = 'Stop recording'; recordStatus.textContent = 'Recording · 60-second limit'; record.disabled = false; sync();
    } catch (exception) { fail(exception.message || 'Unable to start recording.'); stopTracks(); record.disabled = !canRecord; sync(); }
  });
  submit.addEventListener('click', async () => {
    if (submit.disabled) return; state.busy = true; sync(); error.hidden = true;
    try {
      const file = state.file;
      const kind = !file ? 'text' : file.type.startsWith('image/') ? 'image' : 'audio';
      const localTime = dialog.querySelector('#evidence-observed-at').value;
      const payload = {kind, filename:file?.name || 'operator-context.txt', observed_at:localTime ? new Date(localTime).toISOString() : '', source_redacted:dialog.querySelector('#evidence-redacted').checked,
        ...(file ? {content_base64:await fileToBase64(file),context_note:note.value.trim()} : {content_text:note.value.trim()})};
      const response = await fetch('/api/episodes/' + encodeURIComponent(episodeId) + '/evidence', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}); const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Unable to submit evidence.');
      if (selectedEpisodeId === episodeId && selectedReport) { selectedReport.media_evidence = [result, ...(selectedReport.media_evidence || [])]; openDisclosures.add('operator-evidence'); }
      if (!state.closed) dialog.close(); renderPreservingFocus(() => renderConsole(lastState)); await refresh();
    } catch (exception) { fail(exception.message || 'Unable to submit evidence.'); state.busy = false; sync(); }
  });
  dialog.addEventListener('close', () => { state.closed = true; if (state.recorder?.state === 'recording') state.recorder.stop(); stopTracks(); if (state.objectUrl) URL.revokeObjectURL(state.objectUrl); dialog.remove(); });
  dialog.showModal(); note.focus();
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
    setTimeout(() => { button.classList.remove('copied'); button.title = 'Copy direct episode link'; }, 1600);
  } catch (_) {
    window.prompt('Copy this episode link:', link);
  }
}

function sparkline(signal) {
  const values = signal.values || [];
  if (values.length < 2) return '';
  const width = 380; const height = 110; const left = 42; const pad = 8;
  const finite = point => point.value != null && Number.isFinite(Number(point.value)) && Number.isFinite(Date.parse(point.timestamp));
  const valid = values.filter(finite);
  if (!valid.length) return '';
  const isRule = signal.signal_origin === 'alert_rule';
  const numbers = valid.map(point => Number(point.value));
  const guides = [signal.baseline_value, ...(isRule ? [signal.threshold] : [])].filter(value=>value != null && Number.isFinite(Number(value))).map(Number);
  let floor = Math.min(...numbers,...guides); let ceiling = Math.max(...numbers,...guides);
  if (signal.metric === 'up' || signal.metric === 'pod_ready') { floor = 0; ceiling = 1; }
  else if (floor === ceiling) { const margin = Math.max(Math.abs(floor) * 0.1,0.1); floor = floor >= 0 ? Math.max(0,floor-margin) : floor-margin; ceiling += margin; }
  const range = Math.max(ceiling - floor, 0.01);
  const timestamps = values.map(point=>Date.parse(point.timestamp)).filter(Number.isFinite);
  const start = Math.min(...timestamps); const end = Math.max(...timestamps);
  const x = stamp => left + (Date.parse(stamp) - start) / Math.max(end - start,1) * (width - left - pad);
  const y = value => height - pad - (Number(value) - floor) / range * (height - 2 * pad);
  let continuous = false;
  const path = values.map(item=>{ if (!finite(item)) { continuous = false; return ''; } const segment = `${continuous ? 'L' : 'M'}${x(item.timestamp).toFixed(1)},${y(item.value).toFixed(1)}`; continuous = true; return segment; }).join(' ');
  const extreme = valid.find(point=>point.timestamp === signal.alert_timestamp) || valid[numbers.indexOf(signal.metric === 'pod_ready' ? Math.min(...numbers) : Math.max(...numbers))];
  const marker = isRule ? signal.alert_timestamp : extreme.timestamp;
  const markerTime = Date.parse(marker);
  const markerLine = Number.isFinite(markerTime) && markerTime >= start && markerTime <= end ? `<line class="${isRule ? 'alert-start' : 'incident'}" x1="${x(marker)}" y1="0" x2="${x(marker)}" y2="${height}"/>` : '';
  const baseline = signal.baseline_value != null ? `<line class="baseline" x1="${left}" y1="${y(signal.baseline_value)}" x2="${width}" y2="${y(signal.baseline_value)}"/>` : '';
  const threshold = isRule && signal.threshold != null ? `<line class="threshold" x1="${left}" y1="${y(signal.threshold)}" x2="${width}" y2="${y(signal.threshold)}"/>` : '';
  const tick = value => {
    const metric = String(signal.metric || '');
    const divisor = metric.endsWith('_bytes') ? 1048576 : 1;
    return Number(value / divisor).toLocaleString('en',{notation:'compact',maximumSignificantDigits:3}) + (divisor > 1 ? ' MiB' : '');
  };
  return `<svg class="spark" viewBox="0 0 ${width} ${height}" role="img" aria-label="${safe(signal.label)} across the captured window. ${isRule ? 'Amber horizontal line: threshold. Red vertical line: alert start, when in range.' : 'Dashed vertical line: selected deviation, not alert time. Horizontal line: baseline median.'} Gaps represent missing samples."><text x="0" y="12" class="chart-tick">${tick(ceiling)}</text><text x="0" y="${height-pad}" class="chart-tick">${tick(floor)}</text><line class="axis" x1="${left}" y1="${height - pad}" x2="${width}" y2="${height - pad}"/>${baseline}${threshold}${markerLine}<path class="series" d="${path}"/>${valid.length === 1 ? `<circle class="single-sample" cx="${x(valid[0].timestamp)}" cy="${y(valid[0].value)}" r="3"/>` : ''}</svg>`;
}

function evidenceReferences(report, ids) {
  const groups = [
    ['alerts', report.fault_alerts || [], item=>item.name],
    ['metrics', report.pm_signals || [], item=>item.label],
    ['logs', report.log_patterns || [], item=>logLabel(item.pattern)],
    ['config', report.configuration_evidence || [], item=>item.name],
  ];
  const labels = new Map();
  return ids.map(id => {
    for (const [domain, items, label] of groups) {
      const item = items.find(entry=>entry.evidence_id === id);
      if (item) {
        const prefix = {alerts:'Alert',metrics:'Metric',logs:'Logs',config:'Config'}[domain];
        const raw = domain === 'logs' ? String(label(item)).replace(/^(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL):\s*/i,'') : String(label(item));
        const title = raw.length > 54 ? raw.slice(0, 51) + '…' : raw;
        const text = prefix + ': ' + title;
        const occurrence = (labels.get(text) || 0) + 1; labels.set(text, occurrence);
        const detail = occurrence > 1 ? ' · ' + (item.first_seen ? shortTime(item.first_seen) : String(occurrence)) : '';
        const iconName = {alerts:'bell-ring',metrics:'chart-no-axes-combined',logs:'logs',config:'file-code-2'}[domain];
        return '<button class="evidence-link" data-evidence-link="' + safe(id) + '" data-domain="' + domain + '" title="Open retained ' + safe(prefix.toLowerCase()) + ' evidence">' + icon(iconName) + '<span>' + safe(text + detail) + '</span></button>';
      }
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
function briefingPanel(payload, detailed = false) {
  const run = payload.investigation || {status:'not_started', episode_id:selectedEpisodeId};
  const assessment = run.assessment;
  if (detailed && !assessment) return '';
  const loading = ['queued','running','waiting'].includes(run.status);
  const saved = run.finished_at ? (run.status === 'ready' ? 'Assessment ready ' : run.status === 'inconclusive' ? 'Assessment inconclusive ' : 'Last attempt ') + formatDate(run.finished_at) : '';
  const header = '<div class="section-heading"><h3>Episode assessment</h3><span class="queue-note">' + safe([run.model, saved].filter(Boolean).join(' · ')) + '</span></div>';
  const incompleteNote = run.status === 'incomplete'
    ? '<details class="assessment-note"><summary>Why this needs attention</summary><p>The last attempt stopped before a conclusion met the evidence contract. Retained observations below are still available.</p>' + (run.validation_error ? '<small>' + safe(run.validation_error) + '</small>' : '') + '</details>' : '';
  const inconclusiveNote = run.status === 'inconclusive'
    ? '<details class="assessment-note" open><summary>Why no cause is asserted</summary><p>This is an evidence-preserving abstention, not a diagnosis. The retained observations remain available for review and reassessment.</p></details>' : '';
  const pendingTitle = loading ? 'Investigating' : {
    not_configured:'Provider key required', incomplete:'Investigation needs attention',
    inconclusive:'No supported cause yet',
  }[run.status] || 'No validated conclusion yet';
  const pendingCopy = loading ? run.message || 'Checking retained evidence and bounded source observations.'
    : run.status === 'incomplete' ? 'The attempt stopped before validation. Review retained observations, then reassess when the missing discriminator is available.'
      : run.status === 'inconclusive' ? run.message || 'The retained observations cannot distinguish the likely mechanisms. Check the missing evidence in Evidence before reassessing.'
        : run.status === 'not_configured' ? 'Configure the investigation provider in Settings; retained evidence remains available.'
          : run.message || 'Retained evidence is available below.';
  if (!assessment) return '<section class="briefing">' + header + '<div class="analysis-state" role="status" aria-live="polite">' +
    (loading ? '<span class="spinner"></span>' : '') + '<div><strong>' + safe(pendingTitle) + '</strong><p>' + safe(pendingCopy) + '</p>' + incompleteNote + (run.status === 'inconclusive' ? inconclusiveNote : '') +
    (!loading ? run.status === 'not_configured' ? '<a href="/settings">Open Settings</a>' : '<button class="secondary" data-investigate="' + safe(run.episode_id) + '">' + (run.status === 'not_started' ? 'Start investigation' : 'Reassess episode') + '</button>' : '') + '</div></div></section>';
  const hypotheses = (assessment.hypotheses || []).map(item => '<article class="hypothesis-row"><div class="section-heading"><h4>' + safe(item.explanation) + '</h4><span class="hypothesis-state ' + safe(item.status) + '">' + safe(item.status) + '</span></div><p>' + safe(item.reason) + '</p><div class="citations">' + investigationRefs(run, item.evidence_ids) + '</div></article>').join('');
  const name = id => run.context?.alerts?.find(item=>item.incident_id === id)?.title || id;
  const connections = (assessment.connections || []).map(item=>'<article class="hypothesis-row"><h4>' + safe(name(item.from)) + ' / ' + safe(name(item.to)) + '</h4><small>' + safe(item.relationship.replaceAll('_',' ')) + '</small><p>' + safe(item.reason) + '</p><div class="citations">' + investigationRefs(run,item.evidence_ids) + '</div></article>').join('');
  const history = assessment.historical_comparison;
  const historyCandidate = run.context?.historical_candidates?.find(item => item.episode_id === history?.episode_id);
  const historyHtml = history ? '<section class="history-comparison"><div><span class="text-label">Related history</span><h4>' + safe(history.status.replaceAll('_',' ')) + '</h4><p>' + safe(history.summary) + '</p></div><div><span class="incident-reference">' + safe(historyCandidate?.reference || history.episode_id) + '</span><div class="citations">' + investigationRefs(run, history.evidence_ids) + '</div></div></section>' : '';
  const same = (left, right) => String(left || '').trim().replace(/[.\s]+$/,'').toLowerCase() === String(right || '').trim().replace(/[.\s]+$/,'').toLowerCase();
  const assessorFinding = (run.findings || []).find(item => item.category === 'investigator_assessment');
  const primary = assessorFinding || run.findings?.[0];
  const mechanism = assessment.likely_mechanism || primary?.summary || '';
  const mainIds = [...new Set([...(assessment.evidence_ids || []), ...(primary && same(primary.summary, mechanism) ? primary.evidence_ids || [] : [])])];
  const discrepancy = assessorFinding && mechanism && !same(assessorFinding.summary, mechanism)
    ? '<section class="assessment-discrepancy" role="note"><strong>Assessment discrepancy</strong><p>The retained initial finding differs from the latest mechanism. Verify both before acting.</p><dl><div><dt>Latest mechanism</dt><dd>' + safe(mechanism) + '</dd></div><div><dt>Initial finding</dt><dd>' + safe(assessorFinding.summary) + '</dd></div></dl><div class="citations">' + investigationRefs(run, assessorFinding.evidence_ids) + '</div></section>' : '';
  const seenObservations = new Set();
  const observations = (run.findings || []).filter(item => {
    if (item === assessorFinding && discrepancy) return false;
    const key = String(item.summary || '').trim().toLowerCase();
    if (!key || same(key, mechanism) || same(key, assessment.summary) || seenObservations.has(key)) return false;
    seenObservations.add(key);
    return true;
  });
  const briefObservations = observations.slice(0, 3).map(item => '<li><strong>' + safe(item.title) + '</strong><p>' + safe(item.summary) + '</p><div class="citations">' + investigationRefs(run, item.evidence_ids) + '</div></li>').join('');
  const findingDetails = (run.findings || []).map(item => '<article class="finding"><h4>' + safe(item.title) + '</h4><p>' + safe(item.summary) + '</p><div class="citations">' + investigationRefs(run, item.evidence_ids) + '</div>' +
    (item.next_check && !same(item.next_check, assessment.next_action) ? '<p><strong>Additional check:</strong> ' + safe(item.next_check) + '</p>' : '') +
    (item.observations?.length ? disclosure('finding-' + safe(item.id), 'Observed details', item.observations.map(observation => '<pre class="compact-data">' + safe(JSON.stringify(observation, null, 2)) + '</pre>').join(''), item.observations.length) : '') + '</article>').join('');
  const atCapture = assessment.summary && !same(assessment.summary, mechanism)
    ? disclosure('assessment-context', 'Capture context', '<p class="assessment-context">' + safe(assessment.summary) + '</p>') : '';
  if (detailed) return '<section class="investigation-details"><h3>Assessment details</h3>' + atCapture +
    (historyHtml ? disclosure('related-history', 'Related history', historyHtml) : '') +
    (findingDetails ? disclosure('all-findings', 'Retained findings', '<div class="findings">' + findingDetails + '</div>', run.findings.length) : '') +
    (hypotheses ? disclosure('competing-explanations','Explanations considered',hypotheses,assessment.hypotheses.length) : '') +
    (connections ? disclosure('alert-connections','How the alerts relate',connections,assessment.connections.length) : '') + '</section>';
  return '<section class="briefing">' + header + inconclusiveNote +
    '<div class="assessment-decision"><div class="assessment-main"><span class="text-label">' + (run.status === 'inconclusive' ? 'Evidence assessment · inconclusive' : 'Likely explanation · model assessment') + '</span><p class="brief-lead">' + safe(mechanism || 'No supported cause yet.') + '</p>' + (assessment.basis ? '<p class="assessment-basis"><strong>Why this fits</strong> ' + safe(assessment.basis) + '</p>' : '') + '<div class="citations">' + investigationRefs(run, mainIds) + '</div></div>' +
    '<div class="next-check"><h4>Next check</h4><p><strong>' + safe(assessment.next_action) + '</strong></p><p><span class="text-label">What would confirm it</span>' + safe(assessment.expected_finding) + '</p></div>' +
    '</div>' + discrepancy + (briefObservations ? '<div class="key-observations"><h4>Key observations</h4><ul>' + briefObservations + '</ul></div>' : '') +
    '<p class="uncertainty"><strong>Still unconfirmed:</strong> ' + safe(assessment.uncertainty) + '</p></section>';
}

function investigationRefs(run, ids = []) {
  const labels = new Map();
  const checks = {workload_state:'Runtime snapshot',resource_history:'Resource history',search_logs:'Source logs',compare_baseline:'Baseline comparison',database_pressure:'Database metrics',dependency_evidence:'Dependency evidence',review_omitted:'Omitted log patterns',historical_episode:'Prior episode',alert_rule_logic:'Alert logic',scrape_discovery:'Target discovery'};
  return ids.map(id => {
    const item = [...(run.checks || []), ...(run.context?.evidence || [])].find(item=>item.id === id);
    if (!item) return '<span class="source-unavailable">Reference ' + safe(id) + ' unavailable in this capture</span>';
    const log = item?.domain === 'log_template';
    const prior = item?.tool === 'historical_episode' ? item.result?.episode?.reference : '';
    const logTitle = log ? logLabel(item.title).replace(/^(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL):\s*/i,'') : '';
    const label = prior ? 'Prior episode ' + prior : checks[item?.tool] || (log ? 'Logs: ' + (logTitle.length > 72 ? logTitle.slice(0, 69).trimEnd() + '…' : logTitle) : item?.title) || item?.question || id;
    const occurrence = (labels.get(label) || 0) + 1; labels.set(label,occurrence);
    const kind = log || ['search_logs','review_omitted'].includes(item.tool) ? 'logs'
      : item.domain === 'configuration' || item.tool === 'workload_state' ? 'configuration'
        : item.domain === 'metric_anomaly' || ['resource_history','compare_baseline','database_pressure'].includes(item.tool) ? 'performance'
          : item.domain === 'image_evidence' ? 'image' : item.domain === 'audio_evidence' ? 'audio'
          : item.tool === 'historical_episode' ? 'history' : 'observation';
    const iconName = {logs:'logs',configuration:'file-code-2',performance:'chart-no-axes-combined',image:'scan-line',audio:'activity',history:'layers',observation:'bell-ring'}[kind];
    const disambiguator = occurrence > 1 ? ' · ' + (item.time_range?.start ? shortTime(item.time_range.start) : String(occurrence)) : '';
    return '<button class="evidence-link" data-investigation-ref="' + safe(id) + '" title="Open retained ' + kind + ' evidence">' + icon(iconName) + '<span>' + safe(label + disambiguator) + '</span></button>';
  }).join('');
}

function investigationScope(payload) {
  const scope = payload.investigation?.context?.scope || {...payload.report?.incident,...payload.incident};
  const resource = scope.pod || scope.resource?.name || scope.service;
  const recurrence = payload.investigation?.context?.recurrence;
  const parts = [[scope.pod ? 'Pod' : scope.resource?.kind || 'Service',resource],['Namespace',scope.namespace]];
  if (recurrence?.previous_count > 0) parts.push(['Retained history',recurrence.previous_count + (recurrence.count_capped ? '+' : '') + ' earlier same-signature episode' + (recurrence.previous_count === 1 ? '' : 's')]);
  return '<dl class="assessment-scope">' + parts.filter(([,value])=>value).map(([label,value])=>'<div><dt>' + safe(label) + '</dt><dd>' + safe(value) + '</dd></div>').join('') + '</dl>';
}

function checkObservation(item) {
  const result = item.result || {};
  if (item.status === 'running') return 'Waiting for the source response.';
  if (item.status !== 'completed') return String(item.error || result.error || 'No successful observation was retained.').slice(0,300);
  if (result.error) return String(result.error).slice(0,300);
  if (result.summary || result.message) return String(result.summary || result.message).slice(0,300);
  const rule = result.retained_definitions?.at(-1)?.rule;
  if (rule?.query) return 'Retained condition: ' + String(rule.query).slice(0,230) + ' · must hold for ' + (rule.duration || 0) + 's';
  const metrics = result.observations?.filter(row=>row.metric) || result.affected || [];
  const metric = metrics.find(row=>row.metric && row.max != null);
  if (metric) return `${metric.metric}: ${metric.min ?? '?'} to ${metric.max}${metrics.length > 1 ? ' · ' + metrics.length + ' metric series retained' : ''}`;
  const patterns = result.patterns || result.observations?.filter(row=>row.pattern) || [];
  if (patterns.length) {
    const salient = patterns.find(row=>/ERROR|FATAL|WARN|timeout|failed/i.test(row.pattern)) || patterns[0];
    return patterns.length + ' log patterns · ' + logLabel(salient.pattern);
  }
  if (Array.isArray(result.patterns) && !result.patterns.length) return 'No matching log patterns were returned in the queried window.';
  const configs = result.observations?.filter(row=>row.kind) || [];
  if (configs.length) return configs.slice(0,2).map(row=>[row.kind,row.name,row.phase].filter(Boolean).join(' · ')).join('; ');
  if (result.episode) return 'Earlier episode ' + (result.episode.reference || result.episode.episode_id) + ' retained for comparison; not proof of the same cause.';
  const targets = result.targets || result.active_targets;
  if (Array.isArray(targets)) return targets.length ? targets.length + ' targets returned; inspect their observed state.' : 'No matching targets returned.';
  return Object.keys(result).length ? 'Source response retained. Open the observation for its details.' : 'No observations returned.';
}

function investigationProgress(run = {}, compact = false) {
  const checks = run.checks || [];
  const usage = run.usage;
  const budget = run.token_budget || {};
  const tokenText = usage ? (usage.complete ? '' : 'At least ') + Number(usage.total_tokens || 0).toLocaleString('en') + (run.status === 'running' ? ' reported tokens' : ' tokens') : 'Usage pending';
  const rows = checks.map(item => '<li class="agent-step ' + safe(item.status) + '"><span class="step-marker" aria-hidden="true">' + (item.status === 'running' ? '<span class="spinner"></span>' : icon(item.status === 'completed' ? 'check' : 'bell-ring')) + '</span><div><strong>' + safe(item.question) + '</strong><p class="check-answer">' + safe(checkObservation(item)) + '</p><button class="evidence-link" data-investigation-ref="' + safe(item.id) + '">' + icon('chevron-right') + ' Open observation</button><small>' + safe(item.status) + (item.finished_at ? ' · ' + formatDate(item.finished_at) : '') + '</small></div></li>').join('');
  const details = '<dl class="coverage-details"><div><dt>Input tokens</dt><dd>' + Number(usage?.prompt_tokens || 0).toLocaleString('en') + '</dd></div><div><dt>Output tokens</dt><dd>' + Number(usage?.completion_tokens || 0).toLocaleString('en') + '</dd></div><div><dt>Safety reserve</dt><dd>' + Number(budget.accounted_total_tokens || 0).toLocaleString('en') + (budget.maximum_total_tokens ? ' / ' + Number(budget.maximum_total_tokens).toLocaleString('en') : '') + '</dd></div><div><dt>Budget remaining</dt><dd>' + Number(budget.remaining_tokens || 0).toLocaleString('en') + '</dd></div><div><dt>Model calls</dt><dd>' + (run.calls?.length || 0) + '</dd></div><div><dt>Earlier attempts</dt><dd>' + Number(run.lifetime_usage?.total_tokens || 0).toLocaleString('en') + ' tokens</dd></div></dl><p class="queue-note">' + (usage?.complete ? 'Provider-reported usage is shown alongside a pre-call safety reserve.' : 'Provider usage is incomplete; the safety reserve prevents unbounded follow-up calls.') + '</p>';
  const current = checks.find(item => item.status === 'running');
  const running = ['queued','running','waiting'].includes(run.status);
  const activity = running ? (current?.question || 'Checking retained evidence') : run.status === 'ready' ? 'Investigation complete' : run.status === 'inconclusive' ? 'No supported cause yet' : 'Investigation needs attention';
  return '<aside class="agent-progress"><div class="activity-heading"><div><h3>Investigation activity</h3><strong>' + safe(activity) + '</strong><small>' + checks.length + ' check' + (checks.length === 1 ? '' : 's') + (run.finished_at ? ' · ' + formatDate(run.finished_at) : '') + '</small></div>' +
    '<div class="activity-actions"><div class="usage-note">' + disclosure('token-usage',tokenText,details) + '</div>' + (['ready','incomplete','inconclusive'].includes(run.status) ? '<button class="icon-button" data-investigate="' + safe(run.episode_id) + '" title="Reassess episode" aria-label="Reassess episode">' + icon('refresh-cw') + '</button>' : '') + '</div></div>' +
    (compact ? disclosure('activity-checks', 'Review checks', rows ? '<ol class="agent-steps">' + rows + '</ol>' : '<p class="queue-note">No checks recorded yet.</p>', checks.length) : '<ol class="agent-steps">' + rows + '</ol>') +
    (run.lifetime_usage?.complete === false ? '<p class="queue-note">Earlier-attempt usage is a lower bound; an interrupted request has no final count.</p>' : '') + '</aside>';
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
  if (!series.length && !patterns.length && !configs.length && !result.episode) body += '<p>' + safe(result.summary || result.message || (Object.keys(result).length ? 'Structured source response retained below.' : 'No observations returned.')) + '</p>';
  return body + '<p class="uncertainty">' + safe(result.limitation || result.comparability || '') + '</p><details class="raw-observation"><summary>Observation JSON</summary><pre class="log-lines">' + safe(JSON.stringify(result,null,2)) + '</pre></details>';
}

function investigationEvidence(run = {}, attachments = []) {
  const cited = new Set([...(run.assessment?.evidence_ids || []), ...(run.assessment?.hypotheses || []).flatMap(item=>item.evidence_ids), ...(run.assessment?.connections || []).flatMap(item=>item.evidence_ids), ...(run.assessment?.historical_comparison?.evidence_ids || [])]);
  const checks = (run.checks || []).map(item=>disclosure('agent-' + item.id,item.question,'<p>' + safe(item.distinguishes) + '</p><p class="queue-note">' + formatDate(item.started_at) + ' · ' + safe(item.status) + '</p>' + investigationResult(item.result,item.id),item.id)).join('');
  const exampleText = examples => examples.map(item => typeof item === 'string' ? item : JSON.stringify(item)).join('\n');
  const evidence = (run.context?.evidence || []).filter(item=>cited.has(item.id)).map(item=> {
    if (['image_evidence','audio_evidence','audio_transcript','operator_context'].includes(item.domain)) {
      const attachment = attachments.find(entry=>entry.attachment_id === item.attachment_id);
      const extraction = attachment?.extraction;
      const facts = (extraction?.content_text ? [extraction.content_text] : extraction?.observations || item.examples || []).map(entry=>typeof entry === 'string' ? entry : entry.fact).filter(Boolean);
      const observed = item.time_range?.observed_at;
      const limitation = extraction?.limitation || item.limitation;
      const content = '<div class="media-observation-meta"><span class="queue-note">' + (item.domain === 'operator_context' ? 'Operator-provided context, not verified telemetry' : item.domain === 'image_evidence' ? 'Image observation' : 'Audio observation') +
        (observed ? ' · observed ' + safe(formatDate(observed)) : ' · observation time unknown') + '</span>' +
        (attachment?.artifact_url ? '<a class="evidence-link" href="' + safe(attachment.artifact_url) + '" target="_blank" rel="noopener">' + icon('scan-line') + 'View original</a>' : '') + '</div>' +
        (facts.length ? '<ul class="media-observation-facts">' + facts.map(fact=>'<li>' + safe(fact) + '</li>').join('') + '</ul>' : '<p>' + safe(item.summary) + '</p>') +
        (limitation ? '<p class="queue-note">' + safe(limitation) + '</p>' : '') +
        (item.operator_context?.correction ? '<p><strong>Operator correction:</strong> ' + safe(item.operator_context.correction) + '</p>' : '') +
        disclosure('extraction-' + item.id, 'Full extraction', '<pre class="log-lines">' + safe(JSON.stringify(extraction || item, null, 2)) + '</pre>');
      return disclosure('agent-' + item.id, item.title, content);
    }
    const provenance = (item.provenance || []).map(ref=>safe(ref.incident_id)).filter(Boolean);
    return disclosure('agent-' + item.id, item.domain === 'log_template' ? logLabel(item.title) : item.title,
    '<p>' + safe(item.summary) + '</p><p class="queue-note">' + safe(item.domain) + ' · ' + formatDate(item.time_range?.start) + '</p>' +
    (item.examples?.length ? '<pre class="log-lines">' + safe(exampleText(item.examples)) + '</pre>' : '') +
    (item.configuration || item.alert ? '<pre class="log-lines">' + safe(JSON.stringify(item.configuration || item.alert,null,2)) + '</pre>' : '') +
    (provenance.length ? '<small>Captured in ' + provenance.join(', ') + '</small>' : ''));
  }).join('');
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
function metricChart(item, linkToEvidence = false) {
  const points = item.values || [];
  const start = points[0]?.timestamp; const end = points[points.length - 1]?.timestamp;
  const duration = start && end ? Math.round((new Date(end) - new Date(start)) / 60000) : 0;
  if (item.signal_origin === 'alert_rule') {
    const labels = Object.entries(item.labels || {}).filter(([key])=>['namespace','pod','service'].includes(key)).map(([key,value])=>key + '=' + value).join(' · ');
    const replica = item.labels?.prometheus_replica || item.labels?.replica || 'not labelled';
    const unit = item.unit && item.unit !== 'state' ? ' ' + item.unit : '';
    const missing = points.filter(point=>point.value == null).length;
    return '<article class="metric-chart alert-signal"' + (linkToEvidence ? '' : ' id="evidence-' + safe(item.evidence_id) + '" tabindex="-1"') + '><span class="text-label">Alert signal · replica ' + safe(replica) + '</span><h4>' + safe(item.metric === 'up' ? 'Target scrape health' : item.label) + '</h4><p class="queue-note">' + safe(labels || item.component || 'Rule result scope') + '</p>' +
      '<div class="pm-values"><span>Before alert<b>' + safe(item.baseline || 'Unavailable') + '</b></span><span>Selected value<b>' + safe(item.peak) + '</b></span><span>Alert condition<b>' + safe(item.operator + ' ' + item.threshold + unit) + '</b></span></div>' + sparkline(item) +
      '<div class="chart-times"><time title="' + safe(formatExactDate(start)) + '">' + shortTime(start) + '</time><span>' + duration + ' min window</span><time title="' + safe(formatExactDate(end)) + '">' + shortTime(end) + '</time></div><div class="chart-legend">' + (item.metric === 'up' ? '<span>0: failed scrape · 1: successful scrape</span>' : '') + '<span class="threshold-key">Dashed amber: threshold</span><span class="alert-key">Dashed red: alert start ' + safe(shortTime(item.alert_timestamp)) + '</span>' + (missing ? '<span>' + missing + ' missing samples</span>' : '') + '</div>' +
      '<details class="metric-query"><summary>Query and provenance</summary><pre class="log-lines">' + safe(item.expression) + '</pre><p class="queue-note">Original rule: ' + safe(item.rule?.query || item.underlying_expression) + '</p><p class="queue-note">Captured ' + safe(formatExactDate(item.source?.captured_at)) + ' · step ' + safe(item.step_seconds) + 's · values are not filtered by the alert comparison.</p><pre class="log-lines">' + safe(JSON.stringify(item.labels || {},null,2)) + '</pre></details>' +
      (linkToEvidence ? '<button class="evidence-link" data-evidence-link="' + safe(item.evidence_id) + '" data-domain="metrics">Open metric evidence</button>' : '') + '</article>';
  }
  const extreme = item.metric === 'pod_ready' ? 'Selected low' : item.metric.endsWith('_total') ? 'Selected increase' : 'Selected deviation';
  const baselinePeriod = item.baseline_start
    ? ({pre_alert:'Before alert',split_window:'Split-window reference',single_sample:'Single-sample reference'}[item.baseline_basis] || 'Reference window') + ' · ' + shortTime(item.baseline_start) + '–' + shortTime(item.baseline_end)
    : 'Reference period unavailable in this capture';
  return '<article class="metric-chart"' + (linkToEvidence ? '' : ' id="evidence-' + safe(item.evidence_id) + '" tabindex="-1"') + '><h4>' + safe(item.label) + '</h4><p class="queue-note">' + safe(item.meaning) + '</p>' +
    '<div class="pm-values"><span>Baseline median<b>' + safe(item.baseline) + '</b><small title="' + safe(formatExactDate(item.baseline_start)) + ' to ' + safe(formatExactDate(item.baseline_end)) + '">' + safe(baselinePeriod) + '</small></span><span>' + extreme + '<b>' + safe(item.peak) + '</b></span></div>' +
    sparkline(item) + '<div class="chart-times"><time title="' + safe(formatExactDate(start)) + '">' + shortTime(start) + '</time><span>' + duration + ' min captured</span><time title="' + safe(formatExactDate(end)) + '">' + shortTime(end) + '</time></div><small class="queue-note">' + safe(item.component || 'Affected entity not recorded') + ' · dashed marker: ' + (points.some(point => point.timestamp === item.alert_timestamp) ? 'selected deviation' : 'window extreme') + ', not alert time</small>' +
    (linkToEvidence ? '<button class="evidence-link" data-evidence-link="' + safe(item.evidence_id) + '" data-domain="metrics">Open metric evidence</button>' : '') + '</article>';
}
function metricsPanel(report) {
  const unavailable = (report.alert_metric_evidence || []).filter(item=>item.status !== 'available');
  const coverage = unavailable.length ? '<p class="queue-note">Alert signal unavailable: ' + unavailable.map(item=>safe(item.alertname || 'rule') + ' (' + safe(String(item.reason || 'not captured').replaceAll('_',' ')) + ')').join('; ') + '.</p>' : !(report.pm_signals || []).some(item=>item.signal_origin === 'alert_rule') ? '<p class="queue-note">The alert-condition signal was not retained in this capture. Historical captures are not backfilled.</p>' : '';
  return coverage + '<div class="metrics-grid">' + (report.pm_signals || []).map(item => metricChart(item)).join('') + '</div>' + (report.pm_coverage_note ? '<p class="queue-note">' + safe(report.pm_coverage_note) + '</p>' : '');
}
function overviewMetrics(report) {
  const supported = new Set((report.primary_hypothesis?.supporting_evidence || []).map(item => item.evidence_id));
  const chosen = (report.pm_signals || []).filter(item => (item.signal_origin === 'alert_rule' || supported.has(item.evidence_id) && item.component) && (item.values || []).length >= 2).sort((a,b)=>Number(b.signal_origin === 'alert_rule') - Number(a.signal_origin === 'alert_rule')).slice(0, 2);
  return chosen.length ? '<section class="overview-metrics"><h3>Relevant performance</h3><div class="metrics-grid">' + chosen.map(item => metricChart(item, true)).join('') + '</div></section>' : '';
}
function evidencePanel(report) {
  const capture = report.incident || {};
  const captureContext = '<p class="capture-context"><strong>Selected capture</strong>' + safe([capture.service, capture.namespace].filter(Boolean).join(' · ')) + (capture.started_at ? ' · ' + safe(formatDate(capture.started_at)) : '') + '</p>';
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
  return '<div class="evidence-view">' + captureContext +
    disclosure('domain-alerts', 'Alerts', alertHtml || '<p>No fault alert records were retained for this capture.</p>', report.fault_alerts?.length || 0) +
    disclosure('domain-logs', 'Log evidence', logs || '<p>No diagnostic log patterns were selected for this capture.</p>', report.log_patterns?.length || 0) +
    disclosure('domain-metrics', 'Performance', metricsPanel(report), report.pm_signals?.length || 0) +
    disclosure('domain-config', 'Configuration', config || '<p>No configuration snapshot was available in this capture window.</p>', report.configuration_evidence?.length || 0) +
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
    (['ready','incomplete'].includes(payload.investigation?.status) ? link('episode_investigation.json','Latest investigation',null) + link('investigation_revisions.json','Investigation revisions',null) : '') +
    '<p>Capsule archive and report refer to selected capture ' + safe(payload.report.incident.reference || payload.report.incident.incident_id) + '. Investigation files cover the episode and may include several alert captures. This is not a full telemetry backup.</p>' +
    (storage.expires_at ? '<p><strong>Eligible for cleanup ' + formatDate(storage.expires_at) + '</strong>' + storage.retention_days + '-day retention, including archived incidents.</p>' : '') +
    (storage.directory ? '<details><summary>Storage location</summary><code>' + safe(storage.directory) + '</code></details>' : '') + '</div></details>';
}
function reportPanel(payload) {
  if (!payload.report) return '<section class="report-empty"><h3>Evidence captured</h3><p>The report is ' + (lastState?.running ? 'being prepared.' : 'not built yet.') + '</p><button data-build-capsule="' + safe(payload.incident.incident_id) + '" ' + (lastState?.running ? 'disabled' : '') + '>Build report</button></section>';
  const report = payload.report;
  const incident = report.incident;
  const exports = capsuleExport(payload);
  const specialists = mediaEvidenceAvailability();
  const evidenceAction = specialists.text
    ? '<button class="secondary" data-add-evidence="' + safe(selectedEpisodeId || '') + '">' + icon('paperclip') + ' Add context</button>'
    : '<a class="button-link" href="/settings#evidence-models" title="Validate the core and specialist models in Settings to attach image or audio evidence">Evidence setup</a>';
  const ai = payload.investigation;
  const fallback = ai?.status !== 'ready' ? '<section class="observed-summary"><h3>Alert context</h3><p>' + safe(report.fault_alerts?.[0]?.description || incident.summary) + '</p></section>' : '';
  const d = report.engineering_diagnostics;
  const diagnostics = disclosure('diagnostics', 'Engineering diagnostics', '<dl class="coverage-details"><div><dt>Selected evidence</dt><dd>' + d.selected_evidence + '</dd></div><div><dt>Log reduction</dt><dd>' + pct(d.log_compression_ratio) + '</dd></div><div><dt>Signal preservation</dt><dd>' + pct(d.important_signal_preservation) + '</dd></div><div><dt>Grounding</dt><dd>' + pct(d.hypothesis_grounding_score) + '</dd></div><div><dt>Runtime</dt><dd>' + Number(d.runtime_seconds || 0).toFixed(2) + 's</dd></div></dl><p class="queue-note">Rule-based hypothesis: ' + safe(report.primary_hypothesis.statement) + '</p>');
  const tabs = ['overview','investigation','evidence','timeline'];
  let content;
  if (reportTab === 'evidence') content = (sourceReturn?.episodeId === selectedEpisodeId ? '<div class="source-return"><button type="button" data-return-source>Back to ' + safe(sourceReturn.tab) + '</button></div>' : '') + investigationEvidence(ai || {}, payload.media_evidence || []) + '<h3 class="capture-heading">Selected alert capture</h3>' + evidencePanel(report);
  else if (reportTab === 'timeline') content = timelinePanel(report) + investigationTimeline(ai || {});
  else if (reportTab === 'investigation') content = '<div class="overview-layout">' + investigationProgress(ai || {}) + briefingPanel(payload, true) + mediaEvidencePanel(payload) + sourceReviewPanel(payload) + '</div>';
  else content = '<div class="overview-layout"><div>' + investigationScope(payload) + briefingPanel(payload) + fallback + (ai?.assessment ? overviewMetrics(report) : '') + mediaEvidencePanel(payload, false) + '</div>' + investigationProgress(ai || {}, true) + '</div>';
  return '<section id="incident-report"><div class="report-navigation"><div role="tablist" aria-label="Investigation views">' +
    tabs.map(name=>'<button id="tab-' + name + '" role="tab" data-report-tab="' + name + '" aria-selected="' + (name === reportTab) + '" aria-controls="investigation-panel" tabindex="' + (name === reportTab ? 0 : -1) + '">' + name[0].toUpperCase() + name.slice(1) + '</button>').join('') +
    '</div><div class="report-actions">' + evidenceAction + exports + '</div></div><div id="investigation-panel" role="tabpanel" aria-labelledby="tab-' + reportTab + '" tabindex="0">' + content + '</div><div class="report-technical">' + diagnostics + '</div></section>';
}

function renderPatterns(state) {
  lastPatternsSignature = JSON.stringify([state.overview.patterns, state.overview.related_groups, patternsMode]);
  const openRows = new Set([...document.querySelectorAll('.related-group[open], .pattern-older[open]')].map(node => node.id));
  const focusedId = document.activeElement?.id;
  const applications = new Map(state.overview.applications.map(item => [item.app_id, item]));
  const patterns = state.overview.patterns || [];
  const rows = patterns.map(pattern => {
    const application = applications.get(pattern.app_id);
    const target = resourceLabel({app_id:pattern.app_id, resource:pattern.resource}, application);
    const interval = intervalLabel(pattern.observed_interval_seconds, pattern.occurrence_count);
    const recent = (pattern.episodes || []).slice(0, 3);
    const older = (pattern.episodes || []).slice(3);
    const episodeLink = episode => `<a href="/console?episode=${encodeURIComponent(episode.episode_id)}"><strong>${safe(episode.reference)}</strong><time title="${safe(formatExactDate(episode.started_at))}">${safe(relativeTime(episode.started_at))}</time>${icon('chevron-right')}</a>`;
    return `<article class="pattern-row"><div class="pattern-summary"><div class="pattern-main"><h2>${safe(pattern.title)}</h2><p>${safe(target)} · ${safe(application?.namespace || '')}</p><span class="pattern-reference">${safe(pattern.pattern_id)}</span></div><dl class="pattern-stats"><div><dt>Occurrences</dt><dd>${safe(pattern.occurrence_count)}</dd></div><div><dt>Last seen</dt><dd title="${safe(formatExactDate(pattern.last_seen_at))}">${safe(relativeTime(pattern.last_seen_at))}</dd></div><div><dt>Observed gap</dt><dd>${safe(interval || 'Insufficient observations')}</dd></div></dl></div><div class="pattern-episodes"><span>Recent episodes</span>${recent.map(episodeLink).join('')}${older.length ? `<details class="pattern-older" id="older-${safe(pattern.pattern_id)}"><summary>Show ${older.length} more</summary>${older.map(episodeLink).join('')}</details>` : ''}<small>First seen ${safe(formatDate(pattern.first_seen_at))}</small></div></article>`;
  }).join('');
  const groups = state.overview.related_groups || [];
  app.innerHTML = `<div class="page-head"><div><div class="eyebrow">Incident history</div><h1>Patterns</h1><p>Repeated episodes and possible shared operating conditions in retained history.</p></div></div><div class="patterns-view" role="group" aria-label="Pattern view"><button type="button" data-patterns-view="recurring" aria-pressed="${patternsMode === 'recurring'}">Recurring issues</button><button type="button" data-patterns-view="shared" aria-pressed="${patternsMode === 'shared'}">Shared conditions${groups.length ? ` (${groups.length})` : ''}</button></div>${patternsMode === 'recurring' ? `<section class="patterns"><div class="sheet-head"><h2>Recurring issues</h2><span class="queue-note">${patterns.length} recurring pattern${patterns.length === 1 ? '' : 's'}</span></div>${rows || '<div class="empty">No recurring patterns in retained history.</div>'}</section>` : relatedActivity(groups)}`;
  document.querySelectorAll('.related-group, .pattern-older').forEach(node => { if (openRows.has(node.id)) node.open = true; });
  if (focusedId) document.getElementById(focusedId)?.focus({preventScroll:true});
  document.querySelectorAll('[data-patterns-view]').forEach(button => button.addEventListener('click', () => {
    patternsMode = button.dataset.patternsView;
    history.replaceState(null, '', patternsMode === 'shared' ? '/patterns?view=shared' : '/patterns');
    renderPatterns(lastState);
  }));
  document.querySelectorAll('[data-related-open]').forEach(button => button.addEventListener('click', () => {
    location.assign('/console?episode=' + encodeURIComponent(button.dataset.relatedOpen));
  }));
  document.querySelectorAll('[data-related-separate]').forEach(button => button.addEventListener('click', () => {
    separateRelatedEpisode(button.dataset.relatedGroup, button.dataset.relatedSeparate);
  }));
  if (pendingSharedAnchor && patternsMode === 'shared' && location.hash.startsWith('#shared-')) {
    const group = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (group && !group.open) group.open = true;
    group?.scrollIntoView({block:'start'});
    pendingSharedAnchor = false;
  }
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
          document.getElementById('toggle-' + episode.episode_id)?.scrollIntoView({block:'start'});
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
    } else if (view === 'patterns' && JSON.stringify([state.overview.patterns, state.overview.related_groups, patternsMode]) !== lastPatternsSignature) {
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
