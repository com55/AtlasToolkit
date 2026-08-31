/**
 * Browser verification for the optional mesh-mask compositing added to
 * core-region-ops.js's extractRegionFromPage().
 *
 * Loads the REAL browser Canvas 2D code in a headless Chromium and asserts the
 * mesh mask is composited at the correct dimensions on each of the two return
 * paths (the historically-buggy coordinate-space distinction):
 *   - no-offsets path masks the packed `sprite` at its own (currentW, currentH)
 *   - offsets path masks the `canvas` at (origW, origH) AFTER the paste
 * and that the pre-existing 3-argument call form is byte-for-byte unaffected.
 *
 * This is intentionally NOT part of `node --test` (it needs a browser +
 * playwright-core, which are not repo dependencies). Run it directly:
 *
 *   node tests/browser/verify-mesh-mask-integration.mjs
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
import { extractRegionFromPage } from '/www/js/core-region-ops.js';
function makeSourceCanvas(w, h, color) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const ctx = c.getContext('2d');
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, w, h);
  return c;
}
function alpha(c, x, y){ return c.getContext('2d').getImageData(x, y, 1, 1).data[3]; }
window.runCase = (name) => {
  const results = [];
  const check = (label, cond, detail) => results.push({ label, ok: !!cond, detail });

  if (name === 'no-offsets-masks-at-sprite-size') {
    const src = makeSourceCanvas(10, 10, '#f00');
    const region = { x: 0, y: 0, w: 10, h: 10, rotate: 0, offsets: null };
    const meshGeometry = { uvs: [0, 0, 1, 0, 0, 1], triangles: [0, 1, 2] };
    const out = extractRegionFromPage(src, region, null, meshGeometry);
    const inside = alpha(out, 1, 1);
    const outside = alpha(out, 8, 8);
    check('inside triangle should stay opaque (alpha > 200)', inside > 200, 'inside=' + inside);
    check('outside triangle should be masked to transparent (alpha === 0)', outside === 0, 'outside=' + outside);
  } else if (name === 'offsets-branch-masks-at-origWH-not-packed-size') {
    // Packed sprite is 15x15, ORIGINAL (pre-stripped) canvas is 40x40, packed
    // content pasted at (3,3) per offsets=[offX=3, offY=22, origW=40, origH=40]
    // (bottom-origin offY: pasteY = origH - offY - currentH = 40 - 22 - 15 = 3).
    // Content spans canvas x in [3,18).
    //
    // Mesh quad covers u in [0, 0.37], v in [0, 1] (a left strip of the
    // ORIGINAL 40x40 UV space). Two thresholds fall out of this:
    //   correct (origW=40, post-paste):        canvas x = 0.37*40   = 14.8
    //   packed-size regression (currentW=15, pre-paste, +pasteX 3): canvas x = 0.37*15+3 = 8.55
    // Three query pixels, each with a numerically-verified >=1-full-pixel
    // margin from every relevant threshold/edge (no anti-aliasing ambiguity):
    //   x=8  (margin 5)   : opaque under BOTH mappings — sanity, content exists at all.
    //   x=11 (margin 2.45): opaque under correct, transparent under the
    //                       packed-size regression — the coordinate-space discriminator.
    //   x=16 (margin 1)   : transparent under correct mapping — proves the mask
    //                       actually removes content on this branch at all (this
    //                       case previously had no such assertion: deleting the
    //                       offsets-branch maskInPlace() call outright still
    //                       passed 3/3, since every prior check here was an
    //                       "opaque" check — found by Fable scrutinize review,
    //                       2026-08-29, then verified adversarially that the
    //                       fix actually catches both the coordinate-space
    //                       regression AND a deleted maskInPlace call).
    const src = makeSourceCanvas(15, 15, '#f00');
    const region = { x: 0, y: 0, w: 15, h: 15, rotate: 0, offsets: [3, 22, 40, 40] };
    const meshGeometry = { uvs: [0, 0, 0.37, 0, 0.37, 1, 0, 1], triangles: [0, 1, 2, 0, 2, 3] };
    const out = extractRegionFromPage(src, region, null, meshGeometry);
    check('out.width === 40', out.width === 40, 'width=' + out.width);
    check('out.height === 40', out.height === 40, 'height=' + out.height);
    const sanity = alpha(out, 8, 10);
    check('content visible well inside opaque range of both mappings (alpha > 200)', sanity > 200, 'sanity=' + sanity);
    const discriminator = alpha(out, 11, 10);
    check('(11,10) opaque under correct origW/origH mapping, transparent under packed-size regression (alpha > 200)', discriminator > 200, 'discriminator=' + discriminator);
    const masked = alpha(out, 16, 10);
    check('(16,10) masked to transparent under correct mapping — proves masking happened at all (alpha === 0)', masked === 0, 'masked=' + masked);
  } else if (name === 'existing-3-arg-calls-unaffected') {
    const src = makeSourceCanvas(5, 5, '#0f0');
    const region = { x: 0, y: 0, w: 5, h: 5, rotate: 0, offsets: null };
    const out = extractRegionFromPage(src, region, null); // no 4th arg
    const center = alpha(out, 2, 2);
    check('unmasked region is fully opaque, unchanged (alpha === 255)', center === 255, 'center=' + center);
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

const cases = ['no-offsets-masks-at-sprite-size', 'offsets-branch-masks-at-origWH-not-packed-size', 'existing-3-arg-calls-unaffected'];
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
