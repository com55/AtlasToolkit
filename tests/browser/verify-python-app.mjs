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
