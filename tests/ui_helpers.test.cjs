const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../fcapsule/ui/assets/app.js'), 'utf8');
function helper(name, next, context = {}) {
  const code = source.slice(source.indexOf(`function ${name}(`), source.indexOf(`function ${next}(`));
  return vm.runInNewContext(code + '\n' + name, context);
}

test('export shows actual artifact sizes and escapes retained paths', () => {
  const render = helper('capsuleExport', 'reportPanel', {
    safe: value => String(value ?? '').replaceAll('<', '&lt;'),
    bytes: value => value + ' B', formatDate: value => value,
  });
  const payload = { record: {capsule_id:'capsule/one'}, report:{incident:{incident_id:'one'}},
    storage:{archive_bytes:4096, report_bytes:8192, directory:'/data/<source>', expires_at:'2026-10-20', retention_days:30} };
  const html = render(payload);
  assert.match(html, /capsule%2Fone\/fcapsule_one.zip/);
  assert.match(html, /4096 B/);
  assert.match(html, /8192 B/);
  assert.match(html, /Eligible for cleanup 2026-10-20/);
  assert.match(html, /selected capture one/);
  assert.match(html, /Investigation files cover the episode/);
  assert.match(html, /\/data\/&lt;source>/);
  assert.doesNotMatch(html, /<source>/);
  payload.storage.archive_bytes = null;
  assert.doesNotMatch(render(payload), /fcapsule_one.zip/);
  assert.equal(render({}), '');
});

test('connection testing does not replace unsaved settings', async () => {
  const button = {disabled:false}; const notice = {hidden:true, textContent:''};
  const state = {sources:{}}; const window = {};
  const code = source.slice(source.indexOf('async function testTargets('), source.indexOf('async function syncTargets('));
  const run = vm.runInNewContext(code + '\ntestTargets', {
    document:{querySelector: selector => selector === '#test-targets' ? button : notice},
    window, lastState:state, refresh:async()=>{},
    renderTargets:()=>assert.fail('Editable form must not be replaced'),
    fetch:async()=>({ok:true,json:async()=>({ok:true,targets:{prometheus:{ok:true}}})}),
  });
  await run();
  assert.equal(button.disabled, false);
  assert.equal(notice.hidden, false);
  assert.equal(state.sources.targets.prometheus.ok, true);
  assert.match(notice.textContent, /Saved source connections are healthy/);
});

test('masked JSON logs retain concise messages, including escaped quotes', () => {
  const label = helper('logLabel', 'briefingPanel');
  assert.equal(label('{"message":"Poison job failed", "job_id": <NUM>}'), 'Poison job failed');
  assert.equal(label('{"message":"Nested \\"quoted\\" error", "job_id": <NUM>}'), 'Nested "quoted" error');
  assert.equal(label('{"msg":"Plain JSON"}'), 'Plain JSON');
  assert.equal(label('{"level":"FATAL","message":"Decoder failure","id":<NUM>}'), 'FATAL: Decoder failure');
  assert.equal(label('plain log'), 'plain log');
  assert.equal(label('x'.repeat(200)).length, 140);
});

test('closed episodes do not duplicate the selected report controls', () => {
  const table = helper('episodeTable', 'episodeContext', {
    lastState: { overview: { applications: [] } }, selectedEpisodeId: 'second',
    selectedReport: {}, reportLoading: false, reportError: '',
    safe: value => String(value ?? ''), status: value => value, formatDate: value => value, formatExactDate: value => value, relativeTime: value => value,
    resourceLabel: item => item.app_id || 'application', recurrenceLabel: () => '',
    episodeContext: () => '<select id="signal-report"></select>',
    reportPanel: () => '<div role="tablist"></div>',
  });
  const html = table(['first', 'second', 'third'].map(episode_id => ({
    episode_id, signal_count: 2, report_count: 2, title: episode_id, status: 'active',
  })), []);
  assert.equal((html.match(/role="tablist"/g) || []).length, 1);
  assert.equal((html.match(/id="signal-report"/g) || []).length, 1);
  assert.equal((html.match(/class="episode is-open"/g) || []).length, 1);
});

