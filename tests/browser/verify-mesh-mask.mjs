/**
 * Browser verification for region-mesh-mask.js's rasterizeMeshMask().
 *
 * Loads the REAL browser Canvas 2D code in a headless Chromium and asserts the
 * rasterized UV polygon is opaque inside the shape and transparent outside it.
 *
 * This is intentionally NOT part of `node --test` (it needs a browser +
 * playwright-core, which are not repo dependencies). Run it directly:
 *
 *   node tests/browser/verify-mesh-mask.mjs
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
    + 'The pure geometry is exercised here only in a real browser.');
  process.exit(0);
}

const HARNESS = `<!doctype html><meta charset=utf8><body><script type="module">
import { rasterizeMeshMask } from '/www/js/region-mesh-mask.js';
function alpha(c, x, y){ return c.getContext('2d').getImageData(x, y, 1, 1).data[3]; }
window.runCase = (name) => {
  const results = [];
  const check = (label, cond, detail) => results.push({ label, ok: !!cond, detail });

  if (name === 'triangle-topleft') {
    const uvs = [0, 0, 1, 0, 0, 1];
    const triangles = [0, 1, 2];
    const canvas = rasterizeMeshMask(uvs, triangles, 10, 10);
    check('canvas.width === 10', canvas.width === 10, 'width=' + canvas.width);
    check('canvas.height === 10', canvas.height === 10, 'height=' + canvas.height);
    const inside = alpha(canvas, 1, 1);
    const outside = alpha(canvas, 8, 8);
    check('inside alpha > 200', inside > 200, 'inside=' + inside);
    check('outside alpha === 0', outside === 0, 'outside=' + outside);
  } else if (name === 'quad-full') {
    const uvs = [0, 0, 1, 0, 1, 1, 0, 1];
    const triangles = [0, 1, 2, 0, 2, 3];
    const canvas = rasterizeMeshMask(uvs, triangles, 4, 4);
    const center = alpha(canvas, 2, 2);
    check('center alpha > 200', center > 200, 'center=' + center);
  } else if (name === 'triangle-nonsquare-transpose-check') {
    // Non-square canvas (20x10) with the same triangle shape as
    // triangle-topleft. On a square canvas a width/height transpose bug is
    // invisible (the shape is symmetric under x/y swap); here it isn't:
    // (12,1) is inside under the correct w,h mapping but outside under a
    // transposed h,w mapping, and (4,9) is the reverse. Either mistake
    // flips one of these two checks.
    const uvs = [0, 0, 1, 0, 0, 1];
    const triangles = [0, 1, 2];
    const canvas = rasterizeMeshMask(uvs, triangles, 20, 10);
    check('canvas.width === 20', canvas.width === 20, 'width=' + canvas.width);
    check('canvas.height === 10', canvas.height === 10, 'height=' + canvas.height);
    const a = alpha(canvas, 12, 1);
    const b = alpha(canvas, 4, 9);
    check('(12,1) alpha > 200 (inside under correct w,h mapping)', a > 200, 'a=' + a);
    check('(4,9) alpha === 0 (outside under correct w,h mapping)', b === 0, 'b=' + b);
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

const cases = ['triangle-topleft', 'quad-full', 'triangle-nonsquare-transpose-check'];
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
