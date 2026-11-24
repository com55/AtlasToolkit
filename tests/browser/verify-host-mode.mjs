// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
/** Host mode in a real browser: a page on another origin frames the built web app. */
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SITE = path.resolve(HERE, '../../dist/web');
const SKEL = path.join(HERE, '../fixtures/synthetic-skeleton-42.skel');

async function loadChromium() {
  const candidates = [
    process.env.PLAYWRIGHT_CORE, 'playwright-core', 'playwright',
    path.join(os.homedir(), '.npm-global/lib/node_modules/@playwright/mcp/node_modules/playwright-core/index.js'),
    path.join(os.homedir(), '.npm-global/lib/node_modules/playwright-core/index.js'),
    '/usr/lib/node_modules/playwright-core/index.js',
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      const mod = await import(candidate);
      return mod.chromium || mod.default?.chromium;
    } catch (_) { /* next */ }
  }
  return null;
}

if (!fs.existsSync(path.join(SITE, 'index.html'))) {
  console.log('SKIP: dist/web is not built (uv run python scripts/build_web.py).');
  process.exit(0);
}
const chromium = await loadChromium();
if (!chromium) {
  console.log('SKIP: playwright-core not found (set $PLAYWRIGHT_CORE to its index.js to run).');
  process.exit(0);
}

async function waitInFrame(frame, predicate, timeout = 120000, arg = null) {
  await frame.evaluate(async ({source, timeout, arg}) => {
    const check = (0, eval)('(' + source + ')');
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (await check(arg)) return;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error('Condition did not become true within ' + timeout + ' ms');
  }, {source: predicate.toString(), timeout, arg});
}

const mime = {
  '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css',
  '.png': 'image/png', '.ico': 'image/x-icon', '.svg': 'image/svg+xml', '.wasm': 'application/wasm',
  '.json': 'application/json', '.zip': 'application/zip', '.whl': 'application/octet-stream',
};
const appServer = http.createServer((req, res) => {
  const pathname = new URL(req.url, 'http://localhost').pathname;
  let filename;
  try { filename = path.resolve(SITE, '.' + decodeURIComponent(pathname === '/' ? '/index.html' : pathname)); }
  catch { res.writeHead(400).end(); return; }
  if (!filename.startsWith(SITE + path.sep) || !fs.existsSync(filename) || !fs.statSync(filename).isFile()) {
    res.writeHead(404).end(); return;
  }
  res.setHeader('content-type', mime[path.extname(filename)] || 'application/octet-stream');
  fs.createReadStream(filename).pipe(res);
});

// The host page: frames the app and records every message it gets.
const HOST_PAGE = `<!doctype html><meta charset="utf-8"><body><script>
window.__msgs = [];
const params = new URLSearchParams(location.search);
window.appOrigin = params.get('app');
const frame = document.createElement('iframe');
frame.id = 'atk'; frame.style.width = '1200px'; frame.style.height = '800px';
addEventListener('message', e => { if (e.origin === window.appOrigin) window.__msgs.push(e.data); });
frame.src = window.appOrigin + '/index.html?test-hooks' + (params.get('nohost') ? '' : '&host=' + encodeURIComponent(location.origin));
document.body.appendChild(frame);
window.send = message => frame.contentWindow.postMessage({atlasToolkitHost: 1, ...message}, window.appOrigin);
window.got = type => window.__msgs.filter(m => m.type === type);
</script>`;
// A page on a third origin, framed next to the app, that tries to drive it.
const ROGUE_PAGE = `<!doctype html><meta charset="utf-8"><script>
const text = 'x.png\\nsize: 4,4\\nrogue\\n bounds: 0,0,2,2\\n';
parent.frames[0].postMessage({atlasToolkitHost: 1, type: 'open', files: [new File([text], 'rogue.atlas')]}, '*');
</script>`;
const pageServer = page => http.createServer((req, res) => {
  res.setHeader('content-type', 'text/html');
  res.end(page);
});
const hostServer = pageServer(HOST_PAGE);
const rogueServer = pageServer(ROGUE_PAGE);
const listen = server => new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(0, '127.0.0.1', () => resolve(server.address().port));
});

