const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../fcapsule/ui/assets/app.js'), 'utf8');
const start = source.indexOf('function atlasRecords(');
const end = source.indexOf('function atlasRender(');
const helpers = source.slice(start, end);
const context = {
  safe:value=>String(value ?? '').replace(/[&<>"']/g, char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char])),
  formatDate:value=>String(value ?? ''),
  icon:name=>`<i data-icon="${name}"></i>`,
  URLSearchParams,
  atlasQuery:'', atlasScope:'',
  atlasSelectedPattern:'pattern/one', atlasSelectedCase:'case/one',
};

function render(name, expression, extras = {}) {
  return vm.runInNewContext(helpers + `\n${expression}`, {...context,...extras});
}

test('pattern detail presents recurrence as similarity, with linked cases and typed observations', () => {
  const html = render('atlasPatternDetail', `atlasPatternDetail({pattern:{
    id:'pattern/one',kind:'observation',key:'deployment',value:'release-42',unit:'',case_count:6,instance_count:3,
    first_seen:'2026-09-01T00:00:00Z',last_seen:'2026-09-12T00:00:00Z',
    interpretation:'Observed co-occurrence only; this is not evidence of a shared cause.'
  },cases:[{id:'case-one',summary:'Retained rollout event',instance_id:'edge-west'}]})`);
  assert.match(html, /Similarity pattern/);
  assert.match(html, /Seen in 6 cases across 3 instances/);
  assert.match(html, /deployment: release-42/);
  assert.match(html, /Observed co-occurrence only; this is not evidence of a shared cause/);
  assert.doesNotMatch(html, /No structured facts were returned/);
  assert.doesNotMatch(html, /No provenance details were returned/);
  assert.match(html, /<code>pattern\/one<\/code>/);
  assert.match(html, /case-one/);
  assert.match(html, /not evidence of a shared cause/);
});

test('case detail separates observations and hypotheses and shows source provenance safely', () => {
  const html = render('atlasCaseDetail', `atlasCaseDetail({case:{
    id:'case/one',instance_id:'edge-north',episode_id:'episode-7',observed_at:'2026-09-12T03:00:00Z',
    scope:{cluster:'north',namespace:'checkout',service:'api'},summary:'Repeated 503 response',
    observations:[{kind:'fm',key:'alert_family',value:'CheckoutErrors'},
      {kind:'http',key:'status',value:503,source:'prometheus',observed_at:'2026-09-12T02:55:00Z',reference:'E-12'}],
    hypotheses:[{statement:'<script>dependency failure</script>',confidence:0.4,supporting_refs:['E-12']}]
  }})`);
  assert.match(html, /Retained observations \(facts\)/);
  assert.match(html, /status: 503/);
  assert.match(html, /<h2>CheckoutErrors · api<\/h2>/);
  assert.match(html, /atlas-case-summary/);
  assert.match(html, /Source: prometheus/);
  assert.match(html, /Observed: 2026-09-12T02:55:00Z/);
  assert.match(html, /Reference: E-12/);
  assert.match(html, /Unverified hypotheses/);
  assert.match(html, /&lt;script&gt;dependency failure&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /Instance/);
  assert.match(html, /edge-north/);
  assert.match(html, /Record/);
  assert.match(html, /episode-7/);
});

test('local deletion warns that an Atlas copy is retained', () => {
  assert.match(source, /A case already published to Atlas will remain there/);
});

test('Atlas retrieval trail links case IDs without treating them as FCAPSule evidence', () => {
  const code = helpers + source.slice(source.indexOf('function relatedAtlasCases('), source.indexOf('function atlasRender('));
  const renderTrail = vm.runInNewContext(code + '\nrelatedAtlasCases', {...context});
  const html = renderTrail({context:{atlas_cases:[{
    atlas_case_id:'prior-case',summary:'Earlier timeout',instance_id:'edge-east',relation:'fingerprint_match',
  }]}});
  assert.match(html, /href="\/atlas\?case=prior-case"/);
  assert.match(html, /not FCAPSule evidence citations/);
  assert.match(html, /Fingerprint match/);
  assert.equal(renderTrail({context:{atlas_cases:[]}}), '');
});

test('Atlas links preserve search context without exposing a misleading similarity percentage', () => {
  const html = render('atlasCaseLink', `atlasCaseLink({id:'case/one',score:1,relation:'lexical_similarity',instance_id:'west'})`,
    {atlasQuery:'queue timeout',atlasScope:'cluster-a'});
  assert.match(html, /case%2Fone&amp;q=queue\+timeout&amp;scope=cluster-a/);
  assert.match(html, /Text match/);
  assert.doesNotMatch(html, /100% similarity/);
});

test('Atlas retrieval shows no-match, pending and unavailable states without inventing empty success', () => {
  const code = helpers + source.slice(source.indexOf('function relatedAtlasCases('), source.indexOf('function atlasRender('));
  const renderTrail = vm.runInNewContext(code + '\nrelatedAtlasCases', {...context});
  for (const [status, text] of [
    ['no_matches','does not establish that a cause is new or absent'],
    ['pending','retrieval is pending'],
    ['unavailable','was unavailable'],
  ]) assert.match(renderTrail({context:{atlas_cases:[],atlas_retrieval:{status}}}), new RegExp(text));
  for (const status of ['disabled','skipped_no_query','skipped_no_time']) {
    assert.equal(renderTrail({context:{atlas_cases:[],atlas_retrieval:{status}}}), '');
  }
});

test('Atlas failures distinguish configuration and disabled reads from empty results', () => {
  const code = source.slice(source.indexOf('function atlasFailureText('), source.indexOf('function relatedAtlasCases('));
  const failureText = vm.runInNewContext(code + '\natlasFailureText', {});
  assert.match(failureText('', 'not_configured'), /not configured/);
  assert.match(failureText('', 'disabled'), /disabled/);
  assert.match(failureText('', 'unavailable'), /unavailable/);
});

test('Atlas settings call out failed publishes and never render a saved token', () => {
  const code = source.slice(source.indexOf('function atlasSettingsForm('), source.indexOf('async function loadAtlasSettings('));
  const renderSettings = vm.runInNewContext(code + '\natlasSettingsForm', {
    ...context, fmt:new Intl.NumberFormat('en-US'), window:{},
  });
  const html = renderSettings({url:'https://atlas.example.test',instance_id:'west',token_configured:true,
    token:'never-render-this-secret',read_enabled:true,publish_enabled:false,pending_count:2,failed_count:3});
  assert.match(html, /3 failed publishes/);
  assert.match(html, /id="retry-atlas-failed"[^>]* disabled/);
  assert.match(html, /Configure a service URL and token/);
  assert.doesNotMatch(html, /never-render-this-secret/);
});
