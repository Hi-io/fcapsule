const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('fcapsule/ui/assets/app.js', 'utf8');
const fn = source.slice(source.indexOf('async function forgetEpisode('), source.indexOf('\nasync function separateRelatedEpisode('));

test('forget is a collapsed report action with explicit shared-memory scope', () => {
  assert.match(source, /disclosure\('episode-deletion', 'Delete episode'/);
  assert.match(source, /data-forget/);
  assert.match(fn, /Other episodes and knowledge already copied/);
});

test('declining confirmation makes no request', async () => {
  const context = vm.createContext({confirm: () => false, fetch: () => assert.fail('unexpected request')});
  vm.runInContext(fn, context);
  await context.forgetEpisode('episode', {});
});

test('confirmed deletion waits for shared withdrawal and clears selection', async () => {
  let calls = 0, refreshes = 0;
  const context = vm.createContext({
    confirm: () => true, alert: () => assert.fail('unexpected alert'),
    fetch: async (url, options) => {
      assert.equal(url, '/api/episodes/ep%2Fa/forget');
      assert.equal(options.method, 'POST');
      return {ok:true, json:async () => ({status: ++calls === 1 ? 'pending' : 'deleted'})};
    },
    setTimeout: callback => callback(), updateLocation() {},
    refresh: async () => { refreshes++; }, selectedReport:{}, selectedEpisodeId:'ep/a', requestSequence:0,
  });
  vm.runInContext(fn, context);
  const button = {};
  await context.forgetEpisode('ep/a', button);
  assert.equal(calls, 2);
  assert.equal(refreshes, 1);
  assert.equal(context.selectedEpisodeId, null);
  assert.equal(button.disabled, false);
});
