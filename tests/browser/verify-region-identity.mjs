/**
 * Synthetic-divergence contract test for the region-identity (key vs.
 * label) refactor. Nothing in production behavior creates a real
 * key != label divergence yet (that's the not-yet-built Rename feature) --
 * this test forces one via the __testOnlySetLabel test seam and proves the
 * wiring is correct: engine-facing operations still address by KEY, every
 * user-facing surface renders the LABEL. See
 * docs/superpowers/specs/2026-09-01-region-identity-key-refactor-design.md
 * for the full design this verifies.
 *
 * Not part of `node --test` (needs a browser + playwright-core). Run via:
 *
 *   node tests/browser/verify-region-identity.mjs
 *
 * playwright-core is located via $PLAYWRIGHT_CORE, a bare import, then
 * common global locations; the script SKIPS (exit 0) if none resolve --
 * same convention as every other file in tests/browser/.
 */
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WWW  = path.resolve(HERE, '..', '..', 'www');

async function loadChromium() {
  const candidates = [
    process.env.PLAYWRIGHT_CORE,
    'playwright-core',
    'playwright',
    path.join(os.homedir(), '.npm-global/lib/node_modules/@playwright/mcp/node_modules/playwright-core/index.js'),
  ].filter(Boolean);
  for (const c of candidates) {
    try {
      const mod = await import(c);
      const pw = mod.chromium ? mod : mod.default;
      if (pw && pw.chromium) return pw.chromium;
    } catch { /* try next */ }
  }
  return null;
}

const chromium = await loadChromium();
if (!chromium) {
  console.log('SKIP: playwright-core not found (set $PLAYWRIGHT_CORE to its index.js to run).');
  process.exit(0);
}

const MIME = {
  '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html',
  '.png': 'image/png', '.json': 'application/json', '.ico': 'image/x-icon',
};
const server = http.createServer((req, res) => {
  let p = decodeURIComponent(req.url.split('?')[0]);
  if (p === '/') p = '/index.html';
  const filePath = path.join(WWW, p);
  if (!filePath.startsWith(WWW) || !fs.existsSync(filePath)) { res.statusCode = 404; return res.end('nf'); }
  res.setHeader('content-type', MIME[path.extname(filePath)] || 'text/plain');
  res.end(fs.readFileSync(filePath));
});
await new Promise((r) => server.listen(0, r));
const URL_ROOT = `http://127.0.0.1:${server.address().port}/`;

const MOD_PNG = fs.readFileSync(path.join(HERE, 'fixtures/opaque-transparent/fixture-opaque.png'));

let pass = 0, fail = 0;
const check = (name, ok, detail = '') => {
  console.log(`${ok ? 'PASS ' : 'FAIL '} ${name}${detail ? ` (${detail})` : ''}`);
  ok ? pass++ : fail++;
};

/** Load a synthetic 2-region single-page atlas through the app's real load path. */
async function loadFixtureAtlas(page) {
  return page.evaluate(async () => {
    const { AtlasAPI } = await import('./js/atlas-api.js');
    const { loadRegions } = await import('./js/region-list.js');
    const { state } = await import('./js/state.js');

    const solid = (w, h, rgba) => {
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      const ctx = c.getContext('2d');
      ctx.fillStyle = `rgba(${rgba.join(',')})`;
      ctx.fillRect(0, 0, w, h);
      return c;
    };
    const toFile = (canvas, name) => new Promise((res) =>
      canvas.toBlob((b) => res(new File([b], name, { type: 'image/png' })), 'image/png'));

    const atlasText = `page1.png
size: 100, 100
alpha
bounds: 0, 0, 30, 30
beta
bounds: 30, 0, 30, 30
`;
    const atlasFile = new File([atlasText], 'identity.atlas', { type: 'text/plain' });
    const p1 = await toFile(solid(100, 100, [40, 80, 120, 255]), 'page1.png');
    const ok = await AtlasAPI.load_atlas_from_file(atlasFile, { 'page1.png': p1 });
    if (!ok) return { ok: false };
    state.selectedIndices.clear();
    state.lastClickIndex = -1;
    await loadRegions();
    return { ok: true };
  });
}

