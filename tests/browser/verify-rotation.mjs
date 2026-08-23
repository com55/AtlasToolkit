/**
 * Rotation-direction empirical verification for core-region-ops.js.
 *
 * Loads the REAL browser Canvas 2D code (cropAndRotate / extractRegionFromPage)
 * in a headless Chromium and asserts pixel-identical output to the PIL ground
 * truth in ground_truth.json (generated from the main-branch region_ops.py by
 * gen-ground-truth.py). PIL is the normative reference; a pixel match proves
 * both rotation direction and non-mirroring at once.
 *
 * This is intentionally NOT part of `node --test` (it needs a browser +
 * playwright-core, which are not repo dependencies). Run it directly:
 *
 *   node test/browser/verify-rotation.mjs
 *
 * playwright-core is located via, in order: $PLAYWRIGHT_CORE, a bare
 * `import('playwright-core')`, then a small set of common global locations.
 * If none resolve, the script SKIPS (exit 0) with a message rather than
 * failing — the portable pixel assertions live in the ground truth, and the
 * pure-Node algebra (roundHalfEven / overlayRect) is covered by
 * test/core-region-ops.test.js.
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
    + 'Ground truth + pure-Node algebra are still covered by node --test.');
  process.exit(0);
}

const groundTruth = JSON.parse(fs.readFileSync(path.join(HERE, 'ground_truth.json'), 'utf8'));

const HARNESS = `<!doctype html><meta charset=utf8><body><script type="module">
import { cropAndRotate, extractRegionFromPage } from '/www/js/core-region-ops.js';
function gridToCanvas(g){
  const c=document.createElement('canvas'); c.width=g.w; c.height=g.h;
  const ctx=c.getContext('2d'); const id=ctx.createImageData(g.w,g.h);
  for(let i=0;i<g.pixels.length;i++){ const [r,gg,b,a]=g.pixels[i];
    id.data[i*4]=r; id.data[i*4+1]=gg; id.data[i*4+2]=b; id.data[i*4+3]=a; }
  ctx.putImageData(id,0,0); return c;
}
function canvasGrid(c){
  const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data; const pixels=[];
  for(let i=0;i<d.length;i+=4) pixels.push([d[i],d[i+1],d[i+2],d[i+3]]);
  return {w:c.width,h:c.height,pixels};
}
window.runCase = (cse) => {
  const src = gridToCanvas(cse.source); const a = cse.args;
  const out = cse.op==='cropAndRotate'
    ? cropAndRotate(src, a.x, a.y, a.w, a.h, a.rotate)
    : extractRegionFromPage(src, {x:a.x,y:a.y,w:a.w,h:a.h,rotate:a.rotate,offsets:a.offsets}, {scaleX:a.scaleX,scaleY:a.scaleY});
  return canvasGrid(out);
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

let pass = 0, fail = 0;
for (const cse of groundTruth.cases) {
  const got = await page.evaluate((c) => window.runCase(c), cse);
  const exp = cse.expected;
  const ok = got.w === exp.w && got.h === exp.h
    && got.pixels.length === exp.pixels.length
    && got.pixels.every((p, i) => p.every((v, k) => v === exp.pixels[i][k]));
  if (ok) { pass++; console.log(`PASS  ${cse.name}`); }
  else {
    fail++;
    console.log(`FAIL  ${cse.name}`);
    console.log(`      expected ${exp.w}x${exp.h} ${JSON.stringify(exp.pixels)}`);
    console.log(`      got      ${got.w}x${got.h} ${JSON.stringify(got.pixels)}`);
  }
}

await browser.close();
server.close();
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
