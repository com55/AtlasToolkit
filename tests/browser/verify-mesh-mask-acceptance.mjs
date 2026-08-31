/**
 * Task 7 acceptance checks (design spec's Testing section, items 3-4) using
 * the committed tests/fixtures/ch0169-mesh-sample.json -- real Spine 4.2
 * Mesh geometry (uvs/triangles only, extracted from a real local-only asset;
 * no copyrighted texture/skeleton binary committed) so these run everywhere,
 * not just where .workspaces/CH0169/ happens to exist locally. See
 * tests/mesh-acceptance.test.js for the type-classification check (item 1,
 * plain node --test, self-skips without the local .skel) and
 * tests/browser/verify-ops.mjs for the regression check (item 2).
 *
 * Not part of `node --test` (needs a browser + playwright-core). Run via:
 *
 *   node tests/browser/verify-mesh-mask-acceptance.mjs
 *
 * playwright-core is located via $PLAYWRIGHT_CORE, a bare import, then
 * common global locations; the script SKIPS (exit 0) if none resolve.
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
    + 'Item 1 (attachment-type classification) still runs standalone in tests/mesh-acceptance.test.js.');
  process.exit(0);
}

const HARNESS = `<!doctype html><meta charset=utf8><body><script type="module">
import { rasterizeMeshMask } from '/www/js/region-mesh-mask.js';
import { AtlasProcessor } from '/www/js/atlas-extracter.js';

function alpha(canvas, x, y) { return canvas.getContext('2d').getImageData(x, y, 1, 1).data[3]; }
function solidDataUrl(w, h, color) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const ctx = c.getContext('2d');
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, w, h);
  return c.toDataURL();
}
async function loadFixture() {
  const res = await fetch('/tests/fixtures/ch0169-mesh-sample.json');
  return res.json();
}

window.runCase = async (name) => {
  const results = [];
  const check = (label, cond, detail) => results.push({ label, ok: !!cond, detail });
  const fixture = await loadFixture();

  if (name === 'single-region-fast-path-is-masked') {
    // Design spec Testing item 3: getPreviewDataURL's images.length === 1
    // branch specifically -- not just the multi-region composite loop.
    const { uvs, triangles } = fixture.CH0169_1;
    const atlasText = 'page1.png\\nsize: 100,100\\nmyregion\\nbounds: 0, 0, 100, 100\\n';
    const proc = new AtlasProcessor(atlasText);
    await proc.loadImages({ 'page1.png': solidDataUrl(100, 100, '#f00') });
    proc.setMeshMaskData(new Map([['myregion', { uvs, triangles }]]), true);

    const url = await proc.getPreviewDataURL(['myregion']); // 1 name -> fast path
    check('getPreviewDataURL returned a URL', !!url, 'url=' + url);

    const img = new Image();
    await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = reject; img.src = url; });
    const c = document.createElement('canvas');
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    c.getContext('2d').drawImage(img, 0, 0);

    // CH0169_1's UV bbox (u:[0.192,0.750], v:[0,0.389]) leaves a corner well
    // outside it; if the fast path skipped masking, the whole 100x100
    // rectangle would stay opaque red everywhere, including this corner.
    const outsideCorner = alpha(c, 95, 95);
    check('fast-path output is masked (corner outside mesh bbox is transparent)', outsideCorner === 0, 'outsideCorner=' + outsideCorner);
  } else if (name === 'shape-checks-non-degenerate-for-all-three') {
    // Design spec Testing item 4: an asymmetric mask must be non-uniform --
    // both fully-opaque and fully-transparent pixels present, not
    // degenerating to all-or-nothing. Checked for all three real shapes.
    for (const attachName of ['CH0169_1', 'CH0169_2', 'CH0169_3']) {
      const { uvs, triangles } = fixture[attachName];
      const size = 200;
      const mask = rasterizeMeshMask(uvs, triangles, size, size);
      const ctx = mask.getContext('2d');
      const data = ctx.getImageData(0, 0, size, size).data;
      let opaqueCount = 0, transparentCount = 0;
      for (let i = 3; i < data.length; i += 4) {
        if (data[i] > 200) opaqueCount++;
        else if (data[i] === 0) transparentCount++;
      }
      check(attachName + ': mask has both opaque and transparent pixels (non-degenerate)',
        opaqueCount > 0 && transparentCount > 0,
        'opaque=' + opaqueCount + ' transparent=' + transparentCount + ' total=' + (size * size));
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
  const ext = path.extname(filePath);
  const contentType = ext === '.js' ? 'text/javascript' : ext === '.json' ? 'application/json' : 'text/plain';
  res.setHeader('content-type', contentType);
  res.end(fs.readFileSync(filePath));
});

await new Promise((r) => server.listen(0, r));
const port = server.address().port;

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
page.on('pageerror', (e) => console.log('PAGE ERROR:', e.message));
await page.goto(`http://localhost:${port}/harness`);
await page.waitForFunction('window.__ready === true');

const cases = ['single-region-fast-path-is-masked', 'shape-checks-non-degenerate-for-all-three'];
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
