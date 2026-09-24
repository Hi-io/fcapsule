const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../fcapsule/ui/assets/app.js'), 'utf8');
const uiStates = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/ui_states.json'), 'utf8'));
function helper(name, next, context = {}) {
  const code = source.slice(source.indexOf(`function ${name}(`), source.indexOf(`function ${next}(`));
  return vm.runInNewContext(code + '\n' + name, {investigationReferenceMatches:()=>[], inlineDisclosure:context.disclosure, ...context});
}

test('image citations show readable observations and original evidence before raw extraction', () => {
  const render = helper('investigationEvidence', 'investigationTimeline', {
    safe:value=>String(value ?? '').replaceAll('<','&lt;'), formatDate:value=>value,
    icon:name=>`<i>${name}</i>`,
    evidenceArtifact:helper('evidenceArtifact','bindEvidenceImages',{safe:value=>String(value ?? '').replaceAll('<','&lt;'),icon:()=>''}),
    disclosure:(id,title,body)=>`<details data-id="${id}"><summary>${title}</summary>${body}</details>`,
  });
  const run = {assessment:{evidence_ids:['A-image']},context:{evidence:[{
    id:'A-image',domain:'image_evidence',attachment_id:'image',title:'Image evidence: targets.png',
    time_range:{observed_at:'2026-09-23T02:16:00Z'},summary:'Target down',
  }]}};
  const html = render(run, [{attachment_id:'image',artifact_url:'/evidence/image.png',extraction:{
    observations:[{fact:'Endpoint <metrics> returned 404'}],limitation:'Visible state only.'}}]);
  assert.match(html, /class="media-observation-facts"/);
  assert.match(html, /Endpoint &lt;metrics> returned 404/);
  assert.match(html, /observed 2026-09-23T02:16:00Z/);
  assert.match(html, /href="\/evidence\/image.png"/);
  assert.match(html, /data-preview-image="\/evidence\/image.png"/);
  assert.match(html, /Download original/);
  assert.doesNotMatch(html, /target="_blank"/);
  assert.match(html, /Full extraction/);
  assert.doesNotMatch(html, /Captured in|image_evidence/);
  assert.ok(html.indexOf('media-observation-facts') < html.indexOf('Full extraction'));
});

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

test('investigation settings expose the active provider without revealing credentials', () => {
  const controls = new Map();
  const app = {innerHTML:''};
  const document = {querySelector(selector) {
    if (!controls.has(selector)) controls.set(selector, {listeners:{}, addEventListener(name, callback) { this.listeners[name] = callback; }});
    return controls.get(selector);
  }};
  const render = helper('renderSettings', 'applicationTable', {
    app, document, window:{}, safe:value=>String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),
    icon:name=>`<i>${name}</i>`, capability:item=>`<span>${item?.status || 'Not configured'}</span>`,
    saveGeneralSettings:()=>{}, saveAiSettings:()=>{}, validateAiSettings:()=>{}, saveMediaSettings:()=>{}, validateMediaSettings:()=>{},
  });
  const ai = {provider:'openrouter',providers:[{id:'deepseek',label:'DeepSeek'},{id:'openrouter',label:'OpenRouter'}],model:'deepseek/deepseek-v4-pro-0813',models:[
    {model_id:'deepseek-v4-pro',provider:'deepseek',enabled:true,max_tokens:3600},
    {model_id:'deepseek/deepseek-v4-pro-0813',provider:'openrouter',enabled:true,max_tokens:3600},
    {model_id:'deepseek/deepseek-v4-flash-0731',provider:'openrouter',enabled:false,max_tokens:2400},
  ],api_key_configured:true,api_key:'never-render-this',capability:{status:'ready'},max_tokens:2000,max_total_tokens:16000,max_prompt_tokens:8000,max_checks:1};
  controls.set('#ai-model', {value:ai.model});
  controls.set('#ai-model-options', {innerHTML:''});
  render({ai,media:{api_key_configured:false,vision:{model:'vision-model'},audio:{model:'audio-model'}},settings:{incident_retention_days:30}});
  assert.match(app.innerHTML, /Active provider<\/strong> OpenRouter/);
  assert.match(app.innerHTML, /deepseek\/deepseek-v4-pro-0813/);
  assert.match(app.innerHTML, /API key<\/strong> Configured/);
  assert.match(app.innerHTML, /value="openrouter" selected/);
  assert.match(app.innerHTML, /Separate from the investigation provider key above/);
  assert.doesNotMatch(app.innerHTML, /never-render-this/);

  controls.get('#ai-provider').listeners.change({target:{value:'deepseek'}});
  assert.equal(controls.get('#ai-model').value, 'deepseek-v4-pro');
  assert.match(controls.get('#ai-model-options').innerHTML, /deepseek-v4-pro/);
  assert.match(controls.get('#ai-provider-hint').textContent, /DeepSeek/);
  assert.match(controls.get('#ai-key').placeholder, /Optional key for DeepSeek/);
  assert.equal(controls.get('label[for="ai-key"]').textContent, 'DeepSeek API key');
  assert.match(app.innerHTML, /Active provider<\/strong> OpenRouter/);
  assert.match(app.innerHTML, /API key<\/strong> Configured/);
  controls.get('#ai-provider').listeners.change({target:{value:'openrouter'}});
  assert.equal(controls.get('#ai-model').value, 'deepseek/deepseek-v4-pro-0813');
  assert.match(controls.get('#ai-model-options').innerHTML, /deepseek\/deepseek-v4-flash-0731/);
});

