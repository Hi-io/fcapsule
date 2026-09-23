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
    if (process.env.LIVE_ASSETS !== '1') {
      await page.route('**/assets/app.css', route => route.fulfill({contentType:'text/css', body:['app.css','visual.css'].map(file=>fs.readFileSync(path.join(root,'fcapsule/ui/assets',file),'utf8')).join('\n')}));
      await page.route('**/assets/app.js', route => route.fulfill({contentType:'text/javascript', body:fs.readFileSync(path.join(root,'fcapsule/ui/assets/app.js'),'utf8')}));
    }
    const capture = async name => {
      const bounds = await page.evaluate(()=>({viewport:innerWidth,content:document.documentElement.scrollWidth}));
      assert.ok(bounds.content <= bounds.viewport + 1, name + ': horizontal page overflow');
      await page.screenshot({path:path.join(output, name + '.png'),animations:'disabled'});
      checks.push(name);
    };
    for (const width of widths) {
      await page.setViewportSize({width,height:width === 1920 ? 1080 : width < 720 ? 844 : 768});
      for (const view of ['console','patterns','targets','settings']) {
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
          await capture('overview-' + width);
          for (const tab of ['Investigation','Evidence','Timeline']) {
            await page.getByRole('tab',{name:tab,exact:true}).click();
            await page.locator('#incident-report').scrollIntoViewIfNeeded();
            await capture(tab.toLowerCase() + '-' + width);
          }
          await page.getByRole('tab',{name:'Overview',exact:true}).click();
          const citation = page.locator('.briefing [data-investigation-ref]').first();
          if (await citation.count()) {
            await citation.click();
            await page.locator('[data-return-source]').click();
            assert.equal(await page.getByRole('tab',{name:'Overview',exact:true}).getAttribute('aria-selected'),'true');
          }
          const evidenceButton = page.locator('[data-add-evidence]');
          if (await evidenceButton.count()) {
            await evidenceButton.click();
            await page.locator('dialog[open]').waitFor();
            assert.equal(await page.locator('#evidence-preview').isVisible(),false, 'No empty preview before a file is chosen');
            await capture('evidence-dialog-' + width);
            await page.keyboard.press('Escape');
            assert.equal(await page.locator('dialog[open]').count(),0);
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
