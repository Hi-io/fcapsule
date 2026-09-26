const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../fcapsule/ui/assets/app.js'), 'utf8');
const escaping = source.slice(source.indexOf('const safe ='), source.indexOf('const shortTime ='));
const references = source.slice(source.indexOf('function investigationReferenceDescriptor('), source.indexOf('function investigationScope('));
const contribution = source.slice(source.indexOf('function memoryContribution('), source.indexOf('function investigationProgress('));
const render = vm.runInNewContext(escaping + references + contribution + '\nmemoryContribution', {
  icon:name=>`<i data-icon="${name}" aria-hidden="true"></i>`,
  logLabel:value=>value,
});

const historicalCheck = overrides => ({
  id:'Q001', tool:'historical_episode', status:'completed',
  question:'Was exporter discovery already failing?',
  distinguishes:'A prior target-selection gap from a new outage.',
  result:{
    availability:'retained',
    observations:[
      {summary:'Unrelated member observation.', source:{matches_current_alert_identity:false}},
      {summary:'Target scrape was absent during the alert window.', source:{matches_current_alert_identity:true}},
    ],
    episode:{reference:'EP-OLD', member_selection:{
      candidate_relation:'same_workload_different_pod',
      current_target:{name:'exporter-now'},
      selected_members:[{matches_current_alert_identity:true, target_relation:'same_workload_different_pod',
        resource:{name:'exporter-old'}}],
      limitation:'Prior and current pod identity differs; this relation does not establish cause.',
    }},
    limitation:'A prior capture does not prove the same cause.',
  },
  ...overrides,
});

test('no history keeps the default report uncluttered', () => {
  assert.equal(render({assessment:{evidence_ids:[]},checks:[]}), '');
});

test('a retrieved historical check is hidden until the assessment cites it', () => {
  assert.equal(render({assessment:{evidence_ids:['E-current']},checks:[historicalCheck()]}), '');
});

test('cited history without a provenance-matched observation is not presented as a match', () => {
  const check = historicalCheck({result:{availability:'retained', observations:[
    {summary:'Some prior observation.', source:{matches_current_alert_identity:false}},
  ], episode:{reference:'EP-OLD'}}});
  assert.equal(render({assessment:{historical_comparison:{evidence_ids:['Q001']}},checks:[check]}), '');
});

test('cited matching history shows its observation, recorded check purpose, grounded difference and source link', () => {
  const html = render({assessment:{historical_comparison:{evidence_ids:['Q001']}},checks:[historicalCheck()]});
  assert.match(html, /^<details class="history-contribution"><summary>How retained history informed this assessment<\/summary>/);
  assert.match(html, /Matched prior observation<\/dt><dd>Target scrape was absent during the alert window/);
  assert.doesNotMatch(html, /Unrelated member observation/);
  assert.match(html, /Recorded check purpose<\/dt><dd>A prior target-selection gap from a new outage/);
  assert.match(html, /from a different pod in the same workload \(exporter-old vs exporter-now\)/);
  assert.match(html, /A prior capture does not prove the same cause/);
  assert.match(html, /type="button" class="evidence-link" data-investigation-ref="Q001"/);
  assert.match(html, /aria-label="Open retained history evidence: Prior episode EP-OLD"/);
  assert.doesNotMatch(html, /root cause|learned RCA/i);
  assert.doesNotMatch(html, /<details class="history-contribution" open/);
});

test('history text is escaped before entering the report markup', () => {
  const check = historicalCheck({distinguishes:'<script>bad()</script>'});
  const html = render({assessment:{evidence_ids:['Q001']},checks:[check]});
  assert.match(html, /&lt;script&gt;bad\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});
