const view = location.pathname.startsWith('/settings') ? 'settings' : location.pathname.startsWith('/targets') ? 'targets' : location.pathname.startsWith('/patterns') ? 'patterns' : (location.pathname.startsWith('/estima') || location.pathname.startsWith('/atlas')) ? 'estima' : 'console';
const app = document.querySelector('#app');
const viewLabels = {console:'Operations', targets:'Targets', patterns:'Patterns', estima:'Estima', settings:'Settings'};
const bootLabel = document.querySelector('#boot-label');
if (bootLabel) bootLabel.textContent = `Loading ${viewLabels[view]}`;
const bootTitle = document.querySelector('#boot-title');
if (bootTitle) bootTitle.textContent = viewLabels[view];
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
document.title = viewLabels[view] + ' | FCAPSule';

document.querySelector('.product-bar nav')?.addEventListener('click', event => {
  const link = event.target.closest('a[data-nav]');
  if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target || link.hasAttribute('download')) return;
  const destination = new URL(link.href, location.href);
  if (destination.origin !== location.origin || destination.href === location.href) return;
  event.preventDefault();
  document.body.classList.add('navigation-pending');
  link.classList.add('is-pending');
  app.setAttribute('aria-busy', 'true');
  requestAnimationFrame(() => requestAnimationFrame(() => location.assign(destination.href)));
});
window.addEventListener('pageshow', event => {
  if (event.persisted) {
    document.body.classList.remove('navigation-pending');
    document.querySelector('.product-bar nav .is-pending')?.classList.remove('is-pending');
    app.removeAttribute('aria-busy');
  }
});
let lastState = null;
let selectedReport = null;
let selectedEpisodeId = null;
let showArchived;
let reportTab = 'overview';
let evidenceView = 'captured';
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
let atlasPatterns = [];
let atlasCases = [];
let atlasResultStatus = 'pending';
let atlasMessage = '';
let atlasPatternError = '';
let atlasCaseError = '';
let atlasCasesSearched = false;
let atlasRequestSequence = 0;
let atlasQuery = new URLSearchParams(location.search).get('q') || '';
let atlasScope = new URLSearchParams(location.search).get('scope') || '';
let atlasSelectedPattern = new URLSearchParams(location.search).get('pattern') || '';
let atlasSelectedCase = new URLSearchParams(location.search).get('case') || '';
let atlasDetail = null;
const openDisclosures = new Set();
const closedNamespaces = new Set();

function rememberDisclosures() {
  document.querySelectorAll('details[data-disclosure]').forEach(node => {
    if (node.open) openDisclosures.add(node.dataset.disclosure);
    else openDisclosures.delete(node.dataset.disclosure);
  });
}

