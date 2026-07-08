/**
 * App-level (AtlasAPI) end-to-end smoke test: drives the exact code path the
 * UI calls (load_atlas_from_file -> enter_modify_mode -> process_mod_image ->
 * toggle_repack) with synthetic File objects, for both a single-page and a
 * multi-page atlas. This checks the WIRING (file loading, session
 * construction, repack-toggle round trip through the public API) that
 * verify-repack-asymmetry.mjs's direct-AtlasSession script doesn't exercise.
 * The offsets-reset-vs-preserved asymmetry itself is proven precisely there;
 * this script just confirms nothing throws and shapes look sane end-to-end
 * through the public AtlasAPI surface, and that multi-page repack toggling
 * still works now that "repack all pages" is gone.
 *
 * Not part of `node --test` (needs a browser + playwright-core). Run via:
 *   node test/browser/verify-app-e2e.mjs
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

const HARNESS = `<!doctype html><meta charset=utf8><body><script type="module">
import { AtlasAPI } from '/www/js/atlas-api.js';

function solidCanvas(w, h, rgba) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const ctx = c.getContext('2d');
  ctx.fillStyle = \`rgba(\${rgba[0]},\${rgba[1]},\${rgba[2]},\${rgba[3]})\`;
  ctx.fillRect(0, 0, w, h);
  return c;
}
function canvasToFile(canvas, name) {
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(new File([blob], name, { type: 'image/png' })), 'image/png');
  });
}
function textFile(text, name) {
  return new File([text], name, { type: 'text/plain' });
}

window.runSinglePage = async () => {
  const atlasText = \`page1.png
size: 100,100
sword
bounds: 0, 0, 10, 10
offsets: 2, 3, 20, 20
shieldA
bounds: 10, 0, 15, 15
offsets: 0, 0, 20, 20
shieldB
bounds: 30, 0, 15, 15
offsets: 0, 0, 20, 20
\`;
  const atlasFile = textFile(atlasText, 'test.atlas');
  const pageFile = await canvasToFile(solidCanvas(100, 100, [10, 20, 30, 255]), 'page1.png');
  const loaded = await AtlasAPI.load_atlas_from_file(atlasFile, { 'page1.png': pageFile });
  const entered = await AtlasAPI.enter_modify_mode();

  const modSword = await canvasToFile(solidCanvas(20, 20, [255, 0, 0, 255]), 'mod1.png');
  const r1 = await AtlasAPI.process_mod_image(modSword, ['sword'], false);

  const modShield = await canvasToFile(solidCanvas(20, 20, [0, 255, 0, 255]), 'mod2.png');
  const r2 = await AtlasAPI.process_mod_image(modShield, ['shieldA', 'shieldB'], false);

  const repacked = await AtlasAPI.toggle_repack(true);
  const unrepacked = await AtlasAPI.toggle_repack(false);

  return {
    loaded, entered: !!entered,
    r1regions: r1 && Object.keys(r1.regions),
    r2regions: r2 && Object.keys(r2.regions),
    repackedRegions: repacked && Object.keys(repacked.regions),
    unrepackedRegions: unrepacked && Object.keys(unrepacked.regions),
  };
};

window.runMultiPage = async () => {
  const atlasText = \`page1.png
size: 50,50
armR
bounds: 0, 0, 10, 10

page2.png
size: 50,50
legL
bounds: 0, 0, 10, 10
\`;
  const atlasFile = textFile(atlasText, 'multi.atlas');
  const p1 = await canvasToFile(solidCanvas(50, 50, [1, 2, 3, 255]), 'page1.png');
  const p2 = await canvasToFile(solidCanvas(50, 50, [4, 5, 6, 255]), 'page2.png');
  const loaded = await AtlasAPI.load_atlas_from_file(atlasFile, { 'page1.png': p1, 'page2.png': p2 });
  const entered = await AtlasAPI.enter_modify_mode();

  const mod = await canvasToFile(solidCanvas(10, 10, [9, 9, 9, 255]), 'mod.png');
  const merged = await AtlasAPI.process_mod_image(mod, ['armR'], false);
  const repacked = await AtlasAPI.toggle_repack(true);
  const unrepacked = await AtlasAPI.toggle_repack(false);

  return {
    loaded, entered: !!entered,
    mergedPageCount: merged && merged.pageCount,
    repackedPageCount: repacked && repacked.pageCount,
    unrepackedPageCount: unrepacked && unrepacked.pageCount,
  };
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
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message));
page.on('console', (m) => { if (m.type() === 'error') pageErrors.push('console.error: ' + m.text()); });
await page.goto(`http://localhost:${port}/harness`);
await page.waitForFunction('window.__ready === true');

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log(`PASS  ${name}`); }
  else { fail++; console.log(`FAIL  ${name}\n      ${JSON.stringify(detail)}`); }
}

const single = await page.evaluate(() => window.runSinglePage());
check('single-page: atlas loaded', single.loaded === true, single);
check('single-page: entered modify mode', single.entered === true, single);
check('single-page: merge batch 1 (sword) returned regions', Array.isArray(single.r1regions) && single.r1regions.includes('sword'), single);
check('single-page: merge batch 2 (shieldA/B) returned regions', Array.isArray(single.r2regions) && single.r2regions.includes('shieldA') && single.r2regions.includes('shieldB'), single);
check('single-page: repack toggle ON returned all 3 regions', Array.isArray(single.repackedRegions) && single.repackedRegions.length === 3, single);
check('single-page: repack toggle OFF (back to merge) returned all 3 regions', Array.isArray(single.unrepackedRegions) && single.unrepackedRegions.length === 3, single);

const multi = await page.evaluate(() => window.runMultiPage());
check('multi-page: atlas loaded', multi.loaded === true, multi);
check('multi-page: entered modify mode', multi.entered === true, multi);
check('multi-page: merge produced 2 pages', multi.mergedPageCount === 2, multi);
check('multi-page: repack toggle ON still produces 2 pages (per-page repack, no "all" mode)', multi.repackedPageCount === 2, multi);
check('multi-page: repack toggle OFF produces 2 pages', multi.unrepackedPageCount === 2, multi);

check('no console/page errors during either scenario', pageErrors.length === 0, pageErrors);

await browser.close();
server.close();
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
