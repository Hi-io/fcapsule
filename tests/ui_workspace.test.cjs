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