function renderPreservingFocus(render) {
  const focused = document.activeElement;
  const focusId = focused?.id;
  const selection = focused instanceof HTMLInputElement && focused.type === 'text' ? [focused.selectionStart, focused.selectionEnd] : null;
  const anchorId = selectedEpisodeId ? 'toggle-' + selectedEpisodeId : null;
  const anchorTop = anchorId ? document.getElementById(anchorId)?.getBoundingClientRect().top : null;
  rememberDisclosures();
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
function disclosure(id, title, content, count = '', statusLabel = '') {
  const domainIcon = {'domain-alerts':'bell-ring','domain-logs':'logs','domain-metrics':'chart-no-axes-combined','domain-config':'file-code-2','domain-coverage':'layers','diagnostics':'settings-2'}[id];
  return `<details class="evidence-disclosure" data-disclosure="${safe(id)}" ${openDisclosures.has(id) ? 'open' : ''}><summary id="disclosure-${safe(id)}"><span class="disclosure-title">${domainIcon ? icon(domainIcon) : ''}<span>${safe(title)}</span></span>${statusLabel ? `<span class="sr-only">: ${safe(statusLabel)}</span>` : ''}${count !== '' ? `<span class="detail-count">${safe(count)}</span>` : ''}</summary><div class="disclosure-body">${content}</div></details>`;
}
function inlineDisclosure(id, title, content, count = '') {
  return `<details class="inline-disclosure" data-disclosure="${safe(id)}" ${openDisclosures.has(id) ? 'open' : ''}><summary id="disclosure-${safe(id)}">${safe(title)}${count !== '' ? `<span class="inline-count">${safe(count)}</span>` : ''}</summary><div class="inline-disclosure-body">${content}</div></details>`;
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
  return JSON.stringify([state.overview.episodes, state.overview.archived_episodes, state.overview.related_groups, selectedReport, selectedEpisodeId, showArchived, queueFilters, reportTab, evidenceView, reportLoading, reportError, state.running]);
}
function resourceLabel(item, application) {
  const resource = item.resource || {};
  if (resource.kind === 'node' && resource.name) return 'Node ' + resource.name;
  if (resource.kind === 'pod' && resource.name) return 'Pod ' + resource.name;
  if (resource.kind === 'workload' && resource.name) return 'Workload ' + resource.name;
  if (resource.kind && resource.name && !['application','workload','pod','node'].includes(resource.kind)) return resource.kind.toUpperCase() + ' ' + resource.name;
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
    if (reportTab === 'evidence') evidenceView = 'captured';
    sourceReturn = null;
    renderPreservingFocus(() => renderConsole(lastState));
    document.getElementById('tab-' + reportTab)?.focus({preventScroll:true});
  }));
  document.querySelector('[role="tablist"]')?.addEventListener('keydown', event => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const names = ['overview','investigation','evidence','timeline'];
    reportTab = event.key === 'Home' ? names[0] : event.key === 'End' ? names.at(-1) : names[(names.indexOf(reportTab) + (event.key === 'ArrowRight' ? 1 : names.length - 1)) % names.length];
    if (reportTab === 'evidence') evidenceView = 'captured';
    sourceReturn = null;
    renderPreservingFocus(() => renderConsole(lastState));
    document.getElementById('tab-' + reportTab)?.focus({preventScroll:true});
  });
  document.querySelectorAll('[data-evidence-view]').forEach(button => button.addEventListener('click', () => {
    evidenceView = button.dataset.evidenceView;
    renderPreservingFocus(() => renderConsole(lastState));
    document.getElementById('evidence-view-' + evidenceView)?.focus({preventScroll:true});
  }));
  document.querySelectorAll('[data-evidence-link]').forEach(button => button.addEventListener('click', () => {
    rememberDisclosures();
    sourceReturn = {episodeId:selectedEpisodeId, tab:reportTab, evidenceView, scrollY:window.scrollY, index:[...document.querySelectorAll('[data-evidence-link]')].indexOf(button), kind:'evidence'};
    reportTab = 'evidence';
    evidenceView = 'captured';
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
  bindEvidenceImages(app);
  document.querySelectorAll('[data-media-ref]').forEach(button => button.addEventListener('click', () => {
    rememberDisclosures();
    sourceReturn = {episodeId:selectedEpisodeId, tab:reportTab, evidenceView, scrollY:window.scrollY, index:[...document.querySelectorAll('[data-media-ref]')].indexOf(button), kind:'media'};
    reportTab = 'evidence'; evidenceView = 'sources';
    openDisclosures.add('operator-evidence');
    renderConsole(lastState);
    const target = document.getElementById('attachment-' + button.dataset.mediaRef) || document.getElementById('disclosure-operator-evidence');
    target?.scrollIntoView({block:'center'}); target?.focus({preventScroll:true});
  }));
  document.querySelectorAll('[data-update-evidence-investigation]').forEach(button => button.addEventListener('click', () => updateWithEvidence(button.dataset.updateEvidenceInvestigation)));
  document.querySelectorAll('[data-correct-evidence]').forEach(button => button.addEventListener('click', () => correctEvidence(button.dataset.correctEvidence)));
  document.querySelectorAll('[data-source-review]').forEach(button => button.addEventListener('click', () => askSourceReview(button.dataset.sourceReview)));
  document.querySelectorAll('[data-copy-incident-link]').forEach(button => button.addEventListener('click', () => copyIncidentLink(button)));
  document.querySelectorAll('[data-investigation-ref]').forEach(button => button.addEventListener('click', () => {
    rememberDisclosures();
    sourceReturn = {episodeId:selectedEpisodeId, tab:reportTab, evidenceView, scrollY:window.scrollY, index:[...document.querySelectorAll('[data-investigation-ref]')].indexOf(button), kind:'investigation'};
    reportTab = 'evidence';
    evidenceView = 'sources';
    const id = button.dataset.investigationRef;
    openDisclosures.add('agent-' + id);
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
    evidenceView = previous.evidenceView || 'captured';
    renderConsole(lastState);
    window.scrollTo({top:previous.scrollY, behavior:'instant'});
    const selector = previous.kind === 'evidence' ? '[data-evidence-link]' : previous.kind === 'media' ? '[data-media-ref]' : '[data-investigation-ref]';
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
      <div class="field"><label>Additional resource IDs</label><small>Match alert labels to Kubernetes pod labels. A shared ID can resolve several replicas; pod names still take priority.</small><div id="identity-labels">${(config.identity_labels || []).map(item => identityLabelRow(item)).join('')}</div><button class="secondary" type="button" id="add-identity-label">Add identifier</button></div>
      <div class="field"><label>Grafana alert input</label><small>Optional webhook: <code>/api/webhooks/grafana</code>. Configure a Grafana contact point with a Bearer authorization header. Token: ${config.grafana_webhook_token_configured ? 'configured' : 'not configured (set FCAPSULE_GRAFANA_WEBHOOK_TOKEN)'}. ${config.grafana_last_received_at ? 'Last notification: ' + safe(formatDate(config.grafana_last_received_at)) + '.' : 'No notification received yet.'} Prometheus polling remains available.</small><label class="toggle"><input id="grafana-webhook-enabled" type="checkbox" ${config.grafana_webhook_enabled ? 'checked' : ''} ${config.grafana_webhook_token_configured ? '' : 'disabled'}>Accept Grafana webhook alerts</label></div>
      <div class="form-pair"><div class="field"><label for="poll-interval">Poll interval (seconds)</label><input id="poll-interval" type="number" min="10" max="3600" value="${safe(config.poll_interval_seconds)}"></div><div class="field"><label for="window-minutes">Incident window (minutes)</label><input id="window-minutes" type="number" min="2" max="120" value="${safe(config.incident_window_minutes)}"></div></div>
      <label class="toggle"><input id="source-enabled" type="checkbox" ${config.enabled ? 'checked' : ''}>Poll sources and capture new firing alerts automatically</label>
      <label class="toggle" style="margin-top:8px"><input id="auto-reports" type="checkbox" ${config.auto_build_reports ? 'checked' : ''}>Build a responder report after capture</label>
      <div class="actions"><button id="save-targets">Save settings</button></div>
    </div></section></details>
    <section class="sheet discovery-section"><div class="sheet-head"><h2>Discovery</h2><span class="queue-note" id="discovery-time">${state.sources.last_sync_at ? formatDate(state.sources.last_sync_at) : 'Not synchronized'}</span></div><div class="sheet-body">
      <div class="kpis" style="grid-template-columns:repeat(3,1fr);margin:0"><div class="kpi"><span>Pods visible</span><strong id="pods-visible">${state.sources.pods_visible || 0}</strong></div><div class="kpi"><span>Applications</span><strong id="applications-visible">${state.sources.applications_visible || 0}</strong></div><div class="kpi"><span>Active alerts</span><strong id="active-alerts">${state.sources.active_alerts || 0}</strong></div></div>
      ${state.sources.error ? `<p class="target-error">${safe(state.sources.error)}</p>` : '<p class="queue-note" style="margin-top:12px">Discovery maps Kubernetes pods to Prometheus metrics, OpenSearch logs, and referenced configuration.</p>'}
      ${(state.sources.unmapped_alerts || []).length ? disclosure('unmapped-alerts', (state.sources.unmapped_alerts || []).length + ' alerts could not be mapped', '<div class="pod-list">' + state.sources.unmapped_alerts.map(item=>'<span class="pod-line"><strong>' + safe(item.alertname) + '</strong><small>' + safe([item.namespace,item.source].filter(Boolean).join(' · ')) + '</small></span>').join('') + '</div>') : ''}
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
  document.querySelector('#add-identity-label').addEventListener('click', () => document.querySelector('#identity-labels').insertAdjacentHTML('beforeend', identityLabelRow({name:'',alert_label:'',pod_label:''})));
  document.querySelector('#identity-labels').addEventListener('click', event => { if (event.target.closest('[data-remove-identity]')) event.target.closest('.identity-label-row').remove(); });
  document.querySelector('#test-targets').addEventListener('click', testTargets);
  document.querySelector('#sync-targets').addEventListener('click', syncTargets);
}

function identityLabelRow(item) {
  return `<div class="identity-label-row">
    <label><small>ID</small><input aria-label="Identifier name" placeholder="CNFC" data-id-name value="${safe(item.name || '')}"></label>
    <label><small>Alert label</small><input aria-label="Alert label" placeholder="cnfc" data-id-alert value="${safe(item.alert_label || '')}"></label>
    <label><small>Pod label</small><input aria-label="Pod label" placeholder="telecom.example.com/cnfc" data-id-pod value="${safe(item.pod_label || '')}"></label>
    <button class="secondary" type="button" data-remove-identity aria-label="Remove identifier" title="Remove identifier">${icon('x')}</button></div>`;
}

function renderSettings(state) {
  const ai = state.ai;
  const media = state.media || {provider:'openrouter',api_key_configured:false,vision:{model:'',capability:{}},audio:{model:'',capability:{}}};
  const configuredProviders = Array.isArray(ai.providers) ? ai.providers : [];
  const knownProviders = [{id:'deepseek',label:'DeepSeek'},{id:'openrouter',label:'OpenRouter'}];
  const providers = [...knownProviders.map(item => configuredProviders.find(provider => provider.id === item.id) || item), ...configuredProviders.filter(item => !knownProviders.some(known => known.id === item.id))];
  const providerId = typeof ai.provider === 'string' && ai.provider ? ai.provider : 'deepseek';
  if (!providers.some(item => item.id === providerId)) providers.push({id:providerId,label:providerId});
  const providerLabel = id => providers.find(item => item.id === id)?.label || id;
  const profiles = Array.isArray(ai.models) ? ai.models : [];
  const defaultModels = {deepseek:'deepseek-v4-pro',openrouter:'deepseek/deepseek-v4-pro-0813'};
  const profileIdsFor = id => profiles.filter(item => item.model_id && (item.provider ? item.provider === id : id === providerId)).map(item => item.model_id);
  const enabledModelFor = id => {
    const profile = profiles.find(item => item.provider === id && item.model_id === defaultModels[id] && item.enabled)
      || profiles.find(item => item.provider === id && item.enabled)
      || profiles.find(item => item.provider === id);
    return profile?.model_id || defaultModels[id] || ai.model;
  };
  const optionsFor = id => {
    const ids = profileIdsFor(id);
    if (defaultModels[id]) ids.unshift(defaultModels[id]);
    return [...new Set(ids)].map(model => `<option value="${safe(model)}">${safe(model)}</option>`).join('');
  };
  const options = optionsFor(providerId);
  const activeProviderLabel = providerLabel(providerId);
  const keyState = capability(ai.capability);
  app.innerHTML = `
    <div class="page-head"><div><div class="eyebrow">Runtime configuration</div><h1>Settings</h1><p>Retention, investigation limits, and optional evidence models.</p></div></div>
    <div class="settings-layout"><section class="sheet"><div class="sheet-head"><h2>${icon('archive')}Incident lifecycle</h2><span class="queue-note">Automatic cleanup</span></div><div class="sheet-body">
      <div class="field"><label for="retention-days">Incident retention (days)</label><input id="retention-days" type="number" min="1" max="3650" value="${safe(state.settings.incident_retention_days)}"><small>Active and archived incidents older than this are permanently removed with their managed reports and capsules. Default: 30 days.</small></div>
      ${window.generalSettingsNotice ? `<p class="notice">${safe(window.generalSettingsNotice)}</p>` : ''}
      <div class="actions"><button id="save-general-settings">Save retention</button></div>
    </div></section><section class="sheet"><div class="sheet-head"><h2>${icon('activity')}Episode investigation</h2>${keyState}</div><div class="sheet-body">
      <div class="ai-active-summary" aria-label="Saved investigation provider status"><span><strong>Active provider</strong> ${safe(activeProviderLabel)}</span><span><strong>Model</strong> <code>${safe(ai.model)}</code></span><span><strong>API key</strong> ${ai.api_key_configured ? 'Configured' : 'Not configured'}</span></div>
      <div class="field"><label for="ai-provider">Investigation provider</label><select id="ai-provider">${providers.map(item => `<option value="${safe(item.id)}" ${item.id === providerId ? 'selected' : ''}>${safe(item.label || item.id)}</option>`).join('')}</select></div>
      <div class="field"><label for="ai-model">Model ID</label><input id="ai-model" list="ai-model-options" value="${safe(ai.model)}"><datalist id="ai-model-options">${options}</datalist><small id="ai-provider-hint" aria-live="polite">Selecting a provider loads its enabled/default model. Review or edit the model ID before saving; provider errors never switch providers.</small></div>
      <div class="settings-number-grid"><div class="field"><label for="ai-max-tokens">Output per call</label><input id="ai-max-tokens" type="number" min="256" max="6000" value="${safe(ai.max_tokens)}"></div><div class="field"><label for="ai-total-tokens">Investigation budget</label><input id="ai-total-tokens" type="number" min="4000" max="100000" value="${safe(ai.max_total_tokens)}"></div><div class="field"><label for="ai-prompt-tokens">Input per call</label><input id="ai-prompt-tokens" type="number" min="1600" max="12000" value="${safe(ai.max_prompt_tokens)}"><small>Includes instructions, tool catalogue, and selected evidence.</small></div><div class="field"><label for="ai-max-checks">Additional checks</label><input id="ai-max-checks" type="number" min="0" max="4" value="${safe(ai.max_checks)}"></div></div>
      <div class="field"><label for="ai-key">${safe(activeProviderLabel)} API key</label><input id="ai-key" type="password" autocomplete="new-password" placeholder="${ai.api_key_configured ? 'Leave blank to keep this provider key' : 'Paste a key to enable investigation'}"><small id="ai-key-hint">This key belongs to the selected investigation provider. Leave blank to keep that provider's configured key; validate separately. Provider errors do not switch providers.</small></div>
      ${window.settingsNotice ? `<p class="notice">${safe(window.settingsNotice)}</p>` : ''}
      <div class="actions"><button id="save-ai-settings">Save investigation settings</button><button class="secondary" id="validate-ai-settings">Validate model</button></div>
    </div></section><section id="evidence-models" class="sheet"><div class="sheet-head"><h2>${icon('file-code-2')}Evidence models</h2>${capability(media.core_investigator?.capability)}</div><div class="sheet-body">
      <div class="model-state"><span>Image extraction ${capability(media.vision?.capability)}</span><span>Audio transcription ${capability(media.audio?.capability)}</span></div>
      <div class="field"><label for="media-key">OpenRouter API key</label><input id="media-key" type="password" autocomplete="new-password" placeholder="${media.api_key_configured ? 'Leave blank to keep the saved key' : 'Paste an optional key'}"><small>This credential is also used by the investigator when its provider is OpenRouter. Image and audio specialist models require separate validation.</small></div>
      <div class="field"><label for="vision-model">Image model</label><input id="vision-model" value="${safe(media.vision?.model)}"></div>
      <div class="field"><label for="asr-model">Audio model</label><input id="asr-model" value="${safe(media.audio?.model)}"></div>
      ${window.mediaSettingsNotice ? `<p class="notice">${safe(window.mediaSettingsNotice)}</p>` : ''}
      <div class="actions"><button id="save-media-settings">Validate and save media</button><button class="secondary" id="validate-media-settings">Recheck media</button></div>
    </div></section><section id="atlas-connection" class="sheet"><div class="sheet-head"><h2>${icon('network')}Estima connection</h2><span id="atlas-settings-status" class="queue-note" role="status">Loading</span></div><div class="sheet-body">
      <p class="queue-note">Estima stores shared observations and hypotheses. FCAPSule makes every model call and interprets retrieved context; no LLM API keys are sent to Estima. Similarity is a retrieval signal, not a verified cause.</p>
      <div id="atlas-settings-body" aria-live="polite"><div class="empty">Loading Estima configuration...</div></div>
    </div></section></div>`;
  document.querySelector('#save-general-settings').addEventListener('click', saveGeneralSettings);
  document.querySelector('#save-ai-settings').addEventListener('click', saveAiSettings);
  document.querySelector('#validate-ai-settings').addEventListener('click', validateAiSettings);
  document.querySelector('#save-media-settings').addEventListener('click', saveMediaSettings);
  document.querySelector('#validate-media-settings').addEventListener('click', validateMediaSettings);
  document.querySelector('#ai-provider').addEventListener('change', event => {
    const id = event.target.value;
    const label = providerLabel(id);
    document.querySelector('#ai-model').value = enabledModelFor(id);
    document.querySelector('#ai-model-options').innerHTML = optionsFor(id);
    document.querySelector('#ai-provider-hint').textContent = `Loaded ${document.querySelector('#ai-model').value} for ${label}. Review or edit the model ID before saving; provider errors never switch providers.`;
    document.querySelector('#ai-key').placeholder = id === providerId
      ? (ai.api_key_configured ? 'Leave blank to keep this provider key' : 'Paste a key to enable investigation')
      : `Optional key for ${label}; blank keeps that provider's configured key`;
    document.querySelector('label[for="ai-key"]').textContent = `${label} API key`;
  });
  if (typeof loadAtlasSettings === 'function') loadAtlasSettings();
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
    <td>${disclosure('pods-' + item.app_id, (item.source_config?.pods?.length || 0) + ' observed', `<div class="pod-list">${(item.source_config?.pods || []).map(pod=>`<span class="pod-line"><code>${safe(pod.name)}</code>${(pod.identifiers || []).map(id=>`<small>${safe(id.name)} ${safe(id.value)}</small>`).join('')}${status(pod.ready ? 'ready' : pod.phase || 'unknown')}</span>`).join('')}</div>`)}</td>
    <td><div class="source-row">${sources(item.source_config)}</div></td><td>${formatDate(item.last_incident_at)}</td></tr>`).join('')}</tbody></table></div></details>`).join('');
}

async function saveTargets() {
  const identity_labels = [...document.querySelectorAll('.identity-label-row')].map(row => ({name:row.querySelector('[data-id-name]').value,alert_label:row.querySelector('[data-id-alert]').value,pod_label:row.querySelector('[data-id-pod]').value}));
  const payload = {prometheus_url:document.querySelector('#prometheus-url').value, opensearch_url:document.querySelector('#opensearch-url').value, opensearch_index:document.querySelector('#opensearch-index').value, kubernetes_url:document.querySelector('#kubernetes-url').value, cluster_name:document.querySelector('#cluster-name').value, namespaces:document.querySelector('#source-namespaces').value, poll_interval_seconds:Number(document.querySelector('#poll-interval').value), incident_window_minutes:Number(document.querySelector('#window-minutes').value), enabled:document.querySelector('#source-enabled').checked, auto_build_reports:document.querySelector('#auto-reports').checked, grafana_webhook_enabled:document.querySelector('#grafana-webhook-enabled').checked, identity_labels};
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
  if (['overview','investigation'].includes(reportTab) || reportTab === 'evidence' && evidenceView === 'sources') return episodeLine;
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
  if (!keepOpen) { reportTab = 'overview'; evidenceView = 'captured'; sourceReturn = null; openDisclosures.clear(); animateEpisodeId = episodeId; }
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
  if (item.kind === 'text') return extraction.content_text || item.context_note || 'Operator-provided evidence';
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
    return `<article class="media-evidence-row" id="attachment-${safe(item.attachment_id)}" tabindex="-1"><div><strong>${safe(item.filename)}</strong><small>Operator-provided · ${safe(item.kind)} · ${safe(item.status)}${useState ? ' · ' + safe(useState) : ''}${item.uploaded_at ? ' · uploaded ' + safe(formatDate(item.uploaded_at)) : ''}${item.observed_at ? ' · observed ' + safe(formatDate(item.observed_at)) : ' · observation time unknown'}</small>${evidenceArtifact(item)}<p>${safe(attachmentSummary(item))}</p></div><div class="media-evidence-actions">${item.status === 'ready' ? `<button class="secondary" data-correct-evidence="${safe(item.attachment_id)}">Correct</button>` : ''}</div></article>`;
  }).join('');
  const revision = includeHistory ? payload.investigation_revisions || [] : [];
  const history = revision.length > 1 ? disclosure('assessment-history', 'Assessment history', revision.map(item=>`<div class="revision-row"><span>${safe(item.reason.replaceAll('_',' '))}</span><strong>${safe(item.summary?.summary || item.status.replaceAll('_',' '))}</strong><small>${safe(formatDate(item.completed_at || item.created_at))}</small></div>`).join(''), revision.length) : '';
  if (!items.length) return history;
  return disclosure('operator-evidence', 'Additional evidence', `<section class="media-evidence">${ready ? `<div class="row-actions"><button data-update-evidence-investigation="${safe(episodeId || '')}">Review new evidence</button></div>` : ''}${rows}${history}</section>`, items.length);
}

function evidenceArtifact(item) {
  if (!item.artifact_url) return '<span class="queue-note">Original file unavailable</span>';
  const name = item.filename || 'Evidence image';
  const preview = item.kind === 'image' ? `<button type="button" class="evidence-thumbnail" id="preview-${safe(item.preview_id || item.attachment_id || 'composer')}" data-preview-image="${safe(item.artifact_url)}" data-image-name="${safe(name)}" aria-label="View image: ${safe(name)}" title="View image"><img src="${safe(item.artifact_url)}" alt="${safe(name)}" loading="lazy"></button>` : '';
  return `<div class="evidence-artifact">${preview}<a class="evidence-download" href="${safe(item.artifact_url)}" download="${safe(name)}">${icon('download')}Download original</a></div>`;
}

function bindEvidenceImages(root) {
  root.querySelectorAll('[data-preview-image]').forEach(button => button.addEventListener('click', () => showEvidenceImage(button.dataset.previewImage, button.dataset.imageName, button)));
}

function showEvidenceImage(url, name, trigger) {
  const dialog = document.createElement('dialog');
  dialog.className = 'evidence-dialog image-dialog';
  dialog.setAttribute('aria-labelledby', 'image-dialog-title');
  dialog.innerHTML = `<header><h2 id="image-dialog-title">${safe(name || 'Evidence image')}</h2><button type="button" class="icon-button" aria-label="Close image" title="Close image" autofocus>${icon('x')}</button></header><div class="image-dialog-body"><p class="queue-note" role="status">Loading image...</p><img alt="${safe(name || 'Evidence image')}" hidden></div><footer><a class="evidence-download" href="${safe(url)}" download="${safe(name || 'evidence-image')}">${icon('download')}Download original</a></footer>`;
  const img = dialog.querySelector('img');
  const message = dialog.querySelector('[role="status"]');
  img.addEventListener('load', () => { img.hidden = false; message.hidden = true; });
  img.addEventListener('error', () => { img.hidden = true; message.textContent = 'Image unavailable. The retained file may no longer be accessible.'; });
  dialog.querySelector('button').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    dialog.remove();
    const replacement = trigger?.id ? document.getElementById(trigger.id) : null;
    (trigger?.isConnected ? trigger : replacement)?.focus({preventScroll:true});
  });
  document.body.append(dialog); dialog.showModal(); img.src = url;
}