test('an empty archive has a different explanation from an empty filtered queue', () => {
  const table = helper('episodeTable', 'episodeContext', {
    lastState:{overview:{archived_episodes:[]}}, queueFilters:{namespace:'',scope:'',status:'',period:'',query:''},
  });
  assert.match(table([],[],true),/No archived episodes yet/);
  assert.match(table([],[],false),/No incidents in the queue/);
});

test('domain disclosures retain native semantics, count and decorative icon', () => {
  const disclosure = helper('disclosure', 'reportId', {
    safe: value => String(value ?? '').replaceAll('<', '&lt;'),
    openDisclosures: new Set(['domain-logs']),
  });
  const html = disclosure('domain-logs', 'Log evidence', '<pre>retained log</pre>', 2);
  assert.match(html, /<details[^>]+ open>/);
  assert.match(html, /<summary id="disclosure-domain-logs">/);
  assert.match(html, /data-icon="logs" aria-hidden="true"/);
  assert.match(html, /detail-count">2/);
  assert.match(html, /<pre>retained log<\/pre>/);
});

test('episode overview does not imply it is the selected individual alert', () => {
  const context = helper('episodeContext', 'formatDate', {
    reportId:()=> 'one', reportTab:'overview', safe:value=>String(value ?? ''), relativeTime:value=>value, icon:()=>'',
  });
  const html = context({episode_id:'episode', reference:'EP-ONE', signal_count:2, status:'resolved', signals:[{incident_id:'one',reference:'INC-ONE'},{incident_id:'two'}]});
  assert.match(html, /Episode EP-ONE/);
  assert.doesNotMatch(html, /INC-ONE/);
  assert.match(html, /2 captured alerts/);
  assert.doesNotMatch(html, /select/);
});

test('episode lifecycle does not label an incomplete investigation as saved', () => {
  const context = helper('episodeContext', 'formatDate', {
    reportId:()=> 'one', reportTab:'overview', safe:value=>String(value ?? ''), relativeTime:value=>value, icon:()=>'',
  });
  const html = context({episode_id:'episode', reference:'EP-ONE', signal_count:1, status:'resolved',
    investigation:{status:'incomplete',finished_at:'attempt-time'}, signals:[{incident_id:'one',reference:'INC-ONE'}]});
  assert.match(html, /Investigation needs attention attempt-time/);
  assert.doesNotMatch(html, /Assessment saved/);
});

test('queue filters can find a stable incident reference without hiding unrelated history', () => {
  const filter = helper('filteredEpisodes', 'queueFiltersPanel', {
    queueFilters:{namespace:'commerce',scope:'',status:'resolved',period:'',query:'inc-ab'},
    resourceLabel:(item, application) => item.resource?.name || application?.name || item.app_id,
  });
  const applications = new Map([['orders',{name:'Orders',namespace:'commerce'}],['other',{name:'Other',namespace:'platform'}]]);
  const rows = filter([
    {reference:'EP-AB12',app_id:'orders',status:'resolved',started_at:new Date().toISOString(),resource:{name:'orders-api'},signals:[{reference:'INC-AB12',incident_id:'incident-one'}]},
    {reference:'EP-CD34',app_id:'other',status:'resolved',started_at:new Date().toISOString(),resource:{name:'other-api'},signals:[{reference:'INC-CD34',incident_id:'incident-two'}]},
  ], applications);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].reference, 'EP-AB12');
});

test('queue keeps common filters visible and names hidden restrictions', () => {
  const panel = helper('queueFiltersPanel', 'relatedActivity', {
    safe:value=>String(value ?? ''), openDisclosures:new Set(),
    queueFilters:{namespace:'commerce',query:'',scope:'',status:'resolved',period:'week'},
    resourceLabel:()=> 'orders-api',
  });
  const html = panel([{app_id:'orders'}], new Map([['orders',{namespace:'commerce'}]]));
  assert.match(html, /<select id="queue-namespace"/);
  assert.match(html, /id="queue-search" type="text"/);
  assert.match(html, /<details class="queue-more"/);
  assert.match(html, /Filters <span class="detail-count">2/);
  assert.match(html, /Status: resolved/);
  assert.match(html, /Time: Last 7 days/);
  assert.match(html, /data-clear-filters/);
});

