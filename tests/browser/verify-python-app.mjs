/** Real-browser verification of the locally built Python engine. */
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SITE = path.resolve(HERE, '../../dist/web');

async function loadChromium() {
  const candidates = [
    process.env.PLAYWRIGHT_CORE,
    'playwright-core',
    'playwright',
    path.join(os.homedir(), '.npm-global/lib/node_modules/@playwright/mcp/node_modules/playwright-core/index.js'),
    path.join(os.homedir(), '.npm-global/lib/node_modules/playwright-core/index.js'),
    '/usr/lib/node_modules/playwright-core/index.js',
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      const mod = await import(candidate);
      const pw = mod.chromium ? mod : mod.default;
      if (pw && pw.chromium) return pw.chromium;
    } catch { /* try next */ }
  }
  return null;
}

if (!fs.existsSync(path.join(SITE, 'pyodide/pyodide.asm.wasm'))) {
  console.log('SKIP: dist/web/pyodide/pyodide.asm.wasm is missing; build the web site first.');
  process.exit(0);
}
const chromium = await loadChromium();
if (!chromium) {
  console.log('SKIP: playwright-core not found (set $PLAYWRIGHT_CORE to its index.js to run).');
  process.exit(0);
}

const HARNESS = `<!doctype html><meta charset="utf-8"><script type="module">
import { startEngine, callHost, stopEngine, HostError } from '/js/engine-client.js';
window.engine = { startEngine, callHost, stopEngine, HostError };
</script>`;
const mime = {
  '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.css': 'text/css', '.png': 'image/png',
  '.ico': 'image/x-icon', '.svg': 'image/svg+xml',
  '.wasm': 'application/wasm', '.json': 'application/json',
  '.zip': 'application/zip', '.whl': 'application/octet-stream',
};
const server = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://localhost').pathname;
  if (pathname === '/__test.html') {
    res.setHeader('content-type', 'text/html');
    res.end(HARNESS);
    return;
  }
  let filename;
  try {
    filename = path.resolve(SITE, '.' + decodeURIComponent(pathname));
  } catch {
    res.writeHead(400).end();
    return;
  }
  if (!filename.startsWith(SITE + path.sep) || !fs.existsSync(filename) || !fs.statSync(filename).isFile()) {
    res.writeHead(404).end();
    return;
  }
  res.setHeader('content-type', mime[path.extname(filename)] || 'application/octet-stream');
  fs.createReadStream(filename).pipe(res);
});

