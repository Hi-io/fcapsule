const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../fcapsule/ui/assets/app.js'), 'utf8');
const chunk = (from, to) => source.slice(source.indexOf('function ' + from + '('), source.indexOf('function ' + to + '('));
const escape = source.slice(source.indexOf('const safe ='), source.indexOf('const shortTime ='));
const context = {
  openDisclosures:new Set(), selectedEpisodeId:'episode-one',
  formatDate:value=>value, shortTime:value=>value, formatExactDate:value=>value,
};
const helpers = vm.runInNewContext(escape + chunk('disclosure','reportId') + chunk('logLabel','investigationTimeline') + '\n({briefingPanel,investigationProgress,investigationEvidence})', context);

test('rationale links exact retained sources without repeating a second citation list', () => {
  const run = {status:'ready', context:{evidence:[{id:'E123', domain:'metric_anomaly',title:'Checkout latency'}]},
    assessment:{likely_mechanism:'Latency increased',basis:'E123 shows 29 of 49 samples above threshold.',evidence_ids:['E123'],next_action:'Inspect downstream latency',expected_finding:'A slower dependency',uncertainty:'Cause unconfirmed'}};
  const html = helpers.briefingPanel({investigation:run});
  assert.equal((html.match(/data-investigation-ref="E123"/g) || []).length, 1);
  assert.match(html, /Metric: Checkout latency/);
  assert.match(html, /shows 29 of 49 samples above threshold/);
  assert.match(html, /data-disclosure="assessment-basis"\s*>/);
  assert.match(html, /data-disclosure="expected-finding"\s*>/);
  assert.match(html, /Still unconfirmed/);
});

test('retained prose-only references have an evidence destination even without formal citation IDs', () => {
  const run = {status:'ready',context:{evidence:[{id:'E-only-prose',domain:'metric_anomaly',title:'Latency',summary:'29 samples above threshold'}]},
    assessment:{basis:'E-only-prose supports this observation.',evidence_ids:[]}};
  assert.match(helpers.briefingPanel({investigation:run}), /data-investigation-ref="E-only-prose"/);
  assert.match(helpers.investigationEvidence(run), /id="disclosure-agent-E-only-prose"/);
});

test('assessment basis uses neutral wording only for inconclusive assessments', () => {
  for (const [status, label] of [['ready','Why this fits'],['inconclusive','Assessment basis']]) {
    const html = helpers.briefingPanel({investigation:{status,
      context:{evidence:[{id:'E123',domain:'metric_anomaly',title:'Latency'}]},
      assessment:{basis:'E123 records the observation.',evidence_ids:['E123']}}});
    assert.match(html, new RegExp('<summary id="disclosure-assessment-basis">' + label));
    assert.match(html, /data-disclosure="assessment-basis"/);
    assert.match(html, /data-investigation-ref="E123"/);
    if (status === 'inconclusive') {
      assert.doesNotMatch(html, /Why this fits/);
      assert.match(html, /Why no cause is asserted/);
    }
  }
});

test('zero-call input budget failure replaces deterministic contract wording in both views', () => {
  const contract = "The returned assessment did not satisfy FCAPSule's grounding and structure contract.";
  const run = {status:'inconclusive',episode_id:'episode-<budget>',calls:[],validation_error:'Input size budget reached',
    finished_at:'2026-09-23T13:50:00Z',
    checks:[{id:'Q001',tool:'search_logs',status:'completed',result:{summary:'Retained source observation'}}],
    assessment:{provenance:'deterministic_abstention',summary:'No validated root-cause conclusion is available.',
      uncertainty:contract,hypotheses:[{explanation:'Unresolved',status:'unresolved',reason:contract}]}};
  const before = JSON.stringify(run);
  for (const detailed of [false,true]) {
    const html = helpers.briefingPanel({investigation:run}, detailed, '<div data-evidence-workspace>Retained inputs</div>');
    assert.match(html,/Prompt budget exceeded before any model call/);
    assert.match(html,/role="status" aria-live="polite"/);
    assert.match(html,/Retained evidence and source checks remain available/);
    assert.match(html,/Last attempt 2026-09-23T13:50:00Z/);
    assert.match(html,/data-investigate="episode-&lt;budget&gt;"/);
    assert.match(html,/data-evidence-workspace/);
    assert.doesNotMatch(html,/grounding and structure contract|Why no cause is asserted|Assessment inconclusive|No supported cause yet/);
  }
  assert.match(helpers.investigationProgress(run,true),/Retained source observation/);
  assert.equal(JSON.stringify(run),before);
});

test('zero-call input budget failure is shown without an assessment and for interrupted records', () => {
  for (const status of ['inconclusive','incomplete']) {
    for (const detailed of [false,true]) {
      const html = helpers.briefingPanel({investigation:{status,calls:[],episode_id:'episode-one',
        validation_error:'Input size budget reached'}},detailed);
      assert.match(html,/Prompt budget exceeded before any model call/);
      assert.doesNotMatch(html,/evidence contract|before validation/);
    }
  }
});