let browser, failed = false;
const pass = label => console.log('PASS ' + label);
try {
  const appOrigin = `http://127.0.0.1:${await listen(appServer)}`;
  const hostOrigin = `http://localhost:${await listen(hostServer)}`;
  browser = await chromium.launch({headless: true});
  const context = await browser.newContext({serviceWorkers: 'block'});
  await context.addInitScript(() => {
    window.__appModule = name => window.__atlasModules
      ? Promise.resolve(window.__atlasModules[name])
      : import('/js/' + name + '.js');
  });
  const page = await context.newPage();
  page.setDefaultTimeout(120000);
  await page.goto(`${hostOrigin}/?app=${encodeURIComponent(appOrigin)}`);
  const frameOf = () => page.frames().find(f => f.url().startsWith(appOrigin));
  await page.waitForFunction(() => window.got('ready').length === 1, null, {polling: 500, timeout: 120000});
  const frame = frameOf();
  const ready = (await page.evaluate(() => window.got('ready')[0]));
  if (ready.protocol !== 1 || typeof ready.appVersion !== 'string') throw new Error('ready payload: ' + JSON.stringify(ready));
  if (await frame.locator('.btn-open').isVisible()) throw new Error('Open visible in host mode');
  pass('1a ready arrives with protocol 1; Open is hidden');

  // A frame on a third origin posts "open" to the app: ignored.
  const rogueOrigin = `http://localhost:${await listen(rogueServer)}`;
  await page.evaluate(src => {
    const rogue = document.createElement('iframe'); rogue.src = src; document.body.appendChild(rogue);
  }, `${rogueOrigin}/rogue.html`);
  await page.waitForTimeout(3000);
  if (await page.evaluate(() => window.__msgs.some(m => m.type === 'opened' || m.type === 'open-failed'))) throw new Error('Rogue open was answered');
  if (await frame.locator('.region-item').count() !== 0) throw new Error('Rogue open loaded an atlas');
  pass('2 an open from another origin and window is ignored');

  // The host opens an atlas, its page and a .skel with another name, right away.
  const skelBytes = [...fs.readFileSync(SKEL)];
  await page.evaluate(async skelBytes => {
    const c = new OffscreenCanvas(32, 32); const ctx = c.getContext('2d');
    ctx.fillStyle = '#40a060'; ctx.fillRect(0, 0, 32, 32);
    const png = await c.convertToBlob({type: 'image/png'});
    window.editPNG = [...new Uint8Array(await png.arrayBuffer())];
    const text = 'p.png\nsize: 32,32\nmesh_a\n bounds: 0,0,8,8\n';
    window.send({type: 'open', hostName: 'Example Host', saveLabel: 'Apply to Example Host', files: [
      new File([text], 'hero.atlas'), new File([png], 'p.png', {type: 'image/png'}),
      new File([new Uint8Array(skelBytes)], 'other.skel'),
    ]});
  }, skelBytes);
  // The engine is still starting: the frame shows the busy overlay, not an idle app.
  await waitInFrame(frame, () => !document.getElementById('busy-overlay').classList.contains('hidden')
    && document.getElementById('busy-label').textContent === 'Preparing AtlasToolkit engine...'
    && !document.getElementById('busy-progress').classList.contains('hidden')
    && /^\d+%$/.test(document.getElementById('busy-progress').textContent.trim()), 30000);
  await page.waitForFunction(() => window.got('opened').length === 1 || window.got('open-failed').length > 0, null, {polling: 500, timeout: 120000});
  const opened = await page.evaluate(() => window.got('opened')[0]);
  if (!opened || opened.atlas !== 'hero.atlas') throw new Error('open failed: ' + JSON.stringify(await page.evaluate(() => window.__msgs)));
  await frame.locator('.region-item[data-label="mesh_a"]').waitFor();
  if ((await frame.locator('#host-badge').textContent()).trim() !== 'From Example Host') throw new Error('badge text');
  if (!(await frame.locator('#btn-save-mod').textContent()).includes('Apply to Example Host')) throw new Error('Save label');
  if (!await frame.locator('#chk-mesh-hull').isEnabled()) throw new Error('the .skel with another name was not used');
  // In a frame the host page's viewport applies: the preview must keep pinch and pan for itself.
  if (await frame.locator('#preview-container').evaluate(el => getComputedStyle(el).touchAction) !== 'none') throw new Error('preview touch-action is not none');
  // A real two-finger gesture in the frame: moving both fingers pans, then spreading zooms.
  const cdp = await context.newCDPSession(page);
  await cdp.send('Emulation.setTouchEmulationEnabled', {enabled: true, maxTouchPoints: 5});
  const box = await page.locator('#atk').boundingBox();
  const inner = await frame.locator('#preview-container').evaluate(el => { const r = el.getBoundingClientRect(); return {x: r.left + r.width / 2, y: r.top + r.height / 2}; });
  const cx = box.x + inner.x, cy = box.y + inner.y;
  const view = () => frame.evaluate(async () => { const {state} = await window.__appModule('state'); return {...state.viewState}; });
  const touch = (type, points) => cdp.send('Input.dispatchTouchEvent', {type, touchPoints: points.map(([x, y], id) => ({x, y, id}))});
  const before = await view();
  await touch('touchStart', [[cx - 50, cy], [cx + 50, cy]]);
  for (let step = 1; step <= 4; step++) await touch('touchMove', [[cx - 50 + 10 * step, cy + 5 * step], [cx + 50 + 10 * step, cy + 5 * step]]);
  const panned = await view();
  // The browser may hold back the first move (touch slop), so expect most of the 40/20 px.
  const dx = panned.x - before.x, dy = panned.y - before.y;
  if (dx < 25 || dx > 41 || Math.abs(dy - dx / 2) > 1 || Math.abs(panned.scale - before.scale) > 1e-6) throw new Error('two-finger pan: ' + JSON.stringify({before, panned}));
  for (let step = 1; step <= 4; step++) await touch('touchMove', [[cx - 10 - 25 * step, cy + 20], [cx + 90 + 25 * step, cy + 20]]);
  const zoomed = await view();
  if (!(zoomed.scale > panned.scale * 1.4)) throw new Error('two-finger spread did not zoom: ' + JSON.stringify({panned, zoomed}));
  await touch('touchEnd', []);
  pass('1c two fingers pan and zoom the preview inside the frame');
  pass('1b open before engine ready waits under the busy overlay, loads atlas, page and differently named .skel; badge and Save label');

  // Edit, then Save: the host refuses once, then accepts.
  await frame.locator('#mode-modify').click();
  await waitInFrame(frame, async () => document.body.classList.contains('mode-modify') && !(await window.__appModule('busy-overlay')).isBusy());
  const editBytes = await page.evaluate(() => window.editPNG);
  await frame.evaluate(async bytes => {
    const {AtlasAPI} = await window.__appModule('atlas-api');
    const {onModPreviewReceived} = await window.__appModule('modify-mode');
    await onModPreviewReceived(await AtlasAPI.process_mod_image(new File([new Uint8Array(bytes)], 'edit.png'), ['mesh_a']));
  }, editBytes);
  await waitInFrame(frame, async () => (await window.__appModule('atlas-api')).AtlasAPI.has_pending_modifications());
  await frame.locator('#btn-save-mod').click();
  await page.waitForFunction(() => window.got('save').length === 1, null, {polling: 500});
  const save = await page.evaluate(async () => {
    const message = window.got('save')[0];
    return {id: message.id, names: message.files.map(f => f.name), blobs: message.files.every(f => f.blob instanceof Blob)};
  });
  if (JSON.stringify(save.names) !== JSON.stringify(['p.png', 'hero.atlas', 'other.skel']) || !save.blobs) throw new Error('save files: ' + JSON.stringify(save));
  await page.evaluate(id => window.send({type: 'progress', text: 'Writing 1 / 2'}), save.id);
  await waitInFrame(frame, () => document.getElementById('busy-label').textContent === 'Writing 1 / 2');
  await page.evaluate(id => window.send({type: 'saved', id, ok: false, message: 'nope'}), save.id);
  // Error toasts are modal alerts in this app.
  await frame.locator('#modal-overlay:not(.hidden)').waitFor();
  if (!(await frame.locator('#modal-message').textContent()).includes('nope')) throw new Error('refusal message not shown');
  await frame.locator('#btn-modal-confirm').click();
  await frame.locator('#modal-overlay.hidden').waitFor({state: 'attached'});
  if (!await frame.evaluate(async () => (await window.__appModule('atlas-api')).AtlasAPI.has_pending_modifications())) throw new Error('a refused save cleared the edits');
  pass('3a save carries page, atlas and .skel; progress shows; a refused save keeps the edits');

  // Close with pending edits asks; Keep editing answers close {ok: false}.
  await page.evaluate(() => window.send({type: 'close-request'}));
  await frame.locator('#modal-overlay:not(.hidden)').waitFor();
  if (!(await frame.locator('#modal-message').textContent()).includes('not been sent to Example Host')) throw new Error('close question text');
  if ((await frame.locator('#btn-modal-cancel').textContent()).trim() !== 'Keep editing') throw new Error('Keep editing label');
  await frame.locator('#btn-modal-cancel').click();
  await page.waitForFunction(() => window.got('close').length === 1, null, {polling: 500});
  if (await page.evaluate(() => window.got('close')[0].ok) !== false) throw new Error('close ok after Keep editing');
  pass('4 close-request with edits asks, Keep editing answers close {ok:false}');

  await waitInFrame(frame, async () => !(await window.__appModule('busy-overlay')).isBusy());
  await frame.locator('#btn-save-mod').click();
  await page.waitForFunction(() => window.got('save').length === 2, null, {polling: 500});
  await page.evaluate(() => window.send({type: 'saved', id: window.got('save')[1].id, ok: true, message: 'Applied.'}));
  await waitInFrame(frame, async () => !(await window.__appModule('atlas-api')).AtlasAPI.has_pending_modifications());
  await frame.locator('.toast.success', {hasText: 'Applied.'}).waitFor();
  pass('3b an accepted save clears the edits');

  // Save Image in View Mode goes to the host as a download.
  await frame.locator('#mode-extract').click();
  await waitInFrame(frame, () => document.body.classList.contains('mode-extract'));
  await frame.locator('.region-item[data-label="mesh_a"]').click();
  await waitInFrame(frame, async () => !document.getElementById('btn-save-merged').disabled && !(await window.__appModule('busy-overlay')).isBusy());
  await frame.locator('#btn-save-merged').click();
  await page.waitForFunction(() => window.got('download').length === 1, null, {polling: 500});
  const download = await page.evaluate(async () => {
    const m = window.got('download')[0];
    const head = [...new Uint8Array(await m.blob.slice(0, 4).arrayBuffer())];
    return {filename: m.filename, head};
  });
  if (!download.filename.endsWith('.png') || JSON.stringify(download.head) !== JSON.stringify([0x89, 0x50, 0x4e, 0x47])) throw new Error('download: ' + JSON.stringify(download));
  if (await frame.locator('[title]').count() !== 0) throw new Error('a [title] attribute in the frame');
  pass('5 Save Image goes to the host as a PNG download; no [title]');

  // The host keeps the frame: Discard on close drops the edits, and the next
  // open in the same frame loads at once, with no engine wait and no question.
  await frame.locator('#mode-modify').click();
  await waitInFrame(frame, async () => document.body.classList.contains('mode-modify') && !(await window.__appModule('busy-overlay')).isBusy());
  await frame.evaluate(async bytes => {
    const {AtlasAPI} = await window.__appModule('atlas-api');
    const {onModPreviewReceived} = await window.__appModule('modify-mode');
    await onModPreviewReceived(await AtlasAPI.process_mod_image(new File([new Uint8Array(bytes)], 'edit.png'), ['mesh_a']));
  }, editBytes);
  await waitInFrame(frame, async () => (await window.__appModule('atlas-api')).AtlasAPI.has_pending_modifications());
  await page.evaluate(() => window.send({type: 'close-request'}));
  await frame.locator('#modal-overlay:not(.hidden)').waitFor();
  await frame.locator('#btn-modal-confirm').click();
  await page.waitForFunction(() => window.got('close').length === 2, null, {polling: 500});
  if (await page.evaluate(() => window.got('close')[1].ok) !== true) throw new Error('close ok after Discard');
  await waitInFrame(frame, async () => !(await window.__appModule('atlas-api')).AtlasAPI.has_pending_modifications()
    && document.body.classList.contains('mode-extract'));
  await page.evaluate(async () => {
    const text = 'q.png\nsize: 32,32\nsecond\n bounds: 0,0,8,8\n';
    const png = new Blob([new Uint8Array(window.editPNG)], {type: 'image/png'});
    window.send({type: 'open', hostName: 'Example Host', files: [new File([text], 'second.atlas'), new File([png], 'q.png', {type: 'image/png'})]});
  });
  await page.waitForFunction(() => window.got('opened').length === 2 || window.got('open-failed').length > 0, null, {polling: 500, timeout: 60000});
  if (await page.evaluate(() => window.got('opened')[1]?.atlas) !== 'second.atlas') throw new Error('second open: ' + JSON.stringify(await page.evaluate(() => window.__msgs.slice(-3))));
  await frame.locator('.region-item[data-label="second"]').waitFor();
  if (await frame.locator('#modal-overlay:not(.hidden)').count()) throw new Error('second open asked a question');
  pass('7 Discard on close drops the edits; the same frame opens the next atlas without asking');

  // Without ?host the framed app is the normal app.
  const plain = await context.newPage();
  await plain.goto(`${hostOrigin}/?nohost=1&app=${encodeURIComponent(appOrigin)}`);
  const plainFrame = await (async () => {
    for (let i = 0; i < 600; i++) {
      const f = plain.frames().find(f => f.url().startsWith(appOrigin));
      if (f) return f;
      await plain.waitForTimeout(100);
    }
    throw new Error('no app frame');
  })();
  await waitInFrame(plainFrame, () => document.getElementById('status-text').textContent === 'Ready');
  if (!await plainFrame.locator('.btn-open').isVisible()) throw new Error('Open hidden without ?host');
  if (await plainFrame.locator('#host-badge').isVisible()) throw new Error('badge without ?host');
  if (await plain.evaluate(() => window.__msgs.length) !== 0) throw new Error('messages without ?host');
  pass('6 without ?host the framed app behaves as before');
} catch (error) {
  failed = true;
  console.error('FAIL', error.stack || error.message);
} finally {
  await browser?.close();
  for (const server of [appServer, hostServer, rogueServer]) server.close();
}
process.exitCode = failed ? 1 : 0;