let browser;
const external = [];
async function checkWithin(label, check) {
  let timer;
  try {
    return await Promise.race([
      check(),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`${label} timed out after 30 s`)), 30000);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

try {
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ serviceWorkers: 'block' });
  const page = await context.newPage();
  const localOnly = (route) => {
    if (new URL(route.request().url()).host !== new URL(origin).host) {
      external.push(route.request().url());
      return route.abort();
    }
    return route.continue();
  };
  // Context routing also covers fetches initiated inside the engine Worker.
  await context.route('**/*', localOnly);
  await page.route('**/*', localOnly);
  await page.goto(`${origin}/__test.html`);
  await page.waitForFunction(() => !!window.engine);
  page.setDefaultTimeout(120000);
  await page.evaluate(async () => {
    await Promise.race([
      window.engine.startEngine(() => {}),
      new Promise((_, reject) => setTimeout(() => reject(new Error('Engine start timed out')), 120000)),
    ]);
  });
  console.log('PASS startEngine resolves');

  await checkWithin('required_pages', () => page.evaluate(async () => {
    window.atlasText = 'p.png\nsize: 9,7\npma: false\na\n bounds: 1,2,3,2\n';
    const pages = await window.engine.callHost('required_pages', [window.atlasText]);
    if (JSON.stringify(pages) !== '["p.png"]') throw new Error('Unexpected required_pages result');
  }));
  console.log('PASS required_pages returns the page list');

  await checkWithin('load and preview raster', () => page.evaluate(async () => {
    const canvas = new OffscreenCanvas(9, 7);
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = 'rgb(10,20,30)';
    ctx.fillRect(0, 0, 9, 7);
    ctx.fillStyle = 'rgb(61,123,207)';
    ctx.fillRect(3, 3, 1, 1);
    const png = new Uint8Array(await (await canvas.convertToBlob({ type: 'image/png' })).arrayBuffer());
    await window.engine.callHost('load', ['synthetic.atlas', window.atlasText, { 'p.png': png }, null, null]);
    const preview = await window.engine.callHost('preview', [['a'], false, false, false, 'straight']);
    if (!(preview.rgba instanceof Uint8Array) || preview.width !== 3 || preview.height !== 2
      || preview.rgba.length !== preview.width * preview.height * 4) {
      throw new Error('Invalid preview raster');
    }
    // Page (3,3) is preview (2,1), with a 3-pixel row stride.
    const pixel = [...preview.rgba.slice(20, 24)];
    if (JSON.stringify(pixel) !== '[61,123,207,255]') throw new Error('Known pixel mismatch: ' + pixel);
  }));
  console.log('PASS preview decodes RGBA bytes with the known pixel at its expected offset');

  await checkWithin('HostError', () => page.evaluate(async () => {
    try {
      await window.engine.callHost('preview', [['missing'], false, false, false, 'straight']);
    } catch (error) {
      if (error instanceof window.engine.HostError && error.type === 'RegionNotFound') return;
      throw error;
    }
    throw new Error('Unknown region preview should reject');
  }));
  console.log('PASS Python RegionNotFound becomes a HostError');
  await checkWithin('stopEngine', () => page.evaluate(() => window.engine.stopEngine()));

  // Exercise the shipped page as well as the transport harness above.
  const app = await context.newPage();
  const pageErrors = [];
  app.on('pageerror', error => pageErrors.push(error.message));
  let releaseWorker;
  const workerGate = new Promise(resolve => { releaseWorker = resolve; });
  await app.route('**/workers/engine-host-worker.js', async route => {
    await workerGate;
    await route.continue();
  });
  await app.goto(origin + '/index.html', {waitUntil: 'domcontentloaded'});
  if (!await app.locator('#engine-loading').isVisible()) throw new Error('Engine loading overlay missing before ready');
  const loadingText = await app.locator('#engine-loading').textContent();
  if (!loadingText.includes('Starting AtlasToolkit') || !loadingText.includes('0%')) throw new Error('Engine loading overlay missing startup text or initial percentage');
  releaseWorker();
  await app.locator('#engine-loading').waitFor({state: 'hidden', timeout: 120000});
  // Startup restores prefs after the engine promise; wait for that too.
  await app.waitForFunction(() => document.body.classList.contains('mode-extract'));
  await app.evaluate(async () => {
    const {AtlasAPI} = await import('/js/atlas-api.js');
    await AtlasAPI.set_forced_resizing(false);
    window.testAPI = AtlasAPI;
    window.testAtlas = 'p.png\nsize: 12,12\npma: false\na\n bounds: 0,0,10,2\n\nq.png\nsize: 12,12\npma: false\nb\n bounds: 0,0,4,6\n';
    const makePage = async (name, color) => {
      const canvas = document.createElement('canvas');
      canvas.width = canvas.height = 12;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = color; ctx.fillRect(0,0,12,12);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
      return new File([blob], name, {type:'image/png'});
    };
    window.testPages = [await makePage('p.png', '#d02030'), await makePage('q.png', '#2060d0')];
    const ok = await AtlasAPI.load_from_files([new File([window.testAtlas], 'synthetic.atlas'), ...window.testPages]);
    if (!ok || AtlasAPI.get_region_names().length !== 2 || !AtlasAPI.is_multi_page()) throw new Error('Two-page app load failed');
    const {loadRegions} = await import('/js/region-list.js');
    const {updateMeshCroppingUI} = await import('/js/modify-mode.js');
    await loadRegions(); updateMeshCroppingUI();
  });
  if (await app.locator('.region-item').count() !== 2) throw new Error('Region list count mismatch');
  await app.locator('.region-item').nth(0).click();
  await app.waitForFunction(() => {
    const img = document.getElementById('preview-img');
    return img.complete && img.naturalWidth === 10 && img.naturalHeight === 2;
  });
  const nonBlank = await app.evaluate(() => {
    const img = document.getElementById('preview-img');
    const canvas = document.createElement('canvas'); canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
    const ctx = canvas.getContext('2d'); ctx.drawImage(img,0,0);
    return [...ctx.getImageData(0,0,10,2).data].some((v,i) => i % 4 === 3 && v > 0);
  });
  if (!nonBlank) throw new Error('App preview is blank');
  // Drive the same selection state used by pointer selection, then render.
  await app.evaluate(async () => {
    const {state, getSelectedRegions} = await import('/js/state.js');
    state.selectedIndices = new Set([0,1]);
    const {updatePreview} = await import('/js/preview.js');
    await updatePreview(getSelectedRegions());
  });
  await app.waitForFunction(() => {
    const img = document.getElementById('preview-img');
    return img.complete && img.naturalWidth === 10 && img.naturalHeight === 6;
  });
  await app.evaluate(async () => {
    await window.testAPI.set_forced_resizing(true);
    const {getSelectedRegions} = await import('/js/state.js');
    const {updatePreview} = await import('/js/preview.js');
    await updatePreview(getSelectedRegions());
  });
  await app.waitForFunction(() => {
    const img = document.getElementById('preview-img');
    return img.complete && img.naturalWidth === 4 && img.naturalHeight === 6;
  });
  for (const id of ['chk-mesh-hull']) {
    if (!await app.locator('#' + id).isDisabled()) throw new Error(id + ' should be disabled without skeleton');
  }
  console.log('PASS real app loading overlay, two-page region list, nonblank preview, composite, Forced Resizing, and mesh toggles');

  // A tab uses download routing. Disable optional native save pickers so the
  // automation never opens an OS dialog on browsers that expose these APIs.
  await app.evaluate(() => { window.showSaveFilePicker = undefined; window.showDirectoryPicker = undefined; });
  const downloaded = app.waitForEvent('download');
  await app.locator('#btn-extract-all').click();
  await app.locator('#modal-overlay').waitFor({state:'visible'});
  await app.locator('#btn-modal-confirm').click();
  const download = await downloaded;
  if (!download.suggestedFilename().endsWith('.zip')) throw new Error('Extract All did not download a zip');
  const zip = fs.readFileSync(await download.path());
  const entries = [];
  let offset = 0;
  while (offset + 30 <= zip.length && zip.readUInt32LE(offset) === 0x04034b50) {
    const size = zip.readUInt32LE(offset + 18);
    const nameLength = zip.readUInt16LE(offset + 26);
    const extraLength = zip.readUInt16LE(offset + 28);
    const name = zip.toString('utf8', offset + 30, offset + 30 + nameLength);
    const start = offset + 30 + nameLength + extraLength;
    if (!zip.subarray(start, start + 8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) throw new Error('ZIP entry is not PNG');
    entries.push(name);
    offset = start + size;
  }
  if (JSON.stringify(entries.sort()) !== '["a.png","b.png"]') throw new Error('Unexpected extracted entries: ' + entries);
  console.log('PASS real app Extract All downloads one PNG per region in a zip');

  await app.evaluate(() => {
    window.pendingMissing = window.testAPI.load_from_files([
      new File([window.testAtlas], 'missing.atlas'), window.testPages[0],
    ]);
  });
  await app.locator('.missing-images-overlay').waitFor({state:'visible'});
  if (!(await app.locator('.missing-images-overlay').innerText()).includes('q.png')) throw new Error('Missing-page dialog omits page');
  await app.locator('.missing-images-overlay').getByRole('button', {name:'Cancel', exact:true}).click();
  if (await app.evaluate(() => window.pendingMissing) !== 'cancelled') throw new Error('Missing-page cancellation failed');
  console.log('PASS real app missing-page dialog');

  await app.evaluate(() => {
    const duplicate = window.testAtlas.replace('\nb\n', '\na\n');
    window.pendingDuplicate = window.testAPI.load_from_files([
      new File([duplicate], 'duplicate.atlas'), ...window.testPages,
    ]);
  });
  await app.locator('#modal-overlay').waitFor({state:'visible'});
  const message = await app.locator('#modal-message').innerText();
  if (!message.includes('atlas has duplicate region names: a')) throw new Error('DuplicateRegionName message missing: ' + message);
  if (await app.locator('#modal-title').innerText() !== 'Cannot open atlas') throw new Error('Wrong error title');
  await app.locator('#btn-modal-confirm').click();
  if (await app.evaluate(() => window.pendingDuplicate) !== false) throw new Error('Duplicate load should return false');
  if (pageErrors.length) throw new Error('App page errors: ' + pageErrors.join('; '));
  console.log('PASS real app DuplicateRegionName alert');
  await app.evaluate(async () => (await import('/js/engine-client.js')).stopEngine());

  if (external.length) throw new Error(`Non-local requests recorded: ${external.length}`);
  console.log('PASS no request leaves the local server');
} catch (error) {
  console.error('FAIL', error.message);
  process.exitCode = 1;
} finally {
  if (external.length) {
    console.error(`FAIL ${external.length} non-local request(s) recorded`);
    process.exitCode = 1;
  }
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