test('assessment presents one primary explanation and retains distinct observations', () => {
  const render = helper('briefingPanel', 'investigationRefs', {
    safe:value=>String(value ?? ''), formatDate:value=>value, selectedEpisodeId:'episode',
    investigationRefs:()=>'', disclosure:(id,title,body)=>'<details><summary>'+title+'</summary>'+body+'</details>',
  });
  const html = render({investigation:{status:'ready',model:'test-model',finished_at:'done',
    assessment:{summary:'Alert scope',likely_mechanism:'Pool saturation',next_action:'Check pool usage',
      expected_finding:'Pool is full',uncertainty:'Exact caller unknown',evidence_ids:[],hypotheses:[],connections:[]},
    findings:[{id:'primary',title:'Likely explanation',summary:'Pool saturation',evidence_ids:[]},
      {id:'secondary',title:'Database observation',summary:'Connections rose',evidence_ids:[]}]
  }});
  assert.equal((html.match(/class="brief-lead">Pool saturation/g) || []).length, 1);
  assert.equal((html.match(/<h4>Next check<\/h4>/g) || []).length, 1);
  assert.match(html, /Database observation/);
  assert.match(html, /Still unconfirmed:/);
  assert.match(html, /Full assessment/);
});

test('conflicting investigator finding is visible rather than silently merged', () => {
  const render = helper('briefingPanel', 'investigationRefs', {
    safe:value=>String(value ?? ''), formatDate:value=>value, selectedEpisodeId:'episode',
    investigationRefs:()=>'', disclosure:(id,title,body)=>'<details><summary>'+title+'</summary>'+body+'</details>',
  });
  const html = render({investigation:{status:'ready', assessment:{summary:'Alert context',likely_mechanism:'Connection pressure',
    next_action:'Check active connections',expected_finding:'Near limit',uncertainty:'Caller unknown',hypotheses:[],connections:[]},
    findings:[{category:'investigator_assessment',title:'Initial finding',summary:'DNS resolution failure',evidence_ids:['log-1']}] }});
  assert.match(html, /Assessment discrepancy/);
  assert.match(html, /Connection pressure/);
  assert.match(html, /DNS resolution failure/);
  assert.match(html, /Still unconfirmed:/);
});

test('overview promotes only cited performance evidence', () => {
  const render = helper('overviewMetrics','evidencePanel', {
    metricChart:item=>item.label,
  });
  const signals = [
    {evidence_id:'metric-a',label:'Memory',component:'pod-a',values:[{},{}]},
    {evidence_id:'metric-b',label:'CPU',component:'pod-a',values:[{},{}]},
    {evidence_id:'metric-c',label:'Restarts',component:'pod-a',values:[{},{}]},
  ];
  assert.equal(render({pm_signals:signals,primary_hypothesis:{supporting_evidence:[]}}), '');
  const html = render({pm_signals:signals,primary_hypothesis:{supporting_evidence:[{evidence_id:'metric-a'},{evidence_id:'metric-b'},{evidence_id:'metric-c'}]}});
  assert.match(html, /MemoryCPU/);
  assert.doesNotMatch(html, /Restarts/);
});

test('metric chart distinguishes the reference period and selected deviation', () => {
  const render = helper('metricChart','metricsPanel', {
    safe:value=>String(value ?? ''), shortTime:value=>value, formatExactDate:value=>value,
    sparkline:()=>'<svg></svg>',
  });
  const html = render({evidence_id:'pm-1',metric:'pod_cpu_cores',label:'CPU',meaning:'Used by affected pod',
    baseline:'100 mCPU',peak:'900 mCPU',component:'pod-a',baseline_basis:'pre_alert',
    baseline_start:'before-start',baseline_end:'before-end',alert_timestamp:'peak',
    values:[{timestamp:'before-start',value:0.1},{timestamp:'peak',value:0.9}]});
  assert.match(html,/Before alert/);
  assert.match(html,/selected deviation, not alert time/);
  assert.match(html,/pod-a/);
});