test('missing call history and actual model or grounding failures retain their original wording', () => {
  const contract = "The returned assessment did not satisfy FCAPSule's grounding and structure contract.";
  const base = {status:'inconclusive',episode_id:'episode-one',calls:[],validation_error:'Input size budget reached',
    assessment:{provenance:'deterministic_abstention',uncertainty:contract,
      hypotheses:[{explanation:'Unresolved',status:'unresolved',reason:contract}]}};
  const variants = [
    {calls:undefined}, {calls:null}, {calls:{}}, {calls:[{status:'completed'}]}, {calls:[{status:'failed'}]},
    {validation_error:'Unavailable evidence reference'}, {validation_error:'Investigation token budget reached'},
    {validation_error:undefined}, {status:'ready'},
    {assessment:{uncertainty:contract,hypotheses:base.assessment.hypotheses}},
  ];
  for (const changes of variants) {
    for (const detailed of [false,true]) {
      const html = helpers.briefingPanel({investigation:{...base,...changes}},detailed);
      assert.doesNotMatch(html,/Prompt budget exceeded before any model call/);
      assert.match(html,/grounding and structure contract/);
      if (!detailed && (changes.status || base.status) === 'inconclusive') assert.match(html,/Why no cause is asserted/);
    }
  }
});

test('assessment source counts use singular, plural and no badge for zero sources', () => {
  for (const status of ['ready','inconclusive']) {
    for (const count of [0,1,2]) {
      const ids = Array.from({length:count},(_,index)=>'E' + index);
      const html = helpers.briefingPanel({investigation:{status,
        context:{evidence:ids.map(id=>({id,domain:'metric_anomaly',title:id}))},
        assessment:{basis:'Retained observations.',evidence_ids:ids}}});
      const summary = html.match(/<summary id="disclosure-assessment-basis">(.*?)<\/summary>/)[1];
      if (count) assert.match(summary,new RegExp('<span class="inline-count">' + count + (count === 1 ? ' source' : ' sources') + '</span>'));
      else assert.doesNotMatch(summary,/inline-count/);
      assert.doesNotMatch(summary,/1 sources/);
    }
  }
});

test('compact activity uses individually expandable checks, real statuses and a token footer', () => {
  const html = helpers.investigationProgress({status:'ready',episode_id:'episode-one',usage:{total_tokens:6205,complete:true},checks:[
    {id:'Q001',tool:'workload_state',question:'Is the workload running?',status:'completed',result:{summary:'Pod running'}},
    {id:'Q002',tool:'search_logs',question:'What logs were retained?',status:'completed',result:{error:'Source unavailable'}},
  ]},true);
  assert.match(html, /compact-checks/);
  assert.match(html, /data-disclosure="activity-Q001"\s*>/);
  assert.match(html, /Is the workload running\?/);
  assert.match(html, /Pod running/);
  assert.match(html, /agent-step unavailable/);
  assert.match(html, /<summary id="disclosure-activity-Q001">.*sr-only.*completed.*<\/summary>/);
  assert.match(html, /<summary id="disclosure-activity-Q002">.*detail-count.*unavailable.*<\/summary>/);
  assert.match(html, /Source unavailable/);
  assert.equal((html.match(/data-icon="check"/g) || []).length,1);
  assert.ok(html.indexOf('activity-footer') > html.indexOf('</ol>'));
  assert.match(html, /6,205 tokens/);
});

test('running and empty activity do not invent completed checks', () => {
  const running = helpers.investigationProgress({status:'running',checks:[{id:'Q001',status:'running',question:'Checking workload'}]},true);
  assert.match(running, /spinner/);
  assert.match(running, /<summary id="disclosure-activity-Q001">.*sr-only.*running.*<\/summary>/);
  assert.doesNotMatch(running, /data-icon="check"|Reassess episode/);
  const empty = helpers.investigationProgress({status:'not_started'},true);
  assert.match(empty, /No checks recorded yet/);
  assert.match(empty, /Usage pending/);
});

test('uncertainty and detailed model prose name retained sources and escape hostile markup', () => {
  const run = {status:'ready',context:{evidence:[{id:'E123',domain:'metric_anomaly',title:'Latency'}]},
    assessment:{likely_mechanism:'Unconfirmed',uncertainty:'E123 does not prove <script>cause</script>',
      hypotheses:[{explanation:'Slow dependency',status:'unresolved',reason:'E123 could also mean <img src=x> pressure'}]},
    findings:[{title:'Observation',summary:'E123 indicates a slowdown',next_check:'Compare E123'}]};
  const html = helpers.briefingPanel({investigation:run});
  assert.match(html, /Still unconfirmed:<\/strong> <button/);
  assert.match(html, /&lt;script&gt;cause&lt;\/script&gt;/);
  const detailed = helpers.briefingPanel({investigation:run},true);
  assert.equal((detailed.match(/data-investigation-ref="E123"/g) || []).length,3);
  assert.doesNotMatch(detailed, /<img/);
});
