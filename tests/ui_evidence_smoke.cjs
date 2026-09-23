// Read-only live records + browser-local UI fixtures. No POST reaches the server.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const base = process.env.FCAPSULE_URL || 'https://192.168.0.102:30767';
const output = process.env.FCAPSULE_SCREENSHOT_DIR || path.join(root, 'local_reports', 'evidence-interactions');

(async () => {
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true,
    ...(process.env.CHROME_PATH ? {executablePath:process.env.CHROME_PATH} : {}),
    args:['--use-fake-device-for-media-stream'],
  });
  const checks = []; const errors = []; const blockedWrites = [];
  let retainedImageChecked = false;
  try {
    const context = await browser.newContext({permissions:['microphone']});
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    let fixture = null;
    await page.route('**/api/**', route => {
      if (!['GET','HEAD'].includes(route.request().method())) {
        blockedWrites.push(route.request().url()); return route.abort('blockedbyclient');
      }
      return route.continue();
    });
    await page.route('**/api/incidents/*/report', route => fixture
      ? route.fulfill({json:fixture}) : route.fallback());
    await page.route('**/assets/app.css', route => route.fulfill({contentType:'text/css',body:['app.css','visual.css'].map(file=>fs.readFileSync(path.join(root,'fcapsule/ui/assets',file),'utf8')).join('\n')}));
    await page.route('**/assets/app.js', route => route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.join(root,'fcapsule/ui/assets/app.js'),'utf8')}));
    await page.route('**/assets/icons/image.svg', route => route.fulfill({contentType:'image/svg+xml',body:fs.readFileSync(path.join(root,'fcapsule/ui/assets/icons/image.svg'),'utf8')}));
    const capture = async name => {
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth + 1), name + ': no page overflow');
      await page.screenshot({path:path.join(output,name + '.png'),animations:'disabled'});
      checks.push(name);
    };
    await page.setViewportSize({width:1366,height:900});
    await page.goto(base + '/console');
    await page.locator('.episode-expander > summary').first().click();
    await page.locator('.briefing').waitFor();
    const retained = await page.evaluate(()=>JSON.parse(JSON.stringify(selectedReport)));
    assert.ok(retained.report, 'Actual retained report loaded');
    await page.locator('#incident-report').scrollIntoViewIfNeeded();
    await capture('01-retained-overview');

    // Sources and metric navigation use the real retained report, not a fixture.
    await page.locator('#disclosure-assessment-basis').click();
    const citation = page.locator('.briefing [data-investigation-ref]').first();
    const citationId = await citation.getAttribute('data-investigation-ref');
    await citation.focus(); await page.keyboard.press('Enter');
    assert.equal(await page.locator('#evidence-view-sources').getAttribute('aria-pressed'),'true');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'disclosure-agent-' + citationId);
    await capture('02-retained-source');
    await page.locator('[data-return-source]').click();
    assert.equal(await page.evaluate(()=>document.activeElement.dataset.investigationRef),citationId);
    const metric = page.locator('.overview-metrics [data-evidence-link]').first();
    if (await metric.count()) {
      const id = await metric.getAttribute('data-evidence-link');
      await metric.click();
      assert.equal(await page.locator('#evidence-view-captured').getAttribute('aria-pressed'),'true');
      assert.equal(await page.evaluate(()=>document.activeElement.id),'evidence-' + id);
      await page.locator('[data-return-source]').click();
    }
    const originalUrl = page.url();
    const episodes = await page.evaluate(()=>lastState.overview.episodes.map(item=>({episode:item.episode_id,incident:item.primary_incident_id})));
    for (const episode of episodes.slice(0,20)) {
      const report = await page.evaluate(async incident => {
        const response = await fetch('/api/incidents/' + encodeURIComponent(incident) + '/report');
        return response.ok ? response.json() : null;
      },episode.incident);
      if (!report?.media_evidence?.some(item=>item.kind === 'image' && item.artifact_url)) continue;
      await page.goto(base + '/console?episode=' + encodeURIComponent(episode.episode));
      await page.locator('.briefing').waitFor();
      await page.getByRole('tab',{name:'Evidence',exact:true}).click();
      await page.locator('#evidence-view-sources').click();
      await page.locator('#disclosure-operator-evidence').click();
      const thumbnail = page.locator('.media-evidence-row .evidence-thumbnail').first();
      await thumbnail.click();
      await page.locator('.image-dialog img:not([hidden])').waitFor();
      assert.ok(await page.locator('.image-dialog img').evaluate(el=>el.naturalWidth>0));
      await capture('02b-real-retained-image');
      await page.keyboard.press('Escape');
      await page.locator('.image-dialog').waitFor({state:'detached'});
      assert.equal(await thumbnail.evaluate(el=>el === document.activeElement),true);
      retainedImageChecked = true;
      await page.goto(originalUrl);
      await page.locator('.briefing').waitFor();
      break;
    }
    const screenshot = await page.screenshot();
    const secure = await page.evaluate(()=>isSecureContext);
    assert.equal(secure,true,'This test requires trusted HTTPS or localhost; no bypasses');
    const originalCapabilities = await page.evaluate(()=>mediaEvidenceAvailability());
    // Specialist readiness is a browser fixture only; no model validation or calls.
    await page.evaluate(()=>{
      lastState.media = {core_investigator:{capability:{status:'ready'}},vision:{capability:{status:'ready'}},audio:{capability:{status:'ready'}}};
      renderConsole(lastState);
    });

    for (const width of [1366,390,320]) {
      await page.setViewportSize({width,height:width === 1366 ? 900 : 844});
      await page.locator('[data-add-evidence]').click();
      const composer = page.locator('.evidence-dialog:not(.image-dialog)');
      assert.equal(await composer.getAttribute('aria-labelledby'),'evidence-dialog-title');
      assert.equal(await page.evaluate(()=>document.activeElement.id),'evidence-note');
      assert.equal(await page.locator('#evidence-preview').isVisible(),false);
      assert.equal(await page.locator('.context-metadata').evaluate(el=>el.open),false);
      assert.ok(await page.locator('#evidence-note').evaluate(el=>el.offsetHeight)<60);
      const positions = await page.locator('.composer-row').evaluate(el=>{
        const image = el.querySelector('#attach-image').getBoundingClientRect();
        const note = el.querySelector('textarea').getBoundingClientRect();
        const mic = el.querySelector('#record-evidence').getBoundingClientRect();
        return image.right <= note.left && note.right <= mic.left;
      });
      assert.ok(positions,'Image left, mic right');
      await capture('03-composer-' + width);
      await page.locator('#evidence-note').fill('A retained observation.\nSecond line.\nThird line.\nFourth line.');
      assert.ok(await page.locator('#evidence-note').evaluate(el=>el.offsetHeight)>70);
      assert.equal(await page.locator('#submit-evidence').isEnabled(),true);
      await page.locator('#evidence-file').setInputFiles({name:'retained-screen.png',mimeType:'image/png',buffer:screenshot});
      const thumbnail = page.locator('#evidence-preview .evidence-thumbnail');
      assert.ok(await thumbnail.evaluate(el=>el.offsetWidth)<=110);
      await thumbnail.focus(); await page.keyboard.press('Enter');
      await page.locator('.image-dialog img:not([hidden])').waitFor();
      assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'Close image');
      await capture('04-image-dialog-' + width);
      assert.equal(context.pages().length,1,'Preview does not open another tab');
      await page.keyboard.press('Tab');
      assert.ok(await page.evaluate(()=>document.activeElement.closest('.image-dialog') !== null));
      await page.keyboard.press('Escape');
      await page.locator('.image-dialog').waitFor({state:'detached'});
      assert.equal(await page.locator('.image-dialog').count(),0);
      assert.equal(await thumbnail.evaluate(el=>document.activeElement === el),true);
      await thumbnail.click();
      await page.getByRole('button',{name:'Close image',exact:true}).click();
      await page.locator('.image-dialog').waitFor({state:'detached'});
      assert.equal(await thumbnail.evaluate(el=>document.activeElement === el),true);
      await capture('05-thumbnail-' + width);
      await page.getByRole('button',{name:'Remove attachment',exact:true}).click();
      assert.equal(await page.locator('#evidence-preview').isVisible(),false);
      await page.keyboard.press('Escape');
      await composer.waitFor({state:'detached'});
      assert.equal(await page.evaluate(()=>document.activeElement.id),'add-evidence-action');
    }

    await page.setViewportSize({width:1366,height:900});
    await page.locator('[data-add-evidence]').click();
    await page.locator('#evidence-file').setInputFiles({name:'too-large.png',mimeType:'image/png',buffer:Buffer.alloc(6*1024*1024+1)});
    assert.match(await page.locator('#evidence-error').innerText(),/6 MiB/);
    await page.locator('#evidence-file').setInputFiles({name:'excerpt.txt',mimeType:'text/plain',buffer:Buffer.from('Short log excerpt')});
    await page.waitForFunction(()=>document.querySelector('#evidence-note').value === 'Short log excerpt');
    assert.equal(await page.locator('#evidence-note').inputValue(),'Short log excerpt');
    await page.locator('#evidence-note').evaluate(el=>{
      const transfer = new DataTransfer();
      transfer.items.add(new File([Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lAAAAABJRU5ErkJggg=='),ch=>ch.charCodeAt(0))], 'paste.png',{type:'image/png'}));
      el.dispatchEvent(new ClipboardEvent('paste',{clipboardData:transfer,bubbles:true,cancelable:true}));
    });
    await page.locator('#evidence-preview .evidence-thumbnail').waitFor();
    await page.getByRole('button',{name:'Remove attachment',exact:true}).click();
    await page.evaluate(()=>{
      window.testMicTracks = [];
      const getUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = async constraints => {
        const stream = await getUserMedia(constraints);
        window.testMicTracks.push(...stream.getTracks());
        return stream;
      };
    });
    await page.locator('#record-evidence').click();
    await page.getByRole('button',{name:'Stop recording',exact:true}).waitFor();
    assert.equal(await page.locator('#submit-evidence').isDisabled(),true);
    await capture('06-secure-recording');
    await page.waitForTimeout(1200);
    await page.getByRole('button',{name:'Stop recording',exact:true}).click();
    await page.locator('#evidence-preview audio').waitFor();
    assert.ok(await page.evaluate(()=>window.testMicTracks.every(track=>track.readyState === 'ended')),'Stopped recording releases microphone');
    assert.equal(await page.locator('#submit-evidence').isEnabled(),true);
    await capture('07-recorded-audio');
    await page.keyboard.press('Escape');
    await page.locator('.evidence-dialog').waitFor({state:'detached'});
    checks.push('trusted-https-composer-recording-no-upload');
    await page.locator('[data-add-evidence]').click();
    await page.locator('#record-evidence').click();
    await page.getByRole('button',{name:'Stop recording',exact:true}).waitFor();
    await page.keyboard.press('Escape');
    await page.locator('.evidence-dialog').waitFor({state:'detached'});
    assert.ok(await page.evaluate(()=>window.testMicTracks.every(track=>track.readyState === 'ended')),'Escape during recording releases microphone');
    await context.grantPermissions([], {origin:new URL(base).origin});
    await page.locator('[data-add-evidence]').click();
    await page.locator('#record-evidence').click();
    await page.waitForFunction(()=>document.querySelector('#record-status')?.textContent.includes('permission denied'));
    assert.equal(await page.locator('#record-evidence').getAttribute('aria-describedby'),'record-status');
    await capture('07b-microphone-permission-denied');
    await page.keyboard.press('Escape');
    await page.locator('.evidence-dialog').waitFor({state:'detached'});

    // Loading and unavailable image states stay in the dialog, with an exit.
    let releaseImage;
    await page.route('**/ui-test-unavailable-image', async route => {
      await new Promise(resolve=>{releaseImage=resolve;});
      await route.fulfill({status:404,body:'Not retained'});
    });
    await page.evaluate(()=>showEvidenceImage('/ui-test-unavailable-image','Unavailable image',document.querySelector('[data-add-evidence]')));
    await page.locator('.image-dialog [role="status"]').waitFor();
    assert.match(await page.locator('.image-dialog [role="status"]').innerText(),/Loading image/);
    await capture('08-image-loading');
    await page.waitForFunction(()=>document.querySelector('.image-dialog img')?.src.endsWith('/ui-test-unavailable-image'));
    while (!releaseImage) await page.waitForTimeout(10);
    releaseImage();
    await page.getByText('Image unavailable. The retained file may no longer be accessible.',{exact:true}).waitFor();
    await capture('09-image-unavailable');
    await page.keyboard.press('Escape');
    await page.locator('.image-dialog').waitFor({state:'detached'});

    fixture = structuredClone(retained);
    fixture.media_evidence = [{attachment_id:'ui-only',kind:'image',filename:'Browser-only screenshot.png',status:'ready',uploaded_at:'2026-09-23T03:00:00Z',observed_at:'2026-09-22T01:00:00Z',artifact_url:'/ui-test-retained-image',extraction:{observations:[{fact:'Browser-only preview test, never uploaded.'}]}}];
    fixture.investigation_revisions = [{reason:'evidence_added',created_at:'2026-09-23T03:01:00Z',status:'ready'}];
    await page.route('**/ui-test-retained-image',route=>route.fulfill({contentType:'image/png',body:screenshot}));
    await page.evaluate(payload=>{selectedReport=payload;reportTab='overview';renderConsole(lastState);},fixture);
    await page.locator('.evidence-prompt [data-media-ref]').click();
    assert.equal(await page.locator('#evidence-view-sources').getAttribute('aria-pressed'),'true');
    await page.locator('.media-evidence-row .evidence-thumbnail').click();
    await page.locator('.image-dialog img:not([hidden])').waitFor();
    await page.evaluate(()=>renderConsole(lastState));
    await page.keyboard.press('Escape');
    await page.locator('.image-dialog').waitFor({state:'detached'});
    assert.equal(await page.locator('.media-evidence-row .evidence-thumbnail').evaluate(el=>el === document.activeElement),true);
    await capture('10-additional-evidence');
    await page.locator('[data-return-source]').click();
    assert.equal(await page.evaluate(()=>document.activeElement.hasAttribute('data-media-ref')),true);
    await page.getByRole('tab',{name:'Timeline',exact:true}).click();
    const event = page.locator('.event-timeline li').filter({hasText:'Evidence uploaded: Browser-only screenshot.png'});
    assert.equal(await event.locator('time').getAttribute('datetime'),'2026-09-23T03:00:00Z');
    await event.locator('[data-media-ref]').click();
    assert.equal(await page.evaluate(()=>document.activeElement.id),'attachment-ui-only');
    await page.locator('[data-return-source]').click();
    assert.equal(await page.getByRole('tab',{name:'Timeline',exact:true}).getAttribute('aria-selected'),'true');
    await capture('11-evidence-timeline');

    assert.deepEqual(blockedWrites,[],'No write or paid call was attempted');
    assert.deepEqual(errors,[]);
    const result = {base,checks,errors,blockedWrites,originalCapabilities,secure,retainedImageChecked,syntheticMicrophone:true,clientOnlyFixtures:true};
    fs.writeFileSync(path.join(output,'browser-checks.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
