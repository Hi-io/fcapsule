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
    safe: value => String(value ?? ''), status: value => value, formatDate: value => value,
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