function microphoneAvailability(available, secure, mediaDevices, recorder) {
  if (!available.audio) return {ready:false, hint:'Microphone unavailable: validate the audio model in Settings.'};
  if (!secure) return {ready:false, hint:'Microphone requires trusted HTTPS or localhost. You can still attach an audio file.'};
  if (!mediaDevices?.getUserMedia || !recorder) return {ready:false, hint:'Microphone recording is not supported by this browser. You can still attach an audio file.'};
  return {ready:true, hint:'Record audio, up to 60 seconds. Microphone permission will be requested.'};
}

function sourceReviewPanel(payload) {
  const episodeId = selectedEpisodeId || payload.investigation?.episode_id;
  if (!episodeId) return '';
  const reviews = payload.source_disconnected_reviews || [];
  const rows = reviews.slice(0, 3).map(item => {
    const result = item.result || {};
    const usage = item.usage || {};
    const provider = item.provider === 'deepseek' ? 'DeepSeek' : item.provider === 'openrouter' ? 'OpenRouter' : item.provider;
    const modelText = [provider, item.model].filter(Boolean).join(' · ');
    const tokenText = Number.isFinite(Number(usage.total_tokens)) && Number(usage.total_tokens) > 0
      ? ' · ' + (usage.complete === false ? 'at least ' : '') + Number(usage.total_tokens).toLocaleString('en') + ' tokens'
      : '';
    return `<article class="source-review-row"><div><strong>${safe(item.question)}</strong><small>${safe(String(item.status || '').replaceAll('_',' '))} · ${safe(formatDate(item.completed_at || item.created_at))}${modelText ? ' · ' + safe(modelText) : ''}${tokenText}</small>${result.answer ? `<p>${safe(result.answer)}</p><p class="queue-note">${safe(String(result.sufficiency || '').replaceAll('_',' '))} · ${safe(result.missing_discriminator || '')}</p>` : ''}</div></article>`;
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
  const microphone = microphoneAvailability(available, window.isSecureContext, navigator.mediaDevices, window.MediaRecorder);
  const canRecord = microphone.ready;
  const trigger = document.activeElement;
  const state = {file:null, stream:null, recorder:null, objectUrl:null, timer:null, busy:false, requesting:false, closed:false};
  const dialog = document.createElement('dialog');
  dialog.className = 'evidence-dialog';
  dialog.setAttribute('aria-labelledby','evidence-dialog-title');
  dialog.innerHTML = `<form method="dialog"><header><h2 id="evidence-dialog-title">Add evidence</h2><button class="icon-button" value="cancel" aria-label="Close" title="Close">${icon('x')}</button></header><div class="dialog-body"><div class="context-composer"><div id="evidence-preview" class="evidence-preview" hidden></div><div class="composer-row"><button type="button" class="icon-button" id="attach-image" aria-label="Attach image" title="${available.image ? 'Attach image (up to 6 MiB)' : 'Validate the image model in Settings'}" ${available.image ? '' : 'disabled'}>${icon('image')}</button><label class="sr-only" for="evidence-note">Additional evidence</label><textarea id="evidence-note" maxlength="16000" rows="1" placeholder="Add evidence..."></textarea><button type="button" class="icon-button" id="record-evidence" aria-label="Record audio" aria-describedby="record-status" aria-disabled="${!canRecord}" title="${safe(microphone.hint)}">${icon('mic')}</button></div></div><p id="record-status" class="queue-note microphone-hint" role="status">${safe(microphone.hint)}</p><input id="evidence-file" type="file" hidden><details class="context-metadata"><summary>Source details (optional)</summary><button type="button" class="secondary" id="attach-file">${icon('paperclip')}Attach audio or import text</button><div class="field"><label for="evidence-observed-at">Time of observation</label><input id="evidence-observed-at" type="datetime-local"><small>Unknown when blank; upload time is saved separately.</small></div><label class="toggle"><input id="evidence-redacted" type="checkbox">I have redacted this copy where needed</label></details><p class="queue-note">Operator-provided evidence. Review sensitive content before uploading.</p><p id="evidence-error" class="notice" role="alert" hidden></p></div><footer><button type="button" id="submit-evidence" disabled>Add evidence</button></footer></form>`;
  document.body.append(dialog);
  const fileInput = dialog.querySelector('#evidence-file'); const preview = dialog.querySelector('#evidence-preview');
  const note = dialog.querySelector('#evidence-note'); const error = dialog.querySelector('#evidence-error'); const submit = dialog.querySelector('#submit-evidence'); const record = dialog.querySelector('#record-evidence'); const recordStatus = dialog.querySelector('#record-status');
  const sync = () => {
    const recording = state.recorder?.state === 'recording';
    submit.disabled = state.busy || state.requesting || recording || (!state.file && !note.value.trim());
    submit.textContent = state.busy ? 'Adding...' : 'Add evidence';
    record.disabled = state.busy || state.requesting;
    dialog.querySelector('#attach-image').disabled = !available.image || state.busy || state.requesting || recording;
    dialog.querySelector('#attach-file').disabled = state.busy || state.requesting || recording;
    note.readOnly = state.busy;
    note.style.height = 'auto'; note.style.height = Math.min(180, note.scrollHeight) + 'px';
  };
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
      if (combined.length > 16000) return fail('Text evidence is limited to 16,000 characters. Choose a focused excerpt.');
      note.value = combined; sync(); return;
    }
    if (file) {
      const type = file.type.split(';')[0];
      const isImage = imageTypes.includes(type); const isAudio = audioTypes.includes(type);
      if ((!isImage && !isAudio) || (isImage && !available.image) || (isAudio && !available.audio)) return fail('This file type is unavailable. Validate its model in Settings, or add text evidence.');
      if (file.size > (isImage ? 6 : 8) * 1024 * 1024) return fail(isImage ? 'Images are limited to 6 MiB.' : 'Audio is limited to 8 MiB.');
    }
    if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
    state.file = file || null; preview.hidden = !file; preview.innerHTML = '';
    if (file) {
      state.objectUrl = URL.createObjectURL(file);
      preview.innerHTML = (file.type.startsWith('image/') ? evidenceArtifact({kind:'image', artifact_url:state.objectUrl, filename:file.name}) : `<audio controls src="${safe(state.objectUrl)}"></audio>`) + `<div class="attachment-caption"><span>${safe(file.name)} · ${bytes(file.size)}</span><button type="button" class="icon-button" data-remove-attachment aria-label="Remove attachment" title="Remove attachment">${icon('x')}</button></div>`;
      bindEvidenceImages(preview);
      preview.querySelector('[data-remove-attachment]').addEventListener('click', () => { setFile(null); note.focus(); });
    }
    sync();
  };
  note.addEventListener('input', sync);
  note.addEventListener('paste', event => {
    if (state.busy || state.requesting || state.recorder?.state === 'recording') return;
    const image = [...(event.clipboardData?.items || [])].find(item => item.kind === 'file' && item.type.startsWith('image/'));
    if (image) { event.preventDefault(); setFile(image.getAsFile()); }
  });
  fileInput.addEventListener('change', () => setFile(fileInput.files?.[0]));
  dialog.querySelector('#attach-image').addEventListener('click', () => { fileInput.accept = imageTypes.join(','); fileInput.value = ''; fileInput.click(); });
  dialog.querySelector('#attach-file').addEventListener('click', () => { fileInput.accept = ['.txt','.log',...(available.audio ? audioTypes : [])].join(','); fileInput.value = ''; fileInput.click(); });
  record.addEventListener('click', async () => {
    if (!canRecord || state.busy || state.requesting) return;
    if (state.recorder?.state === 'recording') { state.recorder.stop(); return; }
    try {
      state.requesting = true; recordStatus.textContent = 'Waiting for microphone permission...'; sync();
      const stream = await navigator.mediaDevices.getUserMedia({audio:true});
      if (state.closed) { stream.getTracks().forEach(track => track.stop()); return; }
      state.stream = stream;
      state.requesting = false;
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
    } catch (exception) {
      state.requesting = false; stopTracks();
      const message = exception.name === 'NotAllowedError' ? 'Microphone permission denied. Allow access in your browser or attach an audio file.' : exception.name === 'NotFoundError' ? 'No microphone found. Attach an audio file instead.' : 'Unable to start recording. Check your microphone or attach an audio file.';
      recordStatus.textContent = message; fail(message); sync();
    }
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
  dialog.addEventListener('close', () => {
    state.closed = true; if (state.recorder?.state === 'recording') state.recorder.stop(); stopTracks();
    if (state.objectUrl) URL.revokeObjectURL(state.objectUrl); dialog.remove();
    (trigger?.isConnected ? trigger : document.querySelector('[data-add-evidence]'))?.focus({preventScroll:true});
  });
  dialog.showModal(); sync(); note.focus();
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
  const payload = {provider:document.querySelector('#ai-provider').value, model:document.querySelector('#ai-model').value, max_tokens:Number(document.querySelector('#ai-max-tokens').value), max_total_tokens:Number(document.querySelector('#ai-total-tokens').value), max_prompt_tokens:Number(document.querySelector('#ai-prompt-tokens').value), max_checks:Number(document.querySelector('#ai-max-checks').value), api_key:document.querySelector('#ai-key').value};
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

function atlasSettingsForm(config) {
  const state = !config.url ? 'Not configured' : config.read_enabled ? 'Reads enabled' : 'Reads disabled';
  const pending = config.pending_count == null ? 'Unknown' : fmt.format(config.pending_count);
  const failed = Number(config.failed_count || 0);
  const canRetry = Boolean(config.url && config.token_configured && config.publish_enabled);
  return `<div class="ai-active-summary" aria-label="Estima connection status"><span><strong>Read access</strong> ${safe(state)}</span><span><strong>Service token</strong> ${config.token_configured ? 'Configured' : 'Not configured'}</span><span><strong>Pending publishes</strong> ${pending}</span></div>
    ${failed ? `<div class="atlas-retry-notice" role="status"><div><strong>${fmt.format(failed)} failed publish${failed === 1 ? '' : 'es'}</strong><p>${canRetry ? 'These records remain queued for recovery.' : 'Configure a service URL and token, then enable publishing before retrying.'}</p></div><button id="retry-atlas-failed" class="secondary" type="button" ${canRetry ? '' : 'disabled'}>${icon('refresh-cw')}Retry failed</button></div>` : ''}
    <div class="field"><label for="atlas-url">Estima service URL</label><input id="atlas-url" type="url" value="${safe(config.url || '')}" placeholder="https://estima.example.internal"></div>
    <div class="field"><label for="atlas-instance">Instance ID</label><input id="atlas-instance" value="${safe(config.instance_id || '')}" autocomplete="off"></div>
    <div class="field"><label for="atlas-token">Estima service token</label><input id="atlas-token" type="password" autocomplete="new-password" placeholder="${config.token_configured ? 'Leave blank to keep the saved token' : 'Paste an Estima service token'}"><small>This credential only authenticates FCAPSule to Estima. It is not an LLM key and is never sent back to the browser. Enable publishing only for instances allowed to contribute records.</small></div>
    <label class="toggle"><input id="atlas-read-enabled" type="checkbox" ${config.read_enabled ? 'checked' : ''}>Allow FCAPSule to read Estima patterns and records</label>
    <label class="toggle" style="margin-top:8px"><input id="atlas-publish-enabled" type="checkbox" ${config.publish_enabled ? 'checked' : ''}>Allow FCAPSule to publish eligible records to Estima</label>
    ${config.token_configured ? '<label class="toggle" style="margin-top:8px"><input id="atlas-clear-token" type="checkbox">Remove the saved Estima service token</label>' : ''}
    ${config.last_error ? '<p class="queue-note">Estima reported a recent publish error. Error details are omitted here.</p>' : ''}
    ${window.atlasSettingsNotice ? `<p class="notice" role="status">${safe(window.atlasSettingsNotice)}</p>` : ''}
    <div class="actions"><button id="save-atlas-settings">Save Estima settings</button></div>`;
}

async function loadAtlasSettings() {
  const host = document.querySelector('#atlas-settings-body');
  if (!host) return;
  const status = document.querySelector('#atlas-settings-status');
  try {
    const response = await fetch('/api/settings/estima', {cache:'no-store'});
    const config = await response.json();
    if (!response.ok) throw new Error(config.error || 'Unable to load Estima settings.');
    host.innerHTML = atlasSettingsForm(config);
    status.textContent = !config.url ? 'Not configured' : config.read_enabled ? 'Read enabled' : 'Read disabled';
    document.querySelector('#save-atlas-settings').addEventListener('click', saveAtlasSettings);
    document.querySelector('#retry-atlas-failed')?.addEventListener('click', retryAtlasPublications);
  } catch (error) {
    status.textContent = 'Unavailable';
    host.innerHTML = `<p class="target-error" role="alert">${safe(error.message || 'Estima settings are unavailable.')}</p>`;
  }
}

async function retryAtlasPublications() {
  const button = document.querySelector('#retry-atlas-failed');
  button.disabled = true;
  try {
    const response = await fetch('/api/estima/retry-failed', {method:'POST'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Unable to retry Estima publishes.');
    window.atlasSettingsNotice = 'Retry requested for failed Estima publishes.';
  } catch (error) {
    window.atlasSettingsNotice = error.message || 'Unable to retry Estima publishes.';
  }
  await loadAtlasSettings();
}

async function saveAtlasSettings() {
  const button = document.querySelector('#save-atlas-settings');
  button.disabled = true;
  const payload = {
    url:document.querySelector('#atlas-url').value.trim(),
    instance_id:document.querySelector('#atlas-instance').value.trim(),
    token:document.querySelector('#atlas-token').value,
    read_enabled:document.querySelector('#atlas-read-enabled').checked,
    publish_enabled:document.querySelector('#atlas-publish-enabled').checked,
    clear_token:document.querySelector('#atlas-clear-token')?.checked || false,
  };
  try {
    const response = await fetch('/api/settings/estima', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Unable to save Estima settings.');
    window.atlasSettingsNotice = 'Estima settings saved. The secret remains masked.';
    await loadAtlasSettings();
  } catch (error) {
    window.atlasSettingsNotice = error.message || 'Unable to save Estima settings.';
    await loadAtlasSettings();
  }
}

async function changeEpisodeState(id, action) {
  const response = await fetch(`/api/episodes/${encodeURIComponent(id)}/${action}`, {method:'POST'});
  const result = await response.json();
  if (!response.ok) { alert(result.error || `Unable to ${action} episode`); return; }
  selectedReport = null; selectedEpisodeId = null; requestSequence++; updateLocation(); await refresh();
}

async function deleteEpisode(id) {
  if (!confirm('Permanently delete this local episode and its reports and capsule? A record already published to Estima will remain there.')) return;
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

function sparkline(signal, width = 380) {
  const values = signal.values || [];
  if (values.length < 2) return '';
  const height = 110; const left = 42; const pad = 8;
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
    const divisor = metric.endsWith('_bytes') ? 1048576 : signal.unit === 'seconds' ? 0.001 : 1;
    return Number(value / divisor).toLocaleString('en',{notation:'compact',maximumSignificantDigits:3}) + (divisor > 1 ? ' MiB' : divisor < 1 ? ' ms' : '');
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
function briefingPanel(payload, detailed = false, evidencePrompt = '') {
  const run = payload.investigation || {status:'not_started', episode_id:selectedEpisodeId};
  const assessment = run.assessment;
  const zeroCallBudget = ['inconclusive','incomplete'].includes(run.status) && run.validation_error === 'Input size budget reached' &&
    Array.isArray(run.calls) && run.calls.length === 0 && (!assessment || assessment.provenance === 'deterministic_abstention');
  const loading = ['queued','running','waiting'].includes(run.status);
  const saved = run.finished_at ? (zeroCallBudget ? 'Last attempt ' : run.status === 'ready' ? 'Assessment ready ' : run.status === 'inconclusive' ? 'Assessment inconclusive ' : 'Last attempt ') + formatDate(run.finished_at) : '';
  const provider = run.provider === 'deepseek' ? 'DeepSeek' : run.provider === 'openrouter' ? 'OpenRouter' : run.provider;
  const header = '<div class="section-heading"><h3>Episode assessment</h3><span class="queue-note">' + safe([provider, run.model, saved].filter(Boolean).join(' · ')) + '</span></div>';
  const atlasHtml = typeof relatedAtlasCases === 'function' ? relatedAtlasCases(run) : '';
  if (zeroCallBudget) return '<section class="briefing">' + header + '<div class="analysis-state" role="status" aria-live="polite"><div><strong>Prompt budget exceeded before any model call</strong>' +
    '<p>Retained evidence and source checks remain available. Review the prompt budget before reassessing.</p>' +
    '<button class="secondary" data-investigate="' + safe(run.episode_id) + '">Reassess episode</button>' + evidencePrompt + '</div></div>' + atlasHtml + '</section>';
  if (detailed && !assessment) return atlasHtml ? '<section class="investigation-details">' + atlasHtml + '</section>' : '';
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
    (!loading ? run.status === 'not_configured' ? '<a href="/settings">Open Settings</a>' : '<button class="secondary" data-investigate="' + safe(run.episode_id) + '">' + (run.status === 'not_started' ? 'Start investigation' : 'Reassess episode') + '</button>' : '') + '</div></div>' + atlasHtml + '</section>';
  const hypotheses = (assessment.hypotheses || []).map(item => '<article class="hypothesis-row"><div class="section-heading"><h4>' + safe(item.explanation) + '</h4><span class="hypothesis-state ' + safe(item.status) + '">' + safe(item.status) + '</span></div><p>' + investigationText(run, item.reason) + '</p><div class="citations">' + investigationRefs(run, item.evidence_ids) + '</div></article>').join('');
  const name = id => run.context?.alerts?.find(item=>item.incident_id === id)?.title || id;
  const connections = (assessment.connections || []).map(item=>'<article class="hypothesis-row"><h4>' + safe(name(item.from)) + ' / ' + safe(name(item.to)) + '</h4><small>' + safe(item.relationship.replaceAll('_',' ')) + '</small><p>' + investigationText(run, item.reason) + '</p><div class="citations">' + investigationRefs(run,item.evidence_ids) + '</div></article>').join('');
  const history = assessment.historical_comparison;
  const historyCandidate = run.context?.historical_candidates?.find(item => item.episode_id === history?.episode_id);
  const historyHtml = history ? '<section class="history-comparison"><div><span class="text-label">Related history</span><h4>' + safe(history.status.replaceAll('_',' ')) + '</h4><p>' + investigationText(run, history.summary) + '</p></div><div><span class="incident-reference">' + safe(historyCandidate?.reference || history.episode_id) + '</span><div class="citations">' + investigationRefs(run, history.evidence_ids) + '</div></div></section>' : '';
  const same = (left, right) => String(left || '').trim().replace(/[.\s]+$/,'').toLowerCase() === String(right || '').trim().replace(/[.\s]+$/,'').toLowerCase();
  const assessorFinding = (run.findings || []).find(item => item.category === 'investigator_assessment');
  const primary = assessorFinding || run.findings?.[0];
  const mechanism = assessment.likely_mechanism || primary?.summary || '';
  const mainIds = [...new Set([...(assessment.evidence_ids || []), ...(primary && same(primary.summary, mechanism) ? primary.evidence_ids || [] : [])])];
  const inlineIds = new Set(investigationReferenceMatches(run, assessment.basis).filter(item=>!item.reference.unresolved).map(item=>item.id));
  const extraIds = mainIds.filter(id=>!inlineIds.has(id));
  const discrepancy = assessorFinding && mechanism && !same(assessorFinding.summary, mechanism)
    ? '<section class="assessment-discrepancy" role="note"><strong>Assessment discrepancy</strong><p>The retained initial finding differs from the latest mechanism. Verify both before acting.</p><dl><div><dt>Latest mechanism</dt><dd>' + investigationText(run, mechanism) + '</dd></div><div><dt>Initial finding</dt><dd>' + investigationText(run, assessorFinding.summary) + '</dd></div></dl><div class="citations">' + investigationRefs(run, assessorFinding.evidence_ids) + '</div></section>' : '';
  const seenObservations = new Set();
  const observations = (run.findings || []).filter(item => {
    if (item === assessorFinding && discrepancy) return false;
    const key = String(item.summary || '').trim().toLowerCase();
    if (!key || same(key, mechanism) || same(key, assessment.summary) || seenObservations.has(key)) return false;
    seenObservations.add(key);
    return true;
  });
  const briefObservations = observations.map(item => '<li><strong>' + safe(item.title) + '</strong><p>' + investigationText(run, item.summary) + '</p><div class="citations">' + investigationRefs(run, item.evidence_ids) + '</div></li>').join('');
  const findingDetails = (run.findings || []).map(item => '<article class="finding"><h4>' + safe(item.title) + '</h4><p>' + investigationText(run, item.summary) + '</p><div class="citations">' + investigationRefs(run, item.evidence_ids) + '</div>' +
    (item.next_check && !same(item.next_check, assessment.next_action) ? '<p><strong>Additional check:</strong> ' + investigationText(run, item.next_check) + '</p>' : '') +
    (item.observations?.length ? disclosure('finding-' + safe(item.id), 'Observed details', item.observations.map(observation => '<pre class="compact-data">' + safe(JSON.stringify(observation, null, 2)) + '</pre>').join(''), item.observations.length) : '') + '</article>').join('');
  const atCapture = assessment.summary && !same(assessment.summary, mechanism)
    ? disclosure('assessment-context', 'Capture context', '<p class="assessment-context">' + investigationText(run, assessment.summary) + '</p>') : '';
  if (detailed) return '<section class="investigation-details"><h3>Assessment details</h3>' + atCapture +
    (historyHtml ? disclosure('related-history', 'Related history', historyHtml) : '') +
    (findingDetails ? disclosure('all-findings', 'Retained findings', '<div class="findings">' + findingDetails + '</div>', run.findings.length) : '') +
    (hypotheses ? disclosure('competing-explanations','Explanations considered',hypotheses,assessment.hypotheses.length) : '') +
    (connections ? disclosure('alert-connections','How the alerts relate',connections,assessment.connections.length) : '') + atlasHtml + '</section>';
  return '<section class="briefing">' + header + inconclusiveNote +
    '<div class="assessment-decision"><div class="assessment-main"><span class="text-label">' + (run.status === 'inconclusive' ? 'Evidence assessment · inconclusive' : 'Likely explanation · model assessment') + '</span><p class="brief-lead">' + investigationText(run, mechanism || 'No supported cause yet.') + '</p>' +
    inlineDisclosure('assessment-basis', run.status === 'inconclusive' ? 'Assessment basis' : 'Why this fits', (assessment.basis ? '<p class="assessment-basis">' + investigationText(run, assessment.basis) + '</p>' : '') + (extraIds.length ? '<div class="citations">' + investigationRefs(run, extraIds) + '</div>' : ''), mainIds.length ? mainIds.length + (mainIds.length === 1 ? ' source' : ' sources') : '') + '</div>' +
    '<div class="next-check"><h4>Next check</h4><p><strong>' + investigationText(run, assessment.next_action) + '</strong></p>' + (assessment.expected_finding ? inlineDisclosure('expected-finding', 'Expected finding', '<p>' + investigationText(run, assessment.expected_finding) + '</p>') : '') + evidencePrompt + '</div>' +
    '</div>' + atlasHtml + discrepancy + (briefObservations ? '<div class="key-observations">' + inlineDisclosure('key-observations', 'Key observations', '<ul>' + briefObservations + '</ul>', observations.length) + '</div>' : '') +
    '<p class="uncertainty"><strong>Still unconfirmed:</strong> ' + investigationText(run, assessment.uncertainty) + '</p></section>';
}

function investigationRefs(run, ids = []) {
  const labels = new Map();
  const references = investigationReferenceDescriptors(run);
  return ids.map(id => {
    const reference = references.get(id);
    if (!reference) return '<span class="source-unavailable">Reference ' + safe(id) + ' unavailable in this capture</span>';
    const occurrence = (labels.get(reference.label) || 0) + 1; labels.set(reference.label,occurrence);
    const disambiguator = occurrence > 1 ? ' · ' + (reference.start ? shortTime(reference.start) : String(occurrence)) : '';
    return investigationReferenceButton(reference, false, disambiguator);
  }).join('');
}

function investigationText(run, text) {
  const prose = String(text ?? '');
  const parts = [];
  let offset = 0;
  for (const match of investigationReferenceMatches(run, prose)) {
    parts.push(safe(prose.slice(offset,match.index)), investigationReferenceButton(match.reference, true));
    offset = match.index + match.id.length;
  }
  parts.push(safe(prose.slice(offset)));
  return parts.join('');
}

function investigationReferenceMatches(run, text) {
  const prose = String(text ?? '');
  const references = investigationReferenceDescriptors(run);
  if (!prose || !references.size) return [];
  const ids = [...references.keys()].sort((a,b)=>b.length - a.length).map(id=>id.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'));
  // Match retained IDs once in raw prose, never in escaped text or generated markup.
  const matches = new RegExp('(?<![\\p{L}\\p{N}\\p{M}_-])(?:' + ids.join('|') + ')(?![\\p{L}\\p{N}\\p{M}_-])','gu');
  return [...prose.matchAll(matches)].map(match=>({id:match[0], index:match.index, reference:references.get(match[0])}));
}

function investigationReferenceDescriptors(run) {
  const references = new Map();
  const checks = run?.checks || [];
  for (const item of [...checks, ...(run?.context?.evidence || [])]) {
    const id = item?.id;
    if (typeof id !== 'string' || !id.trim()) continue;
    if (references.has(id)) {
      references.set(id, {id, unresolved:'Ambiguous'});
    } else if (/<(?:ID|UUID|SECRET|EMAIL|IP|NUM|REDACTED)>|\[REDACTED\]/i.test(id)) {
      references.set(id, {id, unresolved:'Redacted'});
    } else {
      references.set(id, investigationReferenceDescriptor(item, checks.includes(item)));
    }
  }
  return references;
}

function investigationReferenceDescriptor(item, isCheck) {
  const checks = {workload_state:'Runtime snapshot',resource_history:'Resource history',search_logs:'Source logs',compare_baseline:'Baseline comparison',database_pressure:'Database metrics',dependency_evidence:'Dependency evidence',review_omitted:'Omitted log patterns',historical_episode:'Prior episode',alert_rule_logic:'Alert logic',scrape_discovery:'Target discovery'};
  const domains = {
    log_template:['Logs','logs','logs'], configuration:['Config','configuration','file-code-2'],
    metric_anomaly:['Metric','performance','chart-no-axes-combined'], alert:['Alert','alert','bell-ring'],
    image_evidence:['Image','image','scan-line'], audio_evidence:['Audio','audio','activity'],
    audio_transcript:['Audio transcript','audio transcript','mic'],
    operator_context:['Operator context (unverified)','operator context (unverified)','file-code-2'],
  };
  const checkDomain = ['search_logs','review_omitted'].includes(item.tool) ? 'log_template'
    : item.tool === 'workload_state' ? 'configuration'
      : ['resource_history','compare_baseline','database_pressure'].includes(item.tool) ? 'metric_anomaly' : '';
  const domain = Object.hasOwn(domains,item.domain) ? domains[item.domain] : domains[checkDomain];
  const [prefix,kind,iconName] = item.tool === 'historical_episode' ? ['Prior episode','history','layers']
    : domain || (isCheck ? ['Check','check','layers'] : ['Observation','observation','bell-ring']);
  let title = String(item.title || item.question || '').trim();
  if (title === item.id) title = '';
  if (item.domain === 'log_template') title = logLabel(title).replace(/^(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL):\s*/i,'');
  if (['image_evidence','audio_evidence','audio_transcript','operator_context'].includes(item.domain)) title = title.replace(/^(?:Image|Audio|Text) evidence:\s*/i,'');
  const prior = item.tool === 'historical_episode' ? item.result?.episode?.reference : '';
  const fullLabel = prior ? 'Prior episode ' + prior : Object.hasOwn(checks,item.tool) ? checks[item.tool] : prefix + (title ? ': ' + title : '');
  const label = fullLabel.length > 80 ? fullLabel.slice(0,77).trimEnd() + '...' : fullLabel;
  return {id:item.id, domain:item.domain || (isCheck ? 'check' : 'observation'), kind, iconName, label, fullLabel, start:item.time_range?.start};
}

function investigationReferenceButton(reference, inline = false, suffix = '') {
  if (reference.unresolved) return '<span class="source-unavailable" title="' + safe(reference.unresolved + ' reference: ' + reference.id) + '">' + safe(reference.unresolved) + ' source (unresolved)</span>';
  const action = 'Open retained ' + reference.kind + ' evidence';
  const description = action + ': ' + reference.fullLabel + suffix;
  return '<button type="button" class="evidence-link' + (inline ? ' evidence-link-inline' : '') + '" data-investigation-ref="' + safe(reference.id) + '" data-investigation-domain="' + safe(reference.domain) + '" title="' + safe(inline ? description : action) + '" aria-label="' + safe(description) + '">' + icon(reference.iconName) + '<span>' + safe(reference.label + suffix) + '</span></button>';
}

function investigationScope(payload) {
  const scope = payload.investigation?.context?.scope || {...payload.report?.incident,...payload.incident};
  const captured = payload.report?.resource_scope || {};
  const resource = scope.pod || scope.resource?.name || scope.service;
  const recurrence = payload.investigation?.context?.recurrence;
  const parts = [[scope.pod ? 'Pod' : scope.resource?.kind || 'Service',resource],['Namespace',scope.namespace]];
  if (captured.matched_pods > 1) parts.push(['Affected scope', captured.matched_pods + (captured.inventory_complete === false ? ' visible matches' : ' matching pods') + ' · ' + captured.captured_pods + ' captured' + (captured.omitted_pods ? ' · ' + captured.omitted_pods + ' omitted' : '')]);
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
  const checkNames = {workload_state:'Workload state', resource_history:'Resource history', search_logs:'Source logs', compare_baseline:'Baseline comparison', database_pressure:'Database metrics', dependency_evidence:'Dependencies', review_omitted:'Omitted logs', historical_episode:'Prior episode', alert_rule_logic:'Alert condition', scrape_discovery:'Target discovery'};
  const rows = checks.map(item => {
    const hasError = item.status !== 'completed' || Boolean(item.result?.error || item.error);
    const state = item.status === 'completed' && hasError ? 'unavailable' : item.status;
    const marker = '<span class="step-marker" aria-hidden="true">' + (item.status === 'running' ? '<span class="spinner"></span>' : icon(hasError ? 'bell-ring' : 'check')) + '</span>';
    const answer = '<p class="check-answer">' + safe(checkObservation(item)) + '</p><button class="evidence-link" data-investigation-ref="' + safe(item.id) + '">' + icon('chevron-right') + ' Open observation</button><small>' + safe(state) + (item.finished_at ? ' · ' + formatDate(item.finished_at) : '') + '</small>';
    if (compact) return '<li class="agent-step ' + safe(state) + '">' + marker + disclosure('activity-' + item.id, checkNames[item.tool] || item.question || 'Source check', '<strong>' + safe(item.question) + '</strong>' + answer, hasError && state !== 'running' ? state : '', !hasError || state === 'running' ? state : '') + '</li>';
    return '<li class="agent-step ' + safe(state) + '">' + marker + '<div><strong>' + safe(item.question) + '</strong>' + answer + '</div></li>';
  }).join('');
  const details = '<dl class="coverage-details"><div><dt>Input tokens</dt><dd>' + Number(usage?.prompt_tokens || 0).toLocaleString('en') + '</dd></div><div><dt>Output tokens</dt><dd>' + Number(usage?.completion_tokens || 0).toLocaleString('en') + '</dd></div><div><dt>Safety reserve</dt><dd>' + Number(budget.accounted_total_tokens || 0).toLocaleString('en') + (budget.maximum_total_tokens ? ' / ' + Number(budget.maximum_total_tokens).toLocaleString('en') : '') + '</dd></div><div><dt>Budget remaining</dt><dd>' + Number(budget.remaining_tokens || 0).toLocaleString('en') + '</dd></div><div><dt>Model calls</dt><dd>' + (run.calls?.length || 0) + '</dd></div><div><dt>Earlier attempts</dt><dd>' + Number(run.lifetime_usage?.total_tokens || 0).toLocaleString('en') + ' tokens</dd></div></dl><p class="queue-note">' + (usage?.complete ? 'Provider-reported usage is shown alongside a pre-call safety reserve.' : 'Provider usage is incomplete; the safety reserve prevents unbounded follow-up calls.') + '</p>';
  const current = checks.find(item => item.status === 'running');
  const running = ['queued','running','waiting'].includes(run.status);
  const activity = running ? (current?.question || 'Checking retained evidence') : run.status === 'ready' ? 'Investigation complete' : run.status === 'inconclusive' ? 'No supported cause yet' : 'Investigation needs attention';
  return '<aside class="agent-progress"><div class="activity-heading"><div><h3>Investigation activity</h3><strong>' + safe(activity) + '</strong><small>' + checks.length + ' check' + (checks.length === 1 ? '' : 's') + (run.finished_at ? ' · ' + formatDate(run.finished_at) : '') + '</small></div>' +
    (['ready','incomplete','inconclusive'].includes(run.status) ? '<button class="icon-button" data-investigate="' + safe(run.episode_id) + '" title="Reassess episode" aria-label="Reassess episode">' + icon('refresh-cw') + '</button>' : '') + '</div>' +
    (rows ? '<ol class="agent-steps' + (compact ? ' compact-checks' : '') + '">' + rows + '</ol>' : '<p class="queue-note">No checks recorded yet.</p>') +
    '<div class="activity-footer"><div class="usage-note">' + disclosure('token-usage',tokenText,details) + '</div></div>' +
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
  const checks = (run.checks || []).map(item=>disclosure('agent-' + item.id,item.question,'<p>' + safe(item.distinguishes) + '</p><p class="queue-note">' + formatDate(item.started_at) + ' · ' + safe(item.status) + '</p>' + investigationResult(item.result,item.id),item.id)).join('');
  const exampleText = examples => examples.map(item => typeof item === 'string' ? item : JSON.stringify(item)).join('\n');
  const evidence = (run.context?.evidence || []).map(item=> {
    if (['image_evidence','audio_evidence','audio_transcript','operator_context'].includes(item.domain)) {
      const attachment = attachments.find(entry=>entry.attachment_id === item.attachment_id);
      const extraction = attachment?.extraction;
      const facts = (extraction?.content_text ? [extraction.content_text] : extraction?.observations || item.examples || []).map(entry=>typeof entry === 'string' ? entry : entry.fact).filter(Boolean);
      const observed = item.time_range?.observed_at;
      const limitation = extraction?.limitation || item.limitation;
      const content = '<div class="media-observation-meta"><span class="queue-note">' + (item.domain === 'operator_context' ? 'Operator-provided evidence, not verified telemetry' : item.domain === 'image_evidence' ? 'Image observation' : 'Audio observation') +
        (observed ? ' · observed ' + safe(formatDate(observed)) : ' · observation time unknown') + '</span>' +
        '</div>' + (attachment ? evidenceArtifact({...attachment, preview_id:'agent-' + item.id, kind:attachment.kind || (item.domain === 'image_evidence' ? 'image' : 'audio')}) : '') +
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
  return '<section class="investigation-sources"><h3>Agent observations <span class="detail-count">' + ((run.checks?.length || 0) + (run.context?.evidence?.length || 0)) + '</span></h3>' + (checks + evidence || '<p class="queue-note">No investigation sources retained yet.</p>') + '</section>';
}

function investigationTimeline(run = {}, attachments = [], revisions = []) {
  const events = (run.checks || []).map(item=>({time:item.started_at, title:item.question, detail:item.status + ' · ' + item.tool, link:'<button class="evidence-link" data-investigation-ref="' + safe(item.id) + '">View observation</button>'}));
  attachments.forEach(item => events.push({time:item.uploaded_at || item.created_at, title:'Evidence uploaded: ' + item.filename, detail:'Operator-provided · ' + item.kind + ' · ' + item.status + (item.observed_at ? ' · observed ' + formatDate(item.observed_at) : ' · observation time unknown'), link:'<button class="evidence-link" data-media-ref="' + safe(item.attachment_id) + '">View evidence</button>'}));
  revisions.filter(item => item.reason && item.reason !== 'initial_capture').forEach(item => events.push({time:item.created_at, title:item.reason === 'evidence_added' ? 'Reassessment requested with additional evidence' : 'Reassessment requested', detail:String(item.reason).replaceAll('_',' ') + ' · ' + item.status}));
  const review = (run.calls || []).find(item=>item.phase === 'evidence_review');
  if (review) events.push({time:review.started_at, title:'Conclusion checked against evidence', detail:run.review?.status || review.status});
  if (run.finished_at) events.push({time:run.finished_at, title:run.status === 'ready' ? 'Assessment saved' : 'Stopped without a validated conclusion'});
  events.sort((left, right) => (Date.parse(left.time) || Infinity) - (Date.parse(right.time) || Infinity));
  const rows = events.map(item => '<li><time' + (item.time ? ' datetime="' + safe(item.time) + '"' : '') + '>' + safe(item.time ? formatDate(item.time) : 'Time unknown') + '</time><div><strong>' + safe(item.title) + '</strong>' + (item.detail ? '<small>' + safe(item.detail) + '</small>' : '') + (item.link || '') + '</div></li>').join('');
  return '<section class="timeline-view"><h3>Investigation activity</h3>' + (rows ? '<ol class="event-timeline">' + rows + '</ol>' : '<p class="queue-note">No investigation activity recorded yet.</p>') + '</section>';
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
  const captures = report.alert_metric_evidence || [];
  const unavailable = captures.filter(item=>item.status !== 'available' && item.status !== 'partial');
  const partial = captures.filter(item=>item.status === 'partial');
  const unavailableNote = unavailable.length ? '<p class="queue-note">Alert signal unavailable: ' + unavailable.map(item=>safe(item.alertname || 'rule') + ' (' + safe(String(item.reason || 'not captured').replaceAll('_',' ')) + ')').join('; ') + '.</p>' : '';
  const partialNote = partial.length ? '<p class="queue-note">Alert signal coverage is partial: ' + partial.map(item=>safe(item.alertname || 'rule') + ' (' + safe(String(item.reason || 'some samples unavailable').replaceAll('_',' ')) + ')').join('; ') + '.</p>' : '';
  const missingNote = !unavailable.length && !partial.length && !(report.pm_signals || []).some(item=>item.signal_origin === 'alert_rule') ? '<p class="queue-note">The alert-condition signal was not retained in this capture. Historical captures are not backfilled.</p>' : '';
  const coverage = unavailableNote + partialNote + missingNote;
  return coverage + '<div class="metrics-grid">' + (report.pm_signals || []).map(item => metricChart(item)).join('') + '</div>' + (report.pm_coverage_note ? '<p class="queue-note">' + safe(report.pm_coverage_note) + '</p>' : '');
}
function overviewMetrics(report) {
  const supported = new Set((report.primary_hypothesis?.supporting_evidence || []).map(item => item.evidence_id));
  const signals = report.pm_signals || [];
  const chosen = signals.filter(item => (item.signal_origin === 'alert_rule' || supported.has(item.evidence_id) && item.component) && (item.values || []).filter(point => point.value != null && Number.isFinite(Number(point.value))).length >= 2).sort((a,b)=>Number(b.signal_origin === 'alert_rule') - Number(a.signal_origin === 'alert_rule'))[0];
  if (!chosen) return '';
  const rule = chosen.signal_origin === 'alert_rule';
  const points = chosen.values;
  const start = points[0].timestamp; const end = points.at(-1).timestamp;
  const minutes = Math.round((Date.parse(end) - Date.parse(start)) / 60000);
  const value = (number, fallback) => {
    if (number == null || !Number.isFinite(Number(number))) return fallback || 'Unavailable';
    if (chosen.unit === 'seconds') return Number(number * 1000).toLocaleString('en', {maximumFractionDigits:1}) + ' ms';
    if (chosen.unit === 'state') return Number(number).toLocaleString('en', {maximumFractionDigits:2});
    return fallback || Number(number).toLocaleString('en', {maximumSignificantDigits:4});
  };
  const title = chosen.metric === 'up' ? 'Target scrape health' : chosen.label;
  const missing = points.filter(point => point.value == null).length;
  const replica = chosen.labels?.prometheus_replica || chosen.labels?.replica;
  return '<section class="overview-metrics"><div class="section-heading"><h3>Relevant performance</h3><button class="evidence-link" data-evidence-link="' + safe(chosen.evidence_id) + '" data-domain="metrics">All performance evidence</button></div>' +
    '<article class="metric-preview"><div class="metric-preview-heading"><h4>' + safe(title) + '</h4><span class="queue-note">' + safe(minutes) + ' min captured</span></div>' +
    '<div class="metric-preview-values"><span>' + (rule ? 'Before alert' : 'Reference median') + ' <b>' + safe(value(chosen.baseline_value, chosen.baseline)) + '</b></span><span>Selected <b>' + safe(value(chosen.peak_value, chosen.peak)) + '</b></span>' +
    (rule ? '<span class="threshold-key">Threshold <b>' + safe(chosen.operator + ' ' + value(chosen.threshold, String(chosen.threshold) + (chosen.unit && chosen.unit !== 'state' ? ' ' + chosen.unit : ''))) + '</b></span>' : '') + '</div>' + sparkline(chosen, 640) +
    '<div class="chart-times"><time title="' + safe(formatExactDate(start)) + '">' + safe(shortTime(start)) + '</time><span class="alert-key">' + (rule ? 'Alert ' + safe(shortTime(chosen.alert_timestamp)) : 'Marker: selected deviation') + '</span><time title="' + safe(formatExactDate(end)) + '">' + safe(shortTime(end)) + '</time></div>' +
    '<p class="metric-preview-source">' + safe(chosen.component || 'Rule result') + (replica ? ' · replica ' + safe(replica) : '') + ' · 1 of ' + signals.length + ' captured series' + (missing ? ' · ' + missing + ' missing samples' : '') + '</p>' +
    (chosen.metric === 'up' ? '<small class="queue-note">0: failed scrape · 1: successful scrape</small>' : '') + '</article></section>';
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
    (report.topology?.length ? '<p class="queue-note">' + (report.topology.every(item=>item.kind === 'pod') ? 'Captured pods: ' + report.topology.map(item=>safe(item.name) + (item.node ? ' on ' + safe(item.node) : '')).join(' · ') : 'Dependencies: ' + report.topology.filter(item=>item.from && item.to).map(item=>safe(item.from) + ' → ' + safe(item.to)).join(' · ')) + '</p>' : '') + '</section>';
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
    ? '<button id="add-evidence-action" class="context-action" data-add-evidence="' + safe(selectedEpisodeId || '') + '">' + icon('paperclip') + ' Add evidence</button>'
    : '<a class="button-link" href="/settings#evidence-models" title="Validate the core and specialist models in Settings to attach image or audio evidence">Evidence setup</a>';
  const contextWorkspace = '<div class="evidence-prompt" aria-label="Additional evidence">' + evidenceAction + ((payload.media_evidence || []).length ? '<button class="evidence-link" data-media-ref="">Additional evidence (' + payload.media_evidence.length + ')</button>' : '') + '</div>';
  const ai = payload.investigation;
  const fallback = ai?.status !== 'ready' ? '<section class="observed-summary"><h3>Alert context</h3><p>' + safe(report.fault_alerts?.[0]?.description || incident.summary) + '</p></section>' : '';
  const d = report.engineering_diagnostics;
  const diagnostics = disclosure('diagnostics', 'Engineering diagnostics', '<dl class="coverage-details"><div><dt>Selected evidence</dt><dd>' + d.selected_evidence + '</dd></div><div><dt>Log reduction</dt><dd>' + pct(d.log_compression_ratio) + '</dd></div><div><dt>Signal preservation</dt><dd>' + pct(d.important_signal_preservation) + '</dd></div><div><dt>Grounding</dt><dd>' + pct(d.hypothesis_grounding_score) + '</dd></div><div><dt>Runtime</dt><dd>' + Number(d.runtime_seconds || 0).toFixed(2) + 's</dd></div></dl><p class="queue-note">Rule-based hypothesis: ' + safe(report.primary_hypothesis.statement) + '</p>');
  const tabs = ['overview','investigation','evidence','timeline'];
  let content;
  if (reportTab === 'evidence') content = (sourceReturn?.episodeId === selectedEpisodeId ? '<div class="source-return"><button type="button" data-return-source>Back to ' + safe(sourceReturn.tab) + '</button></div>' : '') +
    '<div class="evidence-workspace"><div class="evidence-views" role="group" aria-label="Evidence views">' + [['captured','Captured telemetry'],['sources','Investigation sources']].map(([key,label]) => '<button type="button" id="evidence-view-' + key + '" data-evidence-view="' + key + '" aria-pressed="' + (evidenceView === key) + '" aria-controls="evidence-view-panel">' + label + '</button>').join('') + '</div><div id="evidence-view-panel" aria-labelledby="evidence-view-' + evidenceView + '">' +
    (evidenceView === 'sources' ? mediaEvidencePanel(payload) + investigationEvidence(ai || {}, payload.media_evidence || []) : evidencePanel(report)) + '</div></div>';
  else if (reportTab === 'timeline') content = timelinePanel(report) + investigationTimeline(ai || {}, payload.media_evidence || [], payload.investigation_revisions || []);
  else if (reportTab === 'investigation') content = '<div class="overview-layout investigation-layout">' + investigationProgress(ai || {}) + briefingPanel(payload, true) + sourceReviewPanel(payload) + '</div>';
  else content = '<div class="overview-layout report-workspace">' + investigationScope(payload) + '<div class="report-main">' + briefingPanel(payload, false, contextWorkspace) + fallback + (ai?.assessment ? '' : contextWorkspace) + (ai?.assessment ? overviewMetrics(report) : '') + '</div><div class="report-rail">' + investigationProgress(ai || {}, true) + '</div></div>';
  return '<section id="incident-report"><div class="report-navigation"><div role="tablist" aria-label="Investigation views">' +
    tabs.map(name=>'<button id="tab-' + name + '" role="tab" data-report-tab="' + name + '" aria-selected="' + (name === reportTab) + '" aria-controls="investigation-panel" tabindex="' + (name === reportTab ? 0 : -1) + '">' + name[0].toUpperCase() + name.slice(1) + '</button>').join('') +
    '</div><div class="report-actions">' + (reportTab === 'overview' ? '' : evidenceAction) + exports + '</div></div><div id="investigation-panel" role="tabpanel" aria-labelledby="tab-' + reportTab + '" tabindex="0">' + content + '</div><div class="report-technical">' + diagnostics + '</div></section>';
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

function atlasRecords(value) { return Array.isArray(value) ? value : value == null ? [] : [value]; }
function atlasId(item) { return String(item?.atlas_case_id ?? item?.case_id ?? item?.pattern_id ?? item?.id ?? item?.fingerprint ?? ''); }
function atlasText(value) {
  if (value == null) return '';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(atlasText).filter(Boolean).join(', ');
  if (value.key != null && value.value != null) return `${value.key}: ${value.value}${value.unit ? ' ' + value.unit : ''}`;
  return String(value.fact ?? value.observation ?? value.description ?? value.statement ?? value.summary ?? value.title ?? value.text ?? value.value ?? value.name ?? value.ref ?? value.id ?? '');
}
function atlasFactSection(title, values, emptyText) {
  const items = atlasRecords(values).map(item => {
    const text = atlasText(item);
    const source = item && typeof item === 'object'
      ? [item.source && `Source: ${atlasText(item.source)}`, item.observed_at && `Observed: ${formatDate(item.observed_at)}`, item.supporting_refs && `Supporting references: ${atlasText(item.supporting_refs)}`, item.reference && `Reference: ${atlasText(item.reference)}`, item.source_ref && `Reference: ${atlasText(item.source_ref)}`].filter(Boolean).join(' · ')
      : '';
    return text ? `<li><span>${safe(text)}</span>${source ? `<small>${safe(source)}</small>` : ''}</li>` : '';
  }).filter(Boolean);
  return `<section class="atlas-record-section"><h3>${safe(title)}</h3>${items.length ? `<ul>${items.join('')}</ul>` : `<p class="queue-note">${safe(emptyText)}</p>`}</section>`;
}
function atlasUrl(kind = '', id = '') {
  const params = new URLSearchParams();
  if (kind && id) params.set(kind, id);
  if (atlasQuery) params.set('q', atlasQuery);
  if (atlasScope) params.set('scope', atlasScope);
  return '/estima' + (params.size ? '?' + params.toString() : '');
}
function atlasCaseLink(item, label = '') {
  const id = atlasId(item);
  if (!id) return '';
  const alert = atlasRecords(item.observations).find(value => value?.key === 'alert_family')?.value;
  const service = item.scope?.service || item.scope?.workload;
  const title = label || item.title || [alert, service].filter(Boolean).join(' · ') || item.summary || item.case_id || id;
  const instance = item.instance_id || item.instance || '';
  const date = item.observed_at || item.created_at || item.occurred_at || '';
  const relation = item.relation === 'lexical_similarity' ? 'Text match' : item.relation === 'fingerprint_match' ? 'Fingerprint match' : item.relation === 'recent_in_scope' ? 'Recent in scope' : item.relation;
  return `<a class="atlas-case-link" href="${safe(atlasUrl('case', id))}"${item.summary && title !== item.summary ? ` title="${safe(item.summary)}"` : ''}><span><strong>${safe(title)}</strong><small>${safe([relation, instance, date && formatDate(date)].filter(Boolean).join(' · ') || id)}</small></span>${icon('chevron-right')}</a>`;
}
function atlasPatternLink(item) {
  const id = atlasId(item);
  if (!id) return '';
  const observation = [item.key, item.value].filter(value => value != null).join(': ');
  const title = item.title || item.name || item.summary || observation || item.pattern_id || id;
  const cases = item.case_count ?? item.total_cases ?? item.occurrence_count ?? item.match_count;
  const instances = item.instance_count ?? item.instances_count;
  const meta = [cases == null ? '' : `${safe(cases)} ${Number(cases) === 1 ? 'case' : 'cases'}`, instances == null ? '' : `${safe(instances)} ${Number(instances) === 1 ? 'instance' : 'instances'}`].filter(Boolean).join(' · ');
  return `<a class="atlas-pattern-link" href="${safe(atlasUrl('pattern', id))}"><span><strong>${safe(title)}</strong><small>${safe(meta || id)}</small></span>${icon('chevron-right')}</a>`;
}
function atlasProvenance(record) {
  const scope = record.scope && typeof record.scope === 'object' ? Object.entries(record.scope).filter(([,value]) => value != null && String(value) !== '').map(([key,value]) => `${key}: ${value}`).join(' · ') : record.scope;
  const entries = [
    ['Instance', record.instance_id ?? record.instance],
    ['Scope', scope],
    ['Source', record.source ?? record.source_system],
    ['Observed', record.observed_at ?? record.created_at ?? record.occurred_at],
    ['Record', record.source_ref ?? record.source_id ?? record.record_id ?? record.episode_id],
  ].filter(([,value]) => value != null && String(value) !== '');
  const refs = record.evidence_refs ?? record.source_refs ?? record.references ?? record.provenance?.sources;
  atlasRecords(refs).forEach((ref, index) => {
    const value = atlasText(ref);
    if (value) entries.push([`Reference ${index + 1}`, value]);
  });
  if (!entries.length) return '<p class="queue-note">No provenance details were returned for this record.</p>';
  return `<dl class="atlas-provenance">${entries.map(([label,value]) => `<div><dt>${safe(label)}</dt><dd>${safe(value)}</dd></div>`).join('')}</dl>`;
}
function atlasRecord(record) {
  const facts = record.facts ?? record.observations ?? record.observed_facts ?? record.observed ?? (record.kind === 'observation' && record.key != null ? [{key:record.key,value:record.value,unit:record.unit}] : null);
  const hypotheses = record.hypotheses ?? record.interpretations ?? record.possible_causes ?? (record.interpretation ? [{statement:record.interpretation}] : null);
  const hypothesisTitle = record.kind === 'observation' ? 'Pattern interpretation (not causal evidence)' : 'Unverified hypotheses';
  const patternId = record.pattern_id ?? record.related_pattern_id;
  const linkedPattern = patternId ? atlasPatternLink({id:patternId,title:record.pattern_title || patternId}) : '';
  return `<div class="atlas-record"><div class="atlas-record-columns">${atlasFactSection('Retained observations (facts)', facts, 'No structured facts were returned for this record.')}${atlasFactSection(hypothesisTitle, hypotheses, 'No hypothesis was returned. No cause is asserted.')}</div><section class="atlas-record-section"><h3>Provenance</h3>${atlasProvenance(record)}${linkedPattern ? `<div class="atlas-related-pattern"><span>Similarity pattern</span>${linkedPattern}<p class="queue-note">Pattern membership indicates similarity only, not causation.</p></div>` : ''}</section></div>`;
}
function atlasPatternDetail(data) {
  const pattern = data?.pattern || {};
  const cases = atlasRecords(data?.cases || pattern.cases);
  const id = atlasId(pattern) || atlasSelectedPattern;
  const observation = [pattern.key, pattern.value].filter(value => value != null).join(': ');
  const title = pattern.title || pattern.name || pattern.summary || observation || id;
  const caseCount = pattern.case_count ?? pattern.total_cases ?? pattern.occurrence_count ?? pattern.match_count;
  const instanceCount = pattern.instance_count ?? pattern.instances_count;
  const counts = [caseCount == null ? '' : `Seen in ${safe(caseCount)} ${Number(caseCount) === 1 ? 'case' : 'cases'}`, instanceCount == null ? '' : `across ${safe(instanceCount)} ${Number(instanceCount) === 1 ? 'instance' : 'instances'}`].filter(Boolean).join(' ');
  const time = [pattern.first_seen_at || pattern.first_seen ? `First seen ${formatDate(pattern.first_seen_at || pattern.first_seen)}` : '', pattern.last_seen_at || pattern.last_seen ? `Last seen ${formatDate(pattern.last_seen_at || pattern.last_seen)}` : ''].filter(Boolean).join(' · ');
  return `<div class="atlas-detail-head"><a href="${safe(atlasUrl())}">${icon('chevron-right')}Back to Estima</a><div class="eyebrow">Similarity pattern</div><h2>${safe(title)}</h2><p>${safe(counts || `${cases.length} linked case${cases.length === 1 ? '' : 's'} returned`)}${time ? ' · ' + safe(time) : ''}</p><code>${safe(id)}</code></div><p class="atlas-pattern-note">${safe(pattern.interpretation || 'Repeated observation across cases; this does not establish a shared cause.')}</p><section class="atlas-record-section atlas-case-list"><h3>Linked cases <span class="queue-note">${cases.length} returned</span></h3>${cases.length ? cases.map(item => atlasCaseLink(item)).join('') : '<p class="queue-note">No cases were returned with this pattern.</p>'}</section>`;
}
function atlasCaseDetail(data) {
  const record = data?.case || {};
  const id = atlasId(record) || atlasSelectedCase;
  const relations = atlasRecords(record.related_cases || record.relations || record.related_case_ids);
  const scope = record.scope && typeof record.scope === 'object' ? record.scope : {};
  const alert = atlasRecords(record.observations).find(item => item?.key === 'alert_family')?.value;
  const heading = [alert, scope.service || scope.workload].filter(Boolean).join(' · ') || record.title || 'Retained case';
  const location = [scope.cluster, scope.namespace].filter(Boolean).join(' / ');
  return `<div class="atlas-detail-head"><a href="${safe(atlasUrl())}">${icon('chevron-right')}Back to Estima</a><div class="eyebrow">Retained record</div><h2>${safe(heading)}</h2>${record.summary ? `<p class="atlas-case-summary">${safe(record.summary)}</p>` : ''}<p>${safe([record.instance_id || record.instance, record.observed_at && formatDate(record.observed_at), location].filter(Boolean).join(' · ') || 'Record details')}</p><code>${safe(id)}</code></div>${atlasRecord(record)}${relations.length ? `<section class="atlas-record-section atlas-case-list"><h3>Related records</h3>${relations.map(item => typeof item === 'object' ? atlasCaseLink(item) : atlasCaseLink({case_id:item})).join('')}</section>` : ''}`;
}
function atlasFailureText(message, status) {
  if (status === 'not_configured') return 'Estima is not configured. Add its service URL and enable reads in Settings.';
  if (status === 'disabled') return 'Estima reads are disabled. Enable them in Settings to browse retained records.';
  return message || 'Estima is currently unavailable. Try again when the service is reachable.';
}
function relatedAtlasCases(run) {
  const cases = atlasRecords(run?.context?.atlas_cases);
  if (cases.length) return `<section class="related-atlas-cases"><h4>Related Estima records</h4><p>Similarity references retrieved for this investigation. Estima record IDs are not FCAPSule evidence citations, and the relationship does not establish cause.</p>${cases.map(item => atlasCaseLink(item, item.summary || `Estima record ${atlasId(item)}`)).join('')}</section>`;
  const state = run?.context?.atlas_retrieval?.status;
  const messages = {
    no_matches:'Estima search completed with no matching records. This does not establish that a cause is new or absent.',
    unavailable:'Estima retrieval was unavailable; no shared records were available to this investigation.',
    pending:'Estima retrieval is pending; no related records are available yet.',
  };
  return messages[state] ? `<p class="atlas-retrieval-note" role="status">${safe(messages[state])}</p>` : '';
}
function atlasRender() {
  return EstimaExplorer.start(app, {
    query:atlasQuery, scope:atlasScope,
    selectedPattern:atlasSelectedPattern, selectedCase:atlasSelectedCase,
    icon, url:atlasUrl,
    setSearch(query, scope) { atlasQuery = query; atlasScope = scope; },
  });
}
async function loadAtlas() { return atlasRender(); }
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
      view === 'targets' ? renderTargets(state) : view === 'patterns' ? renderPatterns(state) : view === 'estima' ? loadAtlas() : renderSettings(state);
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
    app.removeAttribute('aria-busy');
  } catch (_) {
    document.querySelector('.system-state').className = 'system-state error';
    document.querySelector('#system-state').textContent = 'Disconnected';
    if (app.querySelector('.boot') || app.querySelector('.boot-error')) {
      app.removeAttribute('aria-busy');
      app.innerHTML = `<div class="boot-error" role="alert"><h1>${safe(viewLabels[view])} is unavailable</h1><p>Could not connect to FCAPSule. Your data has not been changed.</p><button type="button" id="retry-view">Retry</button></div>`;
    }
  } finally { refreshing = false; }
}
refresh();
setInterval(refresh, 4000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
app.addEventListener('click', event => { if (event.target.closest('#retry-view')) refresh(); });
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