/** Force "alpha"'s label to diverge -- never reachable via window.AtlasAPI. */
async function divergeAlphaLabel(page) {
  return page.evaluate(async () => {
    const { __testOnlySetLabel } = await import('./js/atlas-api.js');
    __testOnlySetLabel('alpha', 'forearm');
  });
}

const readRegionList = (page) => page.evaluate(() => ({
  labels: [...document.querySelectorAll('.region-item')].map((el) => el.innerText),
  keys: [...document.querySelectorAll('.region-item')].map((el) => el.dataset.key),
}));

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));

  await page.goto(URL_ROOT);
  await page.waitForTimeout(150);

  const loaded = await loadFixtureAtlas(page);
  check('fixture atlas loaded', loaded.ok);

  // 1. Pre-divergence: key === label, region-list shows "alpha" plainly.
  let list = await readRegionList(page);
  check('pre-divergence: region-list shows "alpha"', list.labels.includes('alpha'), list.labels.join(','));

  // 2. Apply a REAL pixel mod to "alpha" through the actual UI (native file
  //    chooser) -- matching tests/browser/verify-ui-flows.mjs's pattern.
  await page.click('#mode-modify');
  await page.waitForTimeout(150);
  const items = page.locator('.region-item');
  await items.nth(0).click(); // "alpha" is index 0
  await page.waitForTimeout(120);
  const chooser = page.waitForEvent('filechooser');
  await page.click('#btn-modify-sel');
  await (await chooser).setFiles({ name: 'mod.png', mimeType: 'image/png', buffer: MOD_PNG });
  await page.waitForTimeout(400);

  const hasPendingMod = await page.evaluate(async () => {
    const { AtlasAPI } = await import('./js/atlas-api.js');
    return AtlasAPI.has_pending_modifications();
  });
  check('mod applied to "alpha", pending modifications true', hasPendingMod);

  // 3. NOW diverge "alpha"'s label -- deliberately AFTER the mod batch
  //    already targets its key, so the coupling below actually exercises
  //    refreshModifiedHighlight()'s key-based membership check, not just
  //    its label rendering.
  await divergeAlphaLabel(page);
  await page.evaluate(async () => {
    const { loadRegions } = await import('./js/region-list.js');
    await loadRegions();
  });

  // 4. The modified-highlight must survive the label swap: "forearm*", not
  //    "alpha*" and not a bare "forearm" with the highlight lost. This is
  //    the exact case a substitute scrutinize pass on the design spec found
  //    was left unstated in an earlier revision.
  list = await readRegionList(page);
  const idx = list.keys.indexOf('alpha');
  check('post-divergence: "forearm*" shown (label rendered AND modified-highlight survived)',
    idx !== -1 && list.labels[idx] === 'forearm*', list.labels.join(','));

  // 5. Engine-facing selection is still keyed by "alpha", never "forearm".
  await items.nth(idx).click();
  await page.waitForTimeout(120);
  const selectedKeys = await page.evaluate(async () => {
    const { getSelectedKeys } = await import('./js/state.js');
    return getSelectedKeys();
  });
  check('selection is still keyed by "alpha", not "forearm"',
    selectedKeys.length === 1 && selectedKeys[0] === 'alpha', selectedKeys.join(','));

  // 6. The save-image filename path (drop.js:198) is fed by labels, not keys.
  const selectedLabels = await page.evaluate(async () => {
    const { getSelectedLabels } = await import('./js/state.js');
    return getSelectedLabels();
  });
  check('getSelectedLabels() returns "forearm" (feeds the saved-image filename)',
    selectedLabels.length === 1 && selectedLabels[0] === 'forearm', selectedLabels.join(','));

  // 7. Preview status text (extract mode) renders the label, not the key.
  await page.click('#mode-extract');
  await page.waitForTimeout(150);
  await page.click('#btn-modal-confirm'); // dismiss the pending-mods guard modal
  await page.waitForTimeout(150);
  await items.nth(idx).click();
  await page.waitForTimeout(150);
  const statusText = await page.evaluate(() => document.getElementById('status-text').innerText);
  check('preview status text shows "Previewing: forearm", not "alpha"',
    statusText.includes('forearm') && !statusText.includes('Previewing: alpha'), statusText);

  check('no page errors during the run', errors.length === 0, errors.join('; '));

  await browser.close();
  server.close();

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail > 0 ? 1 : 0);
})();
