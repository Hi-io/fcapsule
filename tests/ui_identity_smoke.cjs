// Local feature-server smoke test; unlike the live layout check, saving is safe here.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const base = process.env.FCAPSULE_URL || 'http://127.0.0.1:8766';
const output = process.env.FCAPSULE_SCREENSHOT_DIR || path.join(__dirname, '..', 'local_reports', 'identity-smoke');

(async () => {
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true});
  try {
    for (const [index, width] of [1366, 390].entries()) {
      const page = await browser.newPage({viewport:{width,height:844}});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base + '/targets');
      await page.locator('#configure-targets').click();
      if (!index && await page.locator('.identity-label-row').count() === 3) {
        await page.locator('.identity-label-row').last().locator('[data-remove-identity]').click();
      }
      assert.equal(await page.locator('.identity-label-row').count(), index ? 3 : 2);
      assert.deepEqual((await page.locator('[data-id-name]').evaluateAll(items => items.map(item => item.value))).slice(0,2), ['CNFC','VNFC']);
      if (!index) {
        await page.locator('#add-identity-label').focus();
        await page.keyboard.press('Enter');
        assert.equal(await page.locator('.identity-label-row').count(), 3);
        await page.locator('.identity-label-row').last().locator('[data-id-name]').fill('Site');
        await page.locator('.identity-label-row').last().locator('[data-id-alert]').fill('site_id');
        await page.locator('.identity-label-row').last().locator('[data-id-pod]').fill('site.example.com/id');
        await page.locator('#save-targets').click();
        await page.locator('#target-notice').getByText('Target settings saved.').waitFor();
      }
      assert.equal(await page.locator('.identity-label-row').count(), 3);
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({path:path.join(output, 'targets-' + width + '.png'),fullPage:true});
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('Identity settings: desktop/mobile layout, keyboard add, and persistence passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
