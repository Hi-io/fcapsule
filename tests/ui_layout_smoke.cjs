// Read-only browser smoke test against a running instance with retained incidents.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '..');
const base = process.env.FCAPSULE_URL || 'http://127.0.0.1:8765';
const output = process.env.FCAPSULE_SCREENSHOT_DIR || path.join(root, 'local_reports', 'ui-layout');
const widths = [1920, 1366, 1024, 720, 390, 320];

(async () => {
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true, ...(process.env.CHROME_PATH ? {executablePath:process.env.CHROME_PATH} : {})});
  const errors = [];
  const checks = [];
  try {
    const page = await browser.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/**', route => ['GET','HEAD'].includes(route.request().method()) ? route.continue() : route.abort('blockedbyclient'));
    if (process.env.LIVE_ASSETS !== '1') {
      await page.route('**/assets/app.css', route => route.fulfill({contentType:'text/css', body:['app.css','visual.css'].map(file=>fs.readFileSync(path.join(root,'fcapsule/ui/assets',file),'utf8')).join('\n')}));
      await page.route('**/assets/app.js', route => route.fulfill({contentType:'text/javascript', body:fs.readFileSync(path.join(root,'fcapsule/ui/assets/app.js'),'utf8')}));
      await page.route('**/assets/icons/image.svg', route => route.fulfill({contentType:'image/svg+xml',body:fs.readFileSync(path.join(root,'fcapsule/ui/assets/icons/image.svg'),'utf8')}));
    }
    const capture = async name => {
      const bounds = await page.evaluate(()=>({viewport:innerWidth,content:document.documentElement.scrollWidth}));
      assert.ok(bounds.content <= bounds.viewport + 1, name + ': horizontal page overflow');
      await page.screenshot({path:path.join(output, name + '.png'),animations:'disabled'});
      checks.push(name);
    };
    for (const width of widths) {
      await page.setViewportSize({width,height:width === 1920 ? 1080 : width < 720 ? 844 : 768});
      for (const view of ['console','patterns','targets','atlas','settings']) {
        await page.goto(base + '/' + view);
        await page.locator('h1').waitFor();
        await capture(view + '-' + width);
        if (view === 'console') {
          await page.locator('.episode-expander > summary').first().click();
          await page.locator('.briefing').waitFor();
          await page.locator('#incident-report').scrollIntoViewIfNeeded();
          const centered = await page.evaluate(()=>{
            const outer = document.querySelector('#incident-report').getBoundingClientRect();
            const inner = document.querySelector('.overview-layout').getBoundingClientRect();
            return Math.abs((outer.left+outer.right)/2 - (inner.left+inner.right)/2) < 2;
          });
          assert.ok(centered, 'Report reading area must be centered at ' + width);
          const layout = await page.evaluate(()=>{
            const main = document.querySelector('.report-main').getBoundingClientRect();
            const rail = document.querySelector('.report-rail').getBoundingClientRect();
            return {side:rail.left >= main.right, aligned:Math.abs(rail.top-main.top)<2, stacked:rail.top>=main.bottom};
          });
          assert.ok(width >= 1200 ? layout.side && layout.aligned : layout.stacked, 'Activity rail layout at ' + width);
          if (width >= 1200) {
            const aligned = await page.evaluate(()=>Math.abs(document.querySelector('.briefing h3').getBoundingClientRect().top - document.querySelector('.report-rail h3').getBoundingClientRect().top) < 3);
            assert.ok(aligned, 'Assessment and Activity headings must align at ' + width);
          }
          assert.ok(await page.locator('.overview-metrics .metric-preview').count() <= 1);
          await capture('overview-' + width);
          const basis = page.locator('#disclosure-assessment-basis');
          if (await basis.count()) {
            await basis.click();
            await capture('assessment-sources-' + width);
          }
          const check = page.locator('.compact-checks summary').first();
          if (await check.count()) {
            await check.click();
            await capture('activity-observation-' + width);
          }
          for (const tab of ['Investigation','Evidence','Timeline']) {
            await page.getByRole('tab',{name:tab,exact:true}).click();
            await page.locator('#incident-report').scrollIntoViewIfNeeded();
            await capture(tab.toLowerCase() + '-' + width);
            if (tab === 'Evidence') {
              assert.equal(await page.locator('#evidence-view-captured').getAttribute('aria-pressed'),'true');
              assert.equal(await page.locator('.investigation-sources').count(),0);
              assert.ok(await page.evaluate(()=>Math.abs(document.querySelector('.evidence-views').getBoundingClientRect().left - document.querySelector('.capture-context').getBoundingClientRect().left) < 2), 'Segments align with telemetry reading column');
              if (width >= 720) assert.ok(await page.locator('.evidence-views button').evaluateAll(buttons=>buttons.every(button=>button.offsetHeight < 44)), 'Desktop segment labels stay on one line');
              await page.locator('#evidence-view-sources').click();
              await capture('investigation-sources-' + width);
              assert.equal(await page.locator('.evidence-view').count(),0);
            }
          }
          await page.getByRole('tab',{name:'Overview',exact:true}).click();
          const basisDetails = page.locator('[data-disclosure="assessment-basis"]');
          if (await basisDetails.count() && !await basisDetails.evaluate(el=>el.open)) await basisDetails.locator('summary').click();
          const citation = page.locator('.briefing [data-investigation-ref]').first();
          if (await citation.count()) {
            const id = await citation.getAttribute('data-investigation-ref');
            await citation.click();
            assert.equal(await page.locator('#evidence-view-sources').getAttribute('aria-pressed'),'true');
            assert.ok(await page.locator('[id="disclosure-agent-' + id + '"]').isVisible(), 'Citation must open a retained source');
            assert.equal(await page.evaluate(()=>document.activeElement.id),'disclosure-agent-' + id);
            await page.locator('[data-return-source]').click();
            assert.equal(await page.getByRole('tab',{name:'Overview',exact:true}).getAttribute('aria-selected'),'true');
            assert.equal(await basisDetails.evaluate(el=>el.open),true, 'Source navigation must preserve rationale expansion');
            assert.equal(await page.evaluate(()=>document.activeElement.dataset.investigationRef),id);
          }
          const evidenceButton = page.locator('[data-add-evidence]');
          if (await evidenceButton.count()) {
            assert.equal(await evidenceButton.evaluate(el=>Boolean(el.closest('.evidence-prompt'))),true, 'Evidence prompt belongs beside the next action');
            await evidenceButton.click();
            await page.locator('dialog[open]').waitFor();
            assert.equal(await page.locator('#evidence-preview').isVisible(),false, 'No empty preview before a file is chosen');
            assert.ok(await page.locator('#evidence-note').evaluate(el=>el.getBoundingClientRect().height)<60, 'Composer starts at one row');
            await capture('evidence-dialog-' + width);
            await page.keyboard.press('Escape');
            assert.equal(await page.locator('dialog[open]').count(),0);
            assert.equal(await page.evaluate(()=>document.activeElement.id),'add-evidence-action');
          }
          await page.locator('.export-menu > summary').click();
          await capture('export-' + width);
          await page.keyboard.press('Escape');
        } else if (view === 'patterns') {
          const older = page.locator('.pattern-older > summary').first();
          if (await older.count()) {
            await older.click();
            assert.equal(await page.locator('.pattern-older[open]').count(),1);
          }
          await page.locator('[data-patterns-view="shared"]').click();
          const group = page.locator('.related-group > summary').first();
          if (await group.count()) await group.click();
          await capture('shared-' + width);
        } else if (view === 'targets') {
          await page.locator('#configure-targets').click();
          await page.locator('.connection-settings').scrollIntoViewIfNeeded();
          await capture('target-settings-' + width);
        } else if (view === 'atlas') {
          assert.ok(await page.getByRole('note').filter({hasText:'Similarity is not causation'}).count(), 'Atlas page must qualify similarity patterns');
          await page.getByLabel('Search patterns and cases').fill('timeout');
          await page.getByLabel('Search patterns and cases').press('Enter');
          await page.waitForFunction(()=>!document.querySelector('.atlas-status')?.textContent.includes('Loading Atlas records'));
          const hasPattern = await page.locator('.atlas-pattern-link').count();
          if (hasPattern) {
            await page.locator('.atlas-pattern-link').first().click();
            await page.locator('.atlas-detail-head').waitFor();
            const caseLink = page.locator('.atlas-case-list .atlas-case-link').first();
            if (await caseLink.count()) {
              await caseLink.click();
              await page.getByText('Retained case',{exact:true}).waitFor();
              await page.getByRole('heading',{name:'Provenance'}).waitFor();
            }
            await page.goto(base + '/atlas');
            await page.locator('h1').waitFor();
          } else {
            const caseLink = page.locator('.atlas-case-link').first();
            if (await caseLink.count()) {
              await caseLink.click();
              await page.getByText('Retained case',{exact:true}).waitFor();
              await page.getByRole('heading',{name:'Provenance'}).waitFor();
              await page.goto(base + '/atlas');
              await page.locator('h1').waitFor();
            }
          }
          await capture('atlas-search-' + width);
        } else {
          await page.locator('#evidence-models').scrollIntoViewIfNeeded();
          await capture('evidence-settings-' + width);
        }
      }
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output,'browser-checks.json'),JSON.stringify({base,liveAssets:process.env.LIVE_ASSETS === '1',checks,errors},null,2));
    console.log(JSON.stringify({screens:checks.length,widths,errors}));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
