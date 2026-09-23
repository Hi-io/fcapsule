// Explicit live smoke test. Uses a synthetic microphone, never records the user.
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

(async () => {
  const base = process.env.FCAPSULE_URL;
  assert.ok(base, 'Set FCAPSULE_URL to a trusted HTTPS or localhost instance');
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.CHROME_PATH ? {executablePath:process.env.CHROME_PATH} : {}),
    args: ['--use-fake-device-for-media-stream'],
  });
  try {
    const context = await browser.newContext({permissions:['microphone']});
    const page = await context.newPage();
    const response = await page.goto(base + '/console');
    assert.equal(response.status(), 200);
    const result = await page.evaluate(async () => {
      if (!isSecureContext || !navigator.mediaDevices) return {secure:false};
      const stream = await navigator.mediaDevices.getUserMedia({audio:true});
      const audioTracks = stream.getAudioTracks().length;
      const recorder = new MediaRecorder(stream);
      const chunks = [];
      recorder.addEventListener('dataavailable', event => chunks.push(event.data));
      const finished = new Promise(resolve => recorder.addEventListener('stop', resolve, {once:true}));
      recorder.start();
      await new Promise(resolve => setTimeout(resolve, 350));
      recorder.stop();
      await finished;
      stream.getTracks().forEach(track => track.stop());
      return {secure:true, audioTracks, recordingBytes:new Blob(chunks).size};
    });
    assert.equal(result.secure, true);
    assert.equal(result.audioTracks, 1);
    assert.ok(result.recordingBytes > 0);
    console.log(JSON.stringify({url:base, syntheticMicrophone:true, ...result}));
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
