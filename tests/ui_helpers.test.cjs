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
    safe: value => String(value ?? ''), status: value => value, formatDate: value => value, relativeTime: value => value,
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
  assert.match(html, /INC-ONE/);
  assert.match(html, /2 captured alerts/);
  assert.doesNotMatch(html, /select/);
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

test('investigation citations are escaped and point to unique observations', () => {
  const refs = helper('investigationRefs','investigationProgress', {
    safe: value => String(value ?? '').replaceAll('<','&lt;').replaceAll('"','&quot;'),
    logLabel: value=>value,
  });
  const html = refs({checks:[{id:'Q001',question:'<script>source text</script>'}]},['Q001']);
  assert.match(html,/data-investigation-ref="Q001"/);
  assert.doesNotMatch(html,/<script>/);
  const compact = refs({checks:[{id:'Q002',tool:'search_logs',question:'Which logs distinguish competing mechanisms?'}]},['Q002']);
  assert.match(compact,/>Source logs<\/button>/);
  assert.match(compact,/title="Which logs distinguish competing mechanisms\?"/);
});

test('partial token usage is not displayed as complete accounting', () => {
  const progress = helper('investigationProgress','investigationResult', {
    safe:value=>String(value ?? ''), icon:()=>'', formatDate:value=>value,
    disclosure:(id,title,body)=>title+body,
  });
  const html = progress({status:'incomplete', checks:[], usage:{total_tokens:42,complete:false}, calls:[]});
  assert.match(html,/At least 42 tokens/);
  assert.match(html,/not a full billing total/);
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