test('investigation citations are escaped and point to unique observations', () => {
  const refs = helper('investigationRefs','investigationProgress', {
    safe: value => String(value ?? '').replaceAll('<','&lt;').replaceAll('"','&quot;'),
    logLabel: value=>value, icon:name=>'<i data-icon="'+name+'"></i>', shortTime:value=>value,
  });
  const html = refs({checks:[{id:'Q001',question:'<script>source text</script>'}]},['Q001']);
  assert.match(html,/data-investigation-ref="Q001"/);
  assert.doesNotMatch(html,/<script>/);
  const compact = refs({checks:[{id:'Q002',tool:'search_logs',question:'Which logs distinguish competing mechanisms?'}]},['Q002']);
  assert.match(compact,/<span>Source logs<\/span>/);
  assert.match(compact,/data-icon="logs"/);
  assert.match(compact,/title="Open retained logs evidence"/);
  assert.match(refs({checks:[]},['missing']),/Reference missing unavailable in this capture/);
});

test('interval labels report observed gaps without forecasting', () => {
  const label = helper('intervalLabel', 'filteredEpisodes');
  assert.equal(label(34, 2), '34 seconds between two episodes');
  assert.equal(label(14400, 3), '4 hours median of 2 gaps');
  assert.doesNotMatch(label(14400, 3), /every|next/i);
});

test('partial token usage is not displayed as complete accounting', () => {
  const progress = helper('investigationProgress','investigationResult', {
    safe:value=>String(value ?? ''), icon:()=>'', formatDate:value=>value,
    disclosure:(id,title,body)=>title+body,
  });
  const html = progress({status:'incomplete', checks:[], usage:{total_tokens:42,complete:false}, calls:[], token_budget:{accounted_total_tokens:640,maximum_total_tokens:12000,remaining_tokens:11360}});
  assert.match(html,/At least 42 tokens/);
  assert.match(html,/safety reserve prevents unbounded follow-up calls/);
  assert.match(html,/640 \/ 12,000/);
});

test('agent event times remain separate from the historical incident sequence', () => {
  const timeline = helper('investigationTimeline','metricMatters', {
    safe:value=>String(value ?? ''), formatDate:value=>value, investigationRefs:()=>'',
  });
  const html = timeline({status:'ready',checks:[{id:'Q1',question:'Why restarted?',status:'completed',tool:'resource_history',started_at:'analysis-time'}],finished_at:'done-time'});
  assert.match(html,/Agent activity/);
  assert.match(html,/analysis-time/);
  assert.match(html,/Assessment saved/);
});

test('historical model prose is labelled as a hypothesis rather than evidence', () => {
  const render = helper('investigationResult','investigationEvidence', {
    safe:value=>String(value ?? ''), formatDate:value=>value, disclosure:(id,title,body)=>title+body,
  });
  const html = render({episode:{reference:'EP-OLD',title:'Earlier episode',started_at:'earlier',status:'resolved',prior_hypothesis:{summary:'A prior model suspected connection pressure.'},captured_evidence:[{}]}});
  assert.match(html,/Earlier hypothesis, not proof/);
  assert.match(html,/A prior model suspected connection pressure/);
});

test('media upload is enabled only when the core and matching specialist are ready', () => {
  const availability = helper('mediaEvidenceAvailability', 'mediaEvidencePanel', {});
  const imageOnly = availability({
    core_investigator:{capability:{status:'ready'}},
    vision:{capability:{status:'ready'}}, audio:{capability:{status:'not_validated'}},
  });
  assert.equal(imageOnly.image, true);
  assert.equal(imageOnly.audio, false);
  const disabled = availability({
    core_investigator:{capability:{status:'not_validated'}},
    vision:{capability:{status:'ready'}}, audio:{capability:{status:'ready'}},
  });
  assert.equal(disabled.image, false);
  assert.equal(disabled.audio, false);
});
