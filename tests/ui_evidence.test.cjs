const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../fcapsule/ui/assets/app.js'), 'utf8');
const chunk = (from, to) => source.slice(source.indexOf('function ' + from + '('), source.indexOf('function ' + to + '('));
const escape = source.slice(source.indexOf('const safe ='), source.indexOf('const shortTime ='));
const helpers = vm.runInNewContext(escape + chunk('disclosure','reportId') + chunk('evidenceArtifact','sourceReviewPanel') + chunk('investigationTimeline','metricMatters') + '\n({inlineDisclosure,evidenceArtifact,microphoneAvailability,investigationTimeline})', {
  openDisclosures:new Set(['basis']), formatDate:value=>value,
});

test('image originals use native thumbnail buttons and downloads, never a new tab', () => {
  const html = helpers.evidenceArtifact({kind:'image',filename:'<target>.png',artifact_url:'/api/evidence/one/file'});
  assert.match(html, /<button type="button" class="evidence-thumbnail"/);
  assert.match(html, /aria-label="View image: &lt;target&gt;.png"/);
  assert.match(html, /<img .*loading="lazy"/);
  assert.match(html, /download="&lt;target&gt;.png"/);
  assert.doesNotMatch(html, /target=|<target>/);
  assert.doesNotMatch(helpers.evidenceArtifact({kind:'text',artifact_url:'/note'}), /data-preview-image/);
  assert.match(helpers.evidenceArtifact({kind:'image'}), /Original file unavailable/);
});

test('compact assessment disclosures keep native semantics and retained expansion', () => {
  const html = helpers.inlineDisclosure('basis','Why this fits','<p>Source facts</p>','2 sources');
  assert.match(html, /class="inline-disclosure" data-disclosure="basis" open/);
  assert.match(html, /<summary id="disclosure-basis">Why this fits/);
  assert.match(html, /2 sources/);
  assert.doesNotMatch(html, /role="button"|evidence-disclosure/);
});

test('microphone availability distinguishes model, trust and browser limitations', () => {
  const supported = {getUserMedia:()=>{}};
  assert.match(helpers.microphoneAvailability({audio:false},true,supported,{}).hint,/validate the audio model/);
  const insecure = helpers.microphoneAvailability({audio:true},false,supported,{});
  assert.equal(insecure.ready,false);
  assert.match(insecure.hint,/trusted HTTPS or localhost/);
  assert.match(helpers.microphoneAvailability({audio:true},true,{},{}).hint,/not supported/);
  assert.equal(helpers.microphoneAvailability({audio:true},true,supported,null).ready,false);
  const ready = helpers.microphoneAvailability({audio:true},true,supported,{});
  assert.equal(ready.ready,true);
  assert.match(ready.hint,/permission will be requested/);
});

test('timeline orders upload time separately from observed time and retained reassessment', () => {
  const html = helpers.investigationTimeline({checks:[{id:'Q1',question:'Check',started_at:'2026-09-23T02:00:00Z',status:'completed',tool:'search_logs'}]}, [
    {attachment_id:'one',filename:'screenshot.png',kind:'image',status:'ready',uploaded_at:'2026-09-23T03:00:00Z',observed_at:'2026-09-22T00:00:00Z'},
  ], [{reason:'initial_capture',status:'ready',created_at:'2026-09-23T01:00:00Z'},
    {reason:'evidence_added',status:'running',created_at:'2026-09-23T04:00:00Z'}]);
  assert.ok(html.indexOf('>Check<') < html.indexOf('Evidence uploaded'));
  assert.ok(html.indexOf('Evidence uploaded') < html.indexOf('Reassessment requested'));
  assert.match(html, /datetime="2026-09-23T03:00:00Z"/);
  assert.match(html, /observed 2026-09-22T00:00:00Z/);
  assert.doesNotMatch(html, /datetime="2026-09-22T00:00:00Z"|initial_capture/);
  assert.match(html, /data-media-ref="one"/);
});

test('unknown upload times stay unknown and uploads alone never imply reassessment', () => {
  const html = helpers.investigationTimeline({},[{attachment_id:'old',filename:'note.txt',kind:'text',status:'ready',observed_at:'2026-09-22'}]);
  assert.match(html,/Time unknown/);
  assert.doesNotMatch(html,/Reassessment requested|datetime=/);
  assert.match(helpers.investigationTimeline({},[{filename:'note',created_at:'2026-09-23',kind:'text'}]),/datetime="2026-09-23"/);
});

test('four report tabs retain separate captured and investigation source views', () => {
  const context = {reportTab:'evidence',evidenceView:'captured',sourceReturn:null,selectedEpisodeId:'episode',
    capsuleExport:()=>'',mediaEvidenceAvailability:()=>({text:true}),icon:()=>'',disclosure:()=>'',pct:()=>'',
    evidencePanel:()=>'<p>Captured records</p>',mediaEvidencePanel:()=>'<p>Operator uploads</p>',investigationEvidence:()=>'<p>Agent records</p>'};
  const render = vm.runInNewContext(escape + chunk('reportPanel','renderPatterns') + '\nreportPanel',context);
  const payload = {report:{incident:{},engineering_diagnostics:{},primary_hypothesis:{}}};
  const captured = render(payload);
  assert.equal((captured.match(/role="tab" /g) || []).length,4);
  assert.match(captured,/id="evidence-view-captured"[^>]*aria-pressed="true"/);
  assert.match(captured,/Captured records/);
  assert.doesNotMatch(captured,/Operator uploads|Agent records/);
  context.evidenceView = 'sources';
  const sources = render(payload);
  assert.match(sources,/Operator uploads.*Agent records/);
  assert.doesNotMatch(sources,/Captured records/);
});

test('investigation sources never inherit the selected alert capture label', () => {
  const render = vm.runInNewContext(escape + chunk('episodeContext','formatDate') + '\nepisodeContext', {
    reportTab:'evidence',evidenceView:'sources',reportId:()=> 'one',relativeTime:value=>value,icon:()=>'',
  });
  const html = render({episode_id:'episode',reference:'EP-1',signal_count:1,status:'resolved',signals:[{incident_id:'one',reference:'INC-1'}]});
  assert.match(html,/EP-1/);
  assert.doesNotMatch(html,/Selected alert capture|INC-1/);
});
