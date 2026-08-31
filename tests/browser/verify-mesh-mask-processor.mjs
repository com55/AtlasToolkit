/**
 * Browser verification for the mesh-mask wiring added to
 * AtlasProcessor.extractRegion() in atlas-extracter.js.
 *
 * Loads the REAL AtlasProcessor (which needs canvas + Image loading, i.e. a
 * DOM) in headless Chromium and asserts:
 *   - masking is OFF by default and flips on via setMeshMaskData(lookup, true)
 *   - a missing lookup entry falls back to the unmasked full rectangle
 *   - the PMA gate (!page?.pma, checked in atlas-extracter.js, not
 *     core-region-ops.js) suppresses masking even when enabled + entry exists
 *
 * This is intentionally NOT part of `node --test` (it needs a browser +
 * playwright-core, which are not repo dependencies). Run it directly:
 *
 *   node tests/browser/verify-mesh-mask-processor.mjs
 *
 * playwright-core is located via, in order: $PLAYWRIGHT_CORE, a bare
 * `import('playwright-core')`, then a small set of common global locations.
 * If none resolve, the script SKIPS (exit 0) with a message rather than
 * failing.
 */
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');

async function loadChromium() {
  const candidates = [
    process.env.PLAYWRIGHT_CORE,
    'playwright-core',
    'playwright',
    path.join(os.homedir(), '.npm-global/lib/node_modules/@playwright/mcp/node_modules/playwright-core/index.js'),
    path.join(os.homedir(), '.npm-global/lib/node_modules/playwright-core/index.js'),
    '/usr/lib/node_modules/playwright-core/index.js',
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
  console.log('SKIP: playwright-core not found (set $PLAYWRIGHT_CORE to its index.js to run). '
    + 'The canvas compositing is exercised here only in a real browser.');
  process.exit(0);
}

const HARNESS = `<!doctype html><meta charset=utf8><body><script type="module">
import { AtlasProcessor } from '/www/js/atlas-extracter.js';

const ATLAS_TEXT = \`page1.png
size: 10,10
myregion
bounds: 0, 0, 10, 10
\`;

const PMA_ATLAS_TEXT = \`page1.png
size: 10,10
pma: true
myregion
bounds: 0, 0, 10, 10
\`;

function solidDataUrl(w, h, color) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const ctx = c.getContext('2d');
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, w, h);
  return c.toDataURL();
}
function alpha(canvas, x, y) { return canvas.getContext('2d').getImageData(x, y, 1, 1).data[3]; }

window.runCase = async (name) => {
  const results = [];
  const check = (label, cond, detail) => results.push({ label, ok: !!cond, detail });

  if (name === 'mask-off-by-default-then-on-differs') {
    const proc = new AtlasProcessor(ATLAS_TEXT);
    await proc.loadImages({ 'page1.png': solidDataUrl(10, 10, '#f00') });
    const lookup = new Map([['myregion', { uvs: [0, 0, 1, 0, 0, 1], triangles: [0, 1, 2] }]]); // top-left-half triangle, same shape used elsewhere in this feature

    proc.setMeshMaskData(lookup, false); // disabled
    const unmasked = proc.extractRegion('myregion');
    const unmaskedOutside = alpha(unmasked, 8, 8); // outside the triangle

    proc.setMeshMaskData(lookup, true); // enabled
    const masked = proc.extractRegion('myregion');
    const maskedInside = alpha(masked, 1, 1);   // inside the triangle
    const maskedOutside = alpha(masked, 8, 8);  // outside the triangle

    check('disabled: full rectangle, no masking (alpha === 255)', unmaskedOutside === 255, 'unmaskedOutside=' + unmaskedOutside);
    check('enabled: inside triangle stays opaque (alpha > 200)', maskedInside > 200, 'maskedInside=' + maskedInside);
    check('enabled: outside triangle masked to transparent (alpha === 0)', maskedOutside === 0, 'maskedOutside=' + maskedOutside);
  } else if (name === 'no-lookup-entry-falls-back-to-unmasked') {
    const proc = new AtlasProcessor(ATLAS_TEXT);
    await proc.loadImages({ 'page1.png': solidDataUrl(10, 10, '#0f0') });
    const emptyLookup = new Map(); // no entry for 'myregion'
    proc.setMeshMaskData(emptyLookup, true); // enabled, but no matching entry
    const out = proc.extractRegion('myregion');
    const outsideWhereATriangleWouldHaveMasked = alpha(out, 8, 8);
    check('no lookup entry -> meshGeometry null -> unmasked full rectangle (alpha === 255)', outsideWhereATriangleWouldHaveMasked === 255, 'outside=' + outsideWhereATriangleWouldHaveMasked);
  } else if (name === 'pma-page-disables-masking-even-when-enabled') {
    const proc = new AtlasProcessor(PMA_ATLAS_TEXT);
    await proc.loadImages({ 'page1.png': solidDataUrl(10, 10, '#00f') });
    const lookup = new Map([['myregion', { uvs: [0, 0, 1, 0, 0, 1], triangles: [0, 1, 2] }]]);
    proc.setMeshMaskData(lookup, true); // enabled, entry exists, but page.pma === true
    const out = proc.extractRegion('myregion');
    const outsideWhereATriangleWouldHaveMasked = alpha(out, 8, 8);
    check('PMA gate suppresses masking regardless of enabled+lookup (alpha === 255)', outsideWhereATriangleWouldHaveMasked === 255, 'outside=' + outsideWhereATriangleWouldHaveMasked);
  } else if (name === 'null-lookup-with-enabled-true-does-not-throw') {
    // setMeshMaskData's own docstring says lookup may be null (no .skel /
    // parse failed / unsupported version) independently of the enabled
    // flag -- a documented, expected state that Task 5's live UI wiring can
    // plausibly produce (toggle flipped on before/without a successfully
    // parsed .skel). Distinct from the empty-Map case above: this exercises
    // the maskEnabled-AND-meshLookup short-circuit on a null _meshLookup
    // itself, not Map.get() finding no entry in a real Map.
    // Found missing by Fable scrutinize review, 2026-08-31 (mutation-tested:
    // dropping just the _meshLookup-null-check half of the gate still passed
    // every other case here, then threw on this exact state).
    const proc = new AtlasProcessor(ATLAS_TEXT);
    await proc.loadImages({ 'page1.png': solidDataUrl(10, 10, '#f0f') });
    let threw = false;
    let out = null;
    try {
      proc.setMeshMaskData(null, true); // enabled, but no lookup at all
      out = proc.extractRegion('myregion');
    } catch (e) {
      threw = true;
    }
    check('does not throw when lookup is null and enabled is true', !threw, 'threw=' + threw);
    if (!threw) {
      const outsideWhereATriangleWouldHaveMasked = alpha(out, 8, 8);
      check('null lookup -> meshGeometry null -> unmasked full rectangle (alpha === 255)', outsideWhereATriangleWouldHaveMasked === 255, 'outside=' + outsideWhereATriangleWouldHaveMasked);
    }
  } else {
    results.push({ label: 'unknown case', ok: false, detail: name });
  }
  return results;
};
window.__ready = true;
</script></body>`;

const server = http.createServer((req, res) => {
  if (req.url === '/harness') { res.setHeader('content-type', 'text/html'); return res.end(HARNESS); }
  const filePath = path.join(ROOT, decodeURIComponent(req.url.split('?')[0]));
  if (!filePath.startsWith(ROOT) || !fs.existsSync(filePath)) { res.statusCode = 404; return res.end('nf'); }
  res.setHeader('content-type', path.extname(filePath) === '.js' ? 'text/javascript' : 'text/plain');
  res.end(fs.readFileSync(filePath));
});

await new Promise((r) => server.listen(0, r));
const port = server.address().port;

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.goto(`http://localhost:${port}/harness`);
await page.waitForFunction('window.__ready === true');

const cases = ['mask-off-by-default-then-on-differs', 'no-lookup-entry-falls-back-to-unmasked', 'pma-page-disables-masking-even-when-enabled', 'null-lookup-with-enabled-true-does-not-throw'];
let pass = 0, fail = 0;
for (const name of cases) {
  const results = await page.evaluate((n) => window.runCase(n), name);
  const bad = results.filter((r) => !r.ok);
  if (bad.length === 0) {
    pass++;
    console.log(`PASS  ${name}`);
  } else {
    fail++;
    console.log(`FAIL  ${name}`);
    for (const r of results) {
      console.log(`      ${r.ok ? 'ok  ' : 'FAIL'} ${r.label}${r.detail ? ' (' + r.detail + ')' : ''}`);
    }
  }
}

await browser.close();
server.close();
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
