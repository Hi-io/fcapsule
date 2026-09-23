const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../fcapsule/ui/assets/app.js'), 'utf8');
function helpers() {
  const code = source.slice(source.indexOf('function investigationRefs('), source.indexOf('function investigationScope('));
  const escaping = source.slice(source.indexOf('const safe ='), source.indexOf('const shortTime ='));
  const labels = source.slice(source.indexOf('function logLabel('), source.indexOf('function briefingPanel('));
  const icons = source.slice(source.indexOf('function icon('), source.indexOf('function quantity('));
  return vm.runInNewContext(escaping + labels + icons + code + '\n({investigationText, investigationRefs, safe})', {
    shortTime: value => value,
  });
}

const { investigationText, investigationRefs, safe } = helpers();
const evidenceRun = evidence => ({context:{evidence}});
const buttons = html => [...html.matchAll(/<button\b[^>]*data-investigation-ref="([^"]*)"[^>]*>/g)].map(match=>match[1]);

test('assessment prose renders exact retained references with descriptive source labels', () => {
  const run = {checks:[{id:'Q001',tool:'search_logs'}],context:{evidence:[
    {id:'E123456789abc',domain:'metric_anomaly',title:'CPU throttling'},
    {id:'Eabcdef123456',domain:'configuration',title:'Worker limits'},
    {id:'A-image',domain:'image_evidence',title:'Image evidence: targets.png'},
  ]}};
  const html = investigationText(run, 'Compare E123456789abc, Eabcdef123456 and Q001 (A-image).');
  assert.deepEqual(buttons(html), ['E123456789abc','Eabcdef123456','Q001','A-image']);
  assert.match(html, /^Compare <button/);
  assert.match(html, /<span>Metric: CPU throttling<\/span>/);
  assert.match(html, /<span>Config: Worker limits<\/span>/);
  assert.match(html, /<span>Source logs<\/span>/);
  assert.match(html, /<span>Image: targets.png<\/span>/);
  assert.match(html, /<\/button>, <button/);
  assert.match(html, /<\/button>\)\.$/);
  assert.doesNotMatch(html, /<span>E123456789abc<\/span>/);
});

test('inline references have native button semantics, domain metadata and accessible descriptions', () => {
  const run = evidenceRun([{id:'E-one',domain:'metric_anomaly',title:'CPU pressure'}]);
  const html = investigationText(run, 'E-one');
  assert.match(html, /type="button" class="evidence-link evidence-link-inline"/);
  assert.match(html, /data-investigation-domain="metric_anomaly"/);
  assert.match(html, /title="Open retained performance evidence: Metric: CPU pressure"/);
  assert.match(html, /aria-label="Open retained performance evidence: Metric: CPU pressure"/);
  assert.match(html, /data-icon="chart-no-axes-combined" aria-hidden="true"/);
});

test('matches are case-sensitive, longest-first and not fragments of larger identifiers', () => {
  const run = evidenceRun([
    {id:'E123',title:'Short observation'}, {id:'E123-abc',title:'Long observation'},
  ]);
  const fragments = ['xE123','E1234','E123_suffix','prefix-E123','E123-tail','e123','E123-abcdef','\u00e9E123','E123\u0301'];
  const prose = fragments.join(' ') + ' (E123), [E123-abc]; E123.';
  const html = investigationText(run, prose);
  assert.ok(html.startsWith(fragments.join(' ') + ' ('));
  assert.deepEqual(buttons(html), ['E123','E123-abc','E123']);
});

test('unknown IDs, arbitrary hex strings and redacted aliases never invent bindings', () => {
  const run = evidenceRun([{id:'A-0123456789abcdef',domain:'image_evidence',title:'Targets'}]);
  const prose = 'Unknown E987654321abc, deadbeefdeadbeef, A-<ID>, <UUID>, and A-<UUID>.';
  assert.equal(investigationText(run, prose), safe(prose));
  assert.deepEqual(buttons(investigationText(run, 'A-0123456789abcdef')), ['A-0123456789abcdef']);
});

test('prose, IDs, titles and domain attributes are escaped without treating HTML as markup', () => {
  const id = 'E-"<img>&\'';
  const domain = 'custom" onfocus="bad';
  const title = '<svg onload="bad"> & \'quoted\'';
  const prose = '<img src=x onerror="bad"> & ' + id + ' <script>bad()</script>';
  const html = investigationText(evidenceRun([{id,domain,title}]), prose);
  assert.deepEqual(buttons(html), [safe(id)]);
  assert.match(html, /^&lt;img src=x onerror=&quot;bad&quot;&gt; &amp; /);
  assert.ok(html.includes('data-investigation-domain="' + safe(domain) + '"'));
  assert.ok(html.includes(safe(title)));
  assert.ok(html.endsWith(' &lt;script&gt;bad()&lt;/script&gt;'));
  assert.doesNotMatch(html, /<(?:img|svg|script)\b| onfocus="bad"/);
});

test('regex punctuation in retained IDs is literal and generated labels are not rematched', () => {
  const run = evidenceRun([
    {id:'E.[one]+(x)?$',title:'E-other'}, {id:'E-other',title:'Another observation'},
  ]);
  const html = investigationText(run, 'E.[one]+(x)?$ then E-other and E.[one]+(x)?$.');
  assert.deepEqual(buttons(html), ['E.[one]+(x)?$','E-other','E.[one]+(x)?$']);
  assert.match(html, /<span>Observation: E-other<\/span>/);
  assert.equal(investigationText(run, 'Ezoneeex'), 'Ezoneeex');
});

