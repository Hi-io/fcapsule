const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../fcapsule/ui/assets/app.js'), 'utf8');
const start = source.indexOf('function publicationProvenance(');
const end = source.indexOf('function investigationProgress(', start);
const renderer = source.slice(start, end);
const render = vm.runInNewContext(renderer + '\npublicationProvenance', {
  safe:value=>String(value ?? '').replace(/[&<>"']/g, char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char])),
  fmt:new Intl.NumberFormat('en-US'),
});

function record(overrides = {}) {
  return {
    status:'queued', receipt_status:'not_yet_confirmed', revision:3, attempts:0,
    last_error:null, remote_case_id:null,
    ...overrides,
  };
}

test('no linked deliveries keep the report uncluttered', () => {
  assert.equal(render([]), '');
  assert.equal(render(null), '');
});

test('queued publication is explicitly unsent and has no receipt link', () => {
  const html = render([record()]);
  assert.match(html, /Collective publication/);
  assert.match(html, /Queued; not yet sent/);
  assert.match(html, /No successful response recorded/);
  assert.doesNotMatch(html, /remembers|published|href="\/collective\?case=/i);
});

test('retrying and failed deliveries show bounded failure reasons as escaped text', () => {
  const retry = render([record({status:'retry_scheduled', attempts:2, last_error:'Collective is unavailable; delivery will retry.'})]);
  assert.match(retry, /Retry scheduled/);
  assert.match(retry, /2 attempts/);
  assert.match(retry, /Collective is unavailable; delivery will retry\./);

  const failed = render([record({status:'attention_required', last_error:'<script>never()</script>'})]);
  assert.match(failed, /Failed; attention required/);
  assert.match(failed, /&lt;script&gt;never\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(failed, /<script>/);
});

test('a confirmed remote record ID links into Collective and is escaped in the label', () => {
  const html = render([record({status:'published', receipt_status:'remote_id_recorded', remote_case_id:'case/one?<id>'})]);
  assert.match(html, /Collective response succeeded/);
  assert.match(html, /href="\/collective\?case=case%2Fone%3F%3Cid%3E"/);
  assert.match(html, /<code>case\/one\?&lt;id&gt;<\/code>/);
  assert.doesNotMatch(html, /remembers/);
});

test('missing and legacy receipts are not turned into guessed Collective links', () => {
  const missing = render([record({status:'published', receipt_status:'remote_id_not_returned'})]);
  assert.match(missing, /No Collective record ID returned/);
  assert.doesNotMatch(missing, /href="\/collective\?case=/);

  const legacy = render([record({status:'published', receipt_status:'legacy_receipt_unknown', remote_case_id:'old-id'})]);
  assert.match(legacy, /Receipt unavailable for this older record/);
  assert.doesNotMatch(legacy, /old-id|href="\/collective\?case=/);
});

test('report overview renders episode-level provenance beside investigation history', () => {
  assert.match(source, /memoryContribution\(ai \|\| \{\}\) \+ publicationProvenance\(payload\.publication_provenance \|\| \[\]\)/);
});