test('investigation settings save the explicitly selected provider and model', async () => {
  const values = {'#ai-provider':'openrouter','#ai-model':'deepseek/deepseek-v4-pro-0813','#ai-max-tokens':'2400','#ai-total-tokens':'20000','#ai-prompt-tokens':'9000','#ai-max-checks':'2','#ai-key':'openrouter-secret'};
  let request;
  const code = source.slice(source.indexOf('async function saveAiSettings('), source.indexOf('async function validateAiSettings('));
  const save = vm.runInNewContext(code + '\nsaveAiSettings', {
    document:{querySelector:selector=>({value:values[selector]})},
    fetch:async(_url, options)=>{request=JSON.parse(options.body);return {ok:true,json:async()=>({provider:'openrouter'})};},
    window:{}, lastState:{ai:{}}, renderSettings:()=>{},
  });
  await save();
  assert.deepEqual(request, {provider:'openrouter',model:'deepseek/deepseek-v4-pro-0813',max_tokens:2400,max_total_tokens:20000,max_prompt_tokens:9000,max_checks:2,api_key:'openrouter-secret'});
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

test('representative investigation states have distinct operator guidance', () => {
  const render = helper('briefingPanel', 'investigationRefs', {
    safe:value=>String(value ?? ''), formatDate:value=>value, selectedEpisodeId:'episode-example',
    investigationRefs:()=>'', investigationText:(_run,text)=>String(text ?? ''), disclosure:(id,title,body)=>'<details><summary>'+title+'</summary>'+body+'</details>',
  });
  const ready = render({investigation:uiStates.ready});
  const inconclusive = render({investigation:uiStates.inconclusive});
  const running = render({investigation:uiStates.running});
  const missing = render({investigation:uiStates.missing_provider});
  const missingSource = render({investigation:uiStates.missing_source});
  const interrupted = render({investigation:uiStates.interrupted});
  assert.match(ready,/The inventory dependency timed out/);
  assert.match(inconclusive,/No supported cause yet/);
  assert.match(inconclusive,/no retained metrics/);
  assert.match(running,/Investigating/);
  assert.doesNotMatch(running,/Reassess episode/);
  assert.match(missing,/Provider key required/);
  assert.match(missing,/Open Settings/);
  assert.match(missingSource,/No OpenSearch log records were available/);
  assert.match(interrupted,/Investigation needs attention/);
  assert.match(interrupted,/Provider unavailable/);
  assert.match(render({investigation:{status:'not_started',episode_id:'episode-example'}}),/Start investigation/);
});

test('multi-alert and archived fixtures retain scope and lifecycle controls', () => {
  const context = helper('episodeContext', 'formatDate', {
    reportId:()=> 'incident-one', reportTab:'overview', safe:value=>String(value ?? ''),
    relativeTime:value=>value, icon:()=>'',
  });
  const html = context(uiStates.multi_alert);
  assert.match(html,/Episode EP-EXAMPLE/);
  assert.match(html,/2 captured alerts/);
  assert.doesNotMatch(html,/INC-ONE/);
  const table = helper('episodeTable', 'episodeContext', {
    lastState:{overview:{applications:[],related_groups:[]}}, selectedEpisodeId:'episode-archived', selectedReport:{},
    reportLoading:false, reportError:'', episodeContext:()=>'', reportPanel:()=> '',
    queueFilters:{}, safe:value=>String(value ?? ''), status:value=>value, formatDate:value=>value,
    formatExactDate:value=>value, relativeTime:value=>value,
    resourceLabel:item=>item.app_id, recurrenceLabel:()=> '',
  });
  const archived = table([uiStates.archived], [], true);
  assert.match(archived,/data-restore="episode-archived"/);
  assert.match(archived,/data-delete="episode-archived"/);
  assert.doesNotMatch(archived,/data-archive="episode-archived"/);
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
    investigationRefs:()=>'', investigationText:(_run,text)=>String(text ?? ''), disclosure:(id,title,body)=>'<details><summary>'+title+'</summary>'+body+'</details>',
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
  assert.match(html, /<details><summary>Key observations<\/summary>/);
  assert.match(html, /<details><summary>Why this fits<\/summary>/);
  assert.match(html, /Still unconfirmed:/);
  assert.doesNotMatch(html, /Full assessment|Capture context|Explanations considered/);
  assert.match(html, /class="assessment-decision"/);
  const detailed = render({investigation:{status:'ready',assessment:{summary:'Alert scope',likely_mechanism:'Pool saturation',basis:'Connections reached the configured cap.',hypotheses:[{explanation:'Pool full',reason:'At cap',status:'supported'}]},findings:[{title:'Database observation',summary:'Connections rose'}]}},true);
  assert.match(detailed,/Capture context|Retained findings/);
  assert.doesNotMatch(detailed,/class="brief-lead"/);
});

test('conflicting investigator finding is visible rather than silently merged', () => {
  const render = helper('briefingPanel', 'investigationRefs', {
    safe:value=>String(value ?? ''), formatDate:value=>value, selectedEpisodeId:'episode',
    investigationRefs:()=>'', investigationText:(_run,text)=>String(text ?? ''), disclosure:(id,title,body)=>'<details><summary>'+title+'</summary>'+body+'</details>',
  });
  const html = render({investigation:{status:'ready', assessment:{summary:'Alert context',likely_mechanism:'Connection pressure',
    next_action:'Check active connections',expected_finding:'Near limit',uncertainty:'Caller unknown',hypotheses:[],connections:[]},
    findings:[{category:'investigator_assessment',title:'Initial finding',summary:'DNS resolution failure',evidence_ids:['log-1']}] }});
  assert.match(html, /Assessment discrepancy/);
  assert.match(html, /Connection pressure/);
  assert.match(html, /DNS resolution failure/);
  assert.match(html, /Still unconfirmed:/);
});

test('overview previews one relevant series and links to all retained performance evidence', () => {
  const render = helper('overviewMetrics','evidencePanel', {
    safe:value=>String(value ?? ''), icon:()=>'', sparkline:()=>'<svg></svg>',
    shortTime:value=>value, formatExactDate:value=>value,
  });
  const values = [{timestamp:'2026-09-23T00:00:00Z',value:1},{timestamp:'2026-09-23T00:10:00Z',value:2}];
  const signals = [
    {evidence_id:'metric-a',label:'Memory',component:'pod-a',values},
    {evidence_id:'metric-b',label:'CPU',component:'pod-a',values},
    {evidence_id:'metric-c',label:'Restarts',component:'pod-a',values},
  ];
  assert.equal(render({pm_signals:signals,primary_hypothesis:{supporting_evidence:[]}}), '');
  const html = render({pm_signals:signals,primary_hypothesis:{supporting_evidence:[{evidence_id:'metric-a'},{evidence_id:'metric-b'},{evidence_id:'metric-c'}]}});
  assert.match(html, /Memory/);
  assert.doesNotMatch(html, /CPU|Restarts/);
  assert.match(html, /1 of 3 captured series/);
  assert.match(html, /data-evidence-link="metric-a" data-domain="metrics"/);
  assert.match(html, /All performance evidence/);
  assert.equal((html.match(/<svg>/g) || []).length, 1);
  assert.equal(render({pm_signals:[{...signals[0],signal_origin:'alert_rule',values:[{value:null},{value:null}]}]}), '');
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
  assert.match(html,/Investigation activity/);
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

test('assessment revisions without attachments do not render an empty media section', () => {
  const render = helper('mediaEvidencePanel', 'sourceReviewPanel', {
    selectedEpisodeId:'episode', safe:value=>String(value ?? ''), formatDate:value=>value,
    disclosure:(id,title,body,count)=>'<details id="'+id+'"><summary>'+title+' '+count+'</summary>'+body+'</details>',
  });
  assert.equal(render({}), '');
  const html = render({investigation_revisions:[
    {reason:'initial',status:'ready',created_at:'first'},
    {reason:'new_alert',status:'ready',created_at:'second'},
  ]});
  assert.equal((html.match(/Assessment history/g) || []).length, 1);
  assert.doesNotMatch(html, /operator-evidence|Operator evidence|media-evidence/);
  assert.match(html, /first/);
  assert.match(html, /second/);
});

test('alert graph uses timestamps, preserves gaps and distinguishes threshold from start', () => {
  const render = helper('sparkline','evidenceReferences',{safe:value=>String(value)});
  const html = render({signal_origin:'alert_rule',label:'up',metric:'up',threshold:0,baseline_value:1,
    alert_timestamp:'2026-09-23T00:02:00Z', values:[
      {timestamp:'2026-09-23T00:00:00Z',value:1},
      {timestamp:'2026-09-23T00:01:00Z',value:null},
      {timestamp:'2026-09-23T00:02:00Z',value:0},
      {timestamp:'2026-09-23T00:04:00Z',value:0}]});
  assert.match(html,/class="threshold"/);
  assert.match(html,/class="alert-start" x1="207"/);
  assert.match(html,/d="M42.0,8.0  M207.0,102.0 L372.0,102.0"/);
  assert.doesNotMatch(html,/NaN|undefined/);
});

test('completed checks expose observations and explicit failures, not invented answers', () => {
  const answer = helper('checkObservation','investigationProgress',{logLabel:value=>value});
  assert.match(answer({status:'completed',result:{observations:[{metric:'up',min:0,max:1}]}}),/up: 0 to 1/);
  assert.match(answer({status:'unavailable',error:'Source timed out'}),/Source timed out/);
  assert.match(answer({status:'completed',result:{targets:[]}}),/No matching targets/);
  assert.match(answer({status:'completed',result:{other:'data'}}),/Source response retained/);
});

test('scope displays retained pod and namespace without inferring identity from prose', () => {
  const render = helper('investigationScope','checkObservation',{safe:value=>String(value)});
  const html = render({investigation:{context:{scope:{pod:'checkout-123',namespace:'commerce'},recurrence:{previous_count:3}}}});
  assert.match(html,/checkout-123/); assert.match(html,/commerce/);
  assert.match(html,/3 earlier same-signature episodes/);
  assert.doesNotMatch(html,/same cause/);
});

test('text context availability depends on the core, not specialist models', () => {
  const available = helper('mediaEvidenceAvailability','mediaEvidencePanel');
  assert.equal(available({core_investigator:{capability:{status:'ready'}}}).text,true);
  assert.equal(available({}).text,false);
});

test('a corrected attachment can be reviewed again without claiming it changed the diagnosis', () => {
  const render = helper('mediaEvidencePanel','sourceReviewPanel',{
    selectedEpisodeId:'episode', safe:value=>String(value ?? ''), formatDate:value=>value,
    attachmentSummary:()=> 'An observation', disclosure:(id,title,body)=>title+body,
  });
  const payload={investigation:{started_at:'2026-09-23T01:00:00Z',calls:[{visible_evidence_ids:['A-note']}]},
    media_evidence:[{attachment_id:'note',kind:'text',filename:'note.txt',status:'ready',updated_at:'2026-09-23T01:02:00Z',uploaded_at:'2026-09-23T00:59:00Z'}]};
  assert.match(render(payload),/Review new evidence/);
  assert.match(render(payload),/uploaded 2026-09-23T00:59:00Z/);
  payload.media_evidence[0].updated_at='2026-09-23T00:59:00Z';
  assert.doesNotMatch(render(payload),/Review new evidence/);
  assert.match(render(payload),/provided to latest investigation/);
});

test('missing alert series is not presented as a healthy graph', () => {
  const render=helper('metricsPanel','overviewMetrics',{safe:value=>String(value),metricChart:()=>''});
  assert.match(render({pm_signals:[]}),/not retained.*not backfilled/);
  assert.match(render({alert_metric_evidence:[{alertname:'Condition',status:'unavailable',reason:'query_failed'}]}),/Condition \(query failed\)/);
});