test('duplicate IDs cannot bind to an arbitrary source, including across checks and evidence', () => {
  const run = {checks:[{id:'Q001',tool:'search_logs'}],context:{evidence:[
    {id:'Q001',domain:'metric_anomaly',title:'Different source'},
    {id:'E-unique',domain:'configuration',title:'Runtime'},
  ]}};
  const html = investigationText(run, 'Q001 and E-unique');
  assert.match(html, /Ambiguous source \(unresolved\)/);
  assert.deepEqual(buttons(html), ['E-unique']);
  assert.deepEqual(buttons(investigationRefs(run, ['Q001'])), []);
});

test('known redacted references remain explicitly unresolved even with a single candidate', () => {
  for (const id of ['A-<ID>','A-<UUID>','<ID>','[REDACTED]']) {
    const run = evidenceRun([{id,domain:'image_evidence',title:'Targets'}]);
    for (const html of [investigationText(run, 'Inspect ' + id + '.'), investigationRefs(run, [id])]) {
      assert.match(html, /Redacted source \(unresolved\)/);
      assert.doesNotMatch(html, /data-investigation-ref=|<ID>|<UUID>/);
      assert.ok(html.includes(safe(id)));
    }
  }
});

test('audio transcripts and operator context describe their actual source and limitations', () => {
  const evidence = [
    {id:'A-audio',domain:'audio_transcript',title:'Audio evidence: incident.wav'},
    {id:'A-note',domain:'operator_context',title:'Text evidence: handover.txt'},
    {id:'A-legacy',domain:'audio_evidence',title:'Audio evidence: original.wav'},
  ];
  const run = evidenceRun(evidence);
  for (const html of [investigationText(run, evidence.map(item=>item.id).join(', ')), investigationRefs(run, evidence.map(item=>item.id))]) {
    assert.match(html, /<span>Audio transcript: incident.wav<\/span>/);
    assert.match(html, /Open retained audio transcript evidence/);
    assert.match(html, /<span>Operator context \(unverified\): handover.txt<\/span>/);
    assert.match(html, /Open retained operator context \(unverified\) evidence/);
    assert.match(html, /<span>Audio: original.wav<\/span>/);
    assert.doesNotMatch(html, /Open retained observation evidence/);
  }
});

test('log labels are concise and compact labels retain their full accessible description', () => {
  const title = 'CPU ' + 'pressure '.repeat(30).trim();
  const run = evidenceRun([
    {id:'E-log',domain:'log_template',title:'{"level":"ERROR","message":"Worker <failed>","id":<NUM>}'},
    {id:'E-long',domain:'metric_anomaly',title},
  ]);
  const html = investigationText(run, 'E-log E-long');
  assert.match(html, /<span>Logs: Worker &lt;failed&gt;<\/span>/);
  assert.ok(html.includes('aria-label="Open retained performance evidence: Metric: ' + title + '"'));
  const label = html.match(/<span>(Metric: [^<]*)<\/span>/)[1];
  assert.ok(label.length <= 80);
  assert.ok(label.endsWith('...'));
});

test('citation lists preserve check labels, history, disambiguators and unavailable references', () => {
  const run = {checks:[
    {id:'Q001',tool:'search_logs'},
    {id:'Q002',tool:'search_logs',time_range:{start:'09:30:00'}},
    {id:'Q003',tool:'historical_episode',result:{episode:{reference:'EP-123'}}},
    {id:'Q004',question:'Is the target registered?'},
  ]};
  const html = investigationRefs(run, ['Q001','Q002','Q003','Q004','missing<id>']);
  assert.match(html, /<span>Source logs<\/span>/);
  assert.match(html, /title="Open retained logs evidence"/);
  assert.match(html, /<span>Source logs \u00b7 09:30:00<\/span>/);
  assert.match(html, /<span>Prior episode EP-123<\/span>/);
  assert.match(html, /<span>Check: Is the target registered\?<\/span>/);
  assert.match(html, /Reference missing&lt;id&gt; unavailable in this capture/);
  assert.doesNotMatch(html, /evidence-link-inline/);
});

test('absent inputs and untitled evidence produce safe prose and descriptive fallbacks', () => {
  assert.equal(investigationText(), '');
  assert.equal(investigationText(null, null), '');
  assert.equal(investigationText({}, 0), '0');
  assert.equal(investigationText(null, 'One & two\n<unknown>'), 'One &amp; two\n&lt;unknown&gt;');
  const run = evidenceRun([null,{id:''},{id:42},{id:'E-empty',domain:'metric_anomaly',title:'E-empty'}]);
  assert.match(investigationText(run, 'E-empty'), /<span>Metric<\/span>/);
  assert.equal(investigationRefs({}), '');
});

test('unknown domain and tool names cannot resolve to inherited JavaScript properties', () => {
  const run = {checks:[{id:'Q001',tool:'constructor',question:'Check source access'}],context:{evidence:[
    {id:'E-one',domain:'__proto__',title:'Custom source'},
    {id:'E-two',domain:'toString',title:'Another source'},
  ]}};
  const html = investigationText(run, 'Q001 E-one E-two');
  assert.match(html, /<span>Check: Check source access<\/span>/);
  assert.match(html, /<span>Observation: Custom source<\/span>/);
  assert.match(html, /<span>Observation: Another source<\/span>/);
  assert.deepEqual(buttons(html), ['Q001','E-one','E-two']);
});

test('reference rendering is repeatable without changing the retained evidence', () => {
  const item = Object.freeze({id:'E-one',domain:'metric_anomaly',title:'CPU pressure'});
  const run = Object.freeze({context:Object.freeze({evidence:Object.freeze([item])})});
  const html = investigationText(run, 'E-one supports E-one.');
  assert.equal(investigationText(run, 'E-one supports E-one.'), html);
  assert.deepEqual(buttons(html), ['E-one','E-one']);
  assert.deepEqual(buttons(investigationRefs(run, ['E-one'])), ['E-one']);
});
