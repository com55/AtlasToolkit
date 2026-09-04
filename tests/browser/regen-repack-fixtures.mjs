/**
 * Regenerates the repack-related fixtures in ground_truth_ops.json from the
 * JS engine's OWN output, instead of the pinned Python oracle. Repack packing
 * *shape* is no longer required to match repacker.py (see NOTES.md
 * "Deviation: repack packing diverges from repacker.py") -- this script
 * captures whatever the current _shelfPack implementation actually produces
 * for the same input sprite sets already in ground_truth_ops.json's
 * repackCases/realworldCases groups, and overwrites those cases'
 * expectedCanvas/expectedPages/expectedAtlasText fields in place. Every
 * other field (atlasText, baseImage, spriteNames, sprites, pageInfos,
 * regionMetas, assertDedupBoundsEqual, etc.) is left untouched.
 *
 * Run this after any change to _shelfPack's packing behavior (a new spec in
 * the Repack rework series, or a deliberate tuning of the existing one) to
 * re-pin the golden fixtures to the new expected output. Review the diff in
 * ground_truth_ops.json before committing -- this script does not validate
 * that the new output is CORRECT, only that it is what the engine currently
 * produces.
 *
 * Mirrors verify-ops.mjs's own harness/server setup. Run via:
 *   node tests/browser/regen-repack-fixtures.mjs
 */
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');

const WORKSPACES_CANDIDATES = [
  path.join(ROOT, '.workspaces'),
  path.resolve(ROOT, '..', '..', '.workspaces'),
].filter((p) => fs.existsSync(p));
const WORKSPACES_ROOT = WORKSPACES_CANDIDATES[0] || null;

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
  console.log('ABORT: playwright-core not found (set $PLAYWRIGHT_CORE to its index.js). Cannot regenerate fixtures without a browser.');
  process.exit(1);
}

const groundTruthPath = path.join(HERE, 'ground_truth_ops.json');
const gt = JSON.parse(fs.readFileSync(groundTruthPath, 'utf8'));

const HARNESS = `<!doctype html><meta charset=utf8><body><script type="module">
import { AtlasModifier, repackMultiPage } from '/www/js/atlas-modifier.js';

function decodeGrid(g) {
  const bin = atob(g.b64);
  const bytes = new Uint8ClampedArray(g.w * g.h * 4);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const canvas = document.createElement('canvas');
  canvas.width = g.w; canvas.height = g.h;
  const ctx = canvas.getContext('2d');
  ctx.putImageData(new ImageData(bytes, g.w, g.h), 0, 0);
  return canvas;
}

function encodeGrid(canvas) {
  const ctx = canvas.getContext('2d');
  const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
  let bin = '';
  for (let i = 0; i < data.length; i++) bin += String.fromCharCode(data[i]);
  return { w: canvas.width, h: canvas.height, b64: btoa(bin) };
}

window.runRepackSingleCase = async (cse) => {
  const baseCanvas = decodeGrid(cse.baseImage);
  const modifier = new AtlasModifier(cse.atlasText, 'dummy.atlas', baseCanvas);
  const { canvas, atlasText } = await modifier.repack(baseCanvas, cse.atlasText);
  return { canvas: encodeGrid(canvas), atlasText };
};

window.runRepackMultiCase = async (cse) => {
  const allSprites = {};
  for (const name of cse.spriteNames) allSprites[name] = decodeGrid(cse.sprites[name]);
  const { pages, atlasText } = await repackMultiPage(allSprites, cse.numPages, cse.pageInfos, cse.regionMetas);
  return { pages: pages.map(encodeGrid), atlasText };
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
page.on('pageerror', (err) => console.log('  [pageerror]', err.message));
await page.goto(`http://localhost:${port}/harness`);
await page.waitForFunction('window.__ready === true');

let updated = 0;

for (const cse of gt.repackCases) {
  if (cse.op === 'repackSinglePage') {
    const { canvas, atlasText } = await page.evaluate((c) => window.runRepackSingleCase(c), cse);
    cse.expectedCanvas = canvas;
    cse.expectedAtlasText = atlasText;
    updated++;
    console.log(`Updated: ${cse.name}`);
  } else if (cse.op === 'repackMultiPage') {
    const { pages, atlasText } = await page.evaluate((c) => window.runRepackMultiCase(c), cse);
    cse.expectedPages = pages;
    cse.expectedAtlasText = atlasText;
    updated++;
    console.log(`Updated: ${cse.name}`);
  }
}

if (!WORKSPACES_ROOT) {
  console.log('SKIP realworldCases repack fixture: .workspaces/ not found in this environment -- rerun this script somewhere it is present to update that fixture too.');
} else {
  for (const cse of gt.realworldCases) {
    if (cse.op !== 'repackMultiPage') continue;
    const { pages, atlasText } = await page.evaluate((c) => window.runRepackMultiCase(c), cse);
    cse.expectedPages = pages;
    cse.expectedAtlasText = atlasText;
    updated++;
    console.log(`Updated: ${cse.name}`);
  }
}

await browser.close();
server.close();

fs.writeFileSync(groundTruthPath, JSON.stringify(gt, null, 2) + '\n');
console.log(`\n${updated} fixture(s) updated. Review the diff in ground_truth_ops.json before committing.`);
