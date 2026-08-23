/**
 * End-to-end proof of the offsets-reset-on-merge vs preserved-on-repack
 * asymmetry (Task 4b, Deliverable 2), driving the REAL AtlasSession +
 * AtlasModifier in a headless Chromium (needs real Canvas 2D).
 *
 * Scenario, one single-page atlas with three mod batches plus one untouched
 * region:
 *  - batch 1: mod applied to ONE region ("sword") whose mod image (20x20)
 *    exactly matches its pristine offsets' canvas size (20x20) ->
 *    isFullCanvas true. Single-region selections can never set
 *    sharedCanvasMod (selectedShareCanvas requires >1 region) — that flag is
 *    for merge's rotate-avoidance only. fullCanvasRegions must key off
 *    isFullCanvas, not sharedCanvasMod, or single-region full-canvas mods
 *    keep stale pristine offsets after repack (see AtlasSession's
 *    _registerModBatch fix — was reading prep.sharedCanvasMod).
 *  - batch 2: mod applied to TWO regions ("shieldA","shieldB") that share
 *    one logical canvas size and both have offsets -> selectedShareCanvas
 *    is true, and the mod image exactly matches that canvas -> isFullCanvas
 *    true -> sharedCanvasMod true -> both names land in fullCanvasRegions.
 *  - batch 3: mod applied to ONE region ("hair") whose mod image (90x160) is
 *    FAR from its pristine offsets' canvas size (50x80) and not a clean
 *    proportional scale (ratioW=1.8, ratioH=2.0, diff=0.2 > the 0.05
 *    tolerance) -> resolveModCanvas's else-branch treats the mod's own
 *    dimensions as the canvas -> isFullCanvas true by construction. This is
 *    the closest mirror of the real-world repro (region orig 557x830 vs mod
 *    image 1211x1697) that broke kayoko_spr.atlas's kayoko_00 region.
 *  - "belt" region: never selected by any batch. Regression guard that
 *    fullCanvasRegions never leaks to regions outside the batches that
 *    produced it.
 *
 * After all batches + repack:
 *  - "sword"/"hair" (single-region full-canvas mods) offsets must be RESET
 *    to default (no offsets line) — this is the bug fix under test.
 *  - "shieldA"/"shieldB" (shared-canvas mod) offsets must be reset to
 *    default too (unaffected by the fix, already worked before).
 *  - "belt" (untouched) offsets must be its ORIGINAL pristine offsets,
 *    verbatim.
 * After merge only (no repack), existing behavior resets EVERY touched
 * region's offsets to default regardless of shared-canvas — asserted too,
 * to make the asymmetry direction unambiguous.
 *
 * Not part of `node --test` (needs a browser + playwright-core). Run via:
 *   node test/browser/verify-repack-asymmetry.mjs
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
    + 'Pure-Node canvas-resolution/offset-branch logic is covered by test/atlas-modifier.test.js.');
  process.exit(0);
}

const ATLAS_TEXT = `page1.png
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
hair
bounds: 70, 0, 10, 15
offsets: 0, 0, 50, 80
belt
bounds: 50, 0, 10, 10
offsets: 5, 5, 30, 30
`;

const HARNESS = `<!doctype html><meta charset=utf8><body><script type="module">
import { AtlasProcessor } from '/www/js/atlas-extracter.js';
import { AtlasSession } from '/www/js/atlas-session.js';

function solidCanvas(w, h, rgba) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const ctx = c.getContext('2d');
  ctx.fillStyle = \`rgba(\${rgba[0]},\${rgba[1]},\${rgba[2]},\${rgba[3]})\`;
  ctx.fillRect(0, 0, w, h);
  return c;
}

window.runScenario = async (atlasText) => {
  const processor = new AtlasProcessor(atlasText);
  const baseCanvas = solidCanvas(100, 100, [10, 20, 30, 255]);
  processor._loadedImages[processor.pages[0].filename] = baseCanvas;

  const session = new AtlasSession(processor, atlasText, 'test.atlas');

  const modSword = solidCanvas(20, 20, [255, 0, 0, 255]);
  const modShield = solidCanvas(20, 20, [0, 255, 0, 255]);
  const modHair = solidCanvas(90, 160, [0, 0, 255, 255]);

  const mergeOnly1 = await session.processModImage(modSword, ['sword'], false);
  const mergeOnly2 = await session.processModImage(modShield, ['shieldA', 'shieldB'], false);
  const mergeOnly3 = await session.processModImage(modHair, ['hair'], false);
  const mergeText = mergeOnly3.regions ? session.active.text : null;

  const repacked = await session.toggleRepack(true);
  const repackText = session.active.text;

  return { mergeText, repackText };
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
page.on('console', (msg) => console.log('  [page]', msg.text()));
page.on('pageerror', (err) => console.log('  [pageerror]', err.message));
await page.goto(`http://localhost:${port}/harness`);
await page.waitForFunction('window.__ready === true');

const { mergeText, repackText } = await page.evaluate((t) => window.runScenario(t), ATLAS_TEXT);

let pass = 0, fail = 0;
function check(name, cond, detail) {
  if (cond) { pass++; console.log(`PASS  ${name}`); }
  else { fail++; console.log(`FAIL  ${name}\n      ${detail}`); }
}

// Matches a region block header + bounds line, with NO following offsets:
// line — robust to being the last line in the text (no trailing newline).
// Tolerates optional index:/rotate: lines between the header and bounds (in
// either order) — "hair" packs with rotate:true, which the original regex
// (index: only) didn't account for.
const noOffsetsAfter = (name) =>
  new RegExp(`${name}\\s*\\n(?:\\s*(?:index|rotate):.*\\n)*\\s*bounds:[^\\n]*(?:\\n(?!\\s*offsets:)|$)`);

// ── Merge-only: resets offsets on every touched region, shared or not ──
check(
  'merge: sword offsets reset to default (no offsets line)',
  noOffsetsAfter('sword').test(mergeText),
  `mergeText:\n${mergeText}`,
);
check(
  'merge: shieldA offsets reset to default (no offsets line)',
  noOffsetsAfter('shieldA').test(mergeText),
  `mergeText:\n${mergeText}`,
);
check(
  'merge: hair offsets reset to default (no offsets line)',
  noOffsetsAfter('hair').test(mergeText),
  `mergeText:\n${mergeText}`,
);

// ── Repack: sword (single-region full-canvas mod) offsets RESET to default ──
// Regression case for the fullCanvasRegions-keyed-off-sharedCanvasMod bug:
// sword's 20x20 mod exactly matches its offsets' canvas (20,20) -> isFullCanvas
// true even though only one region is selected. Before the fix, session.js
// read prep.sharedCanvasMod (false for single-region) instead of
// prep.isFullCanvas, so sword kept its stale pristine offsets (2,3,20,20)
// while its bounds became the new 20x20 packed size — a mismatch.
check(
  'repack: sword offsets RESET to default (no offsets line, single-region full-canvas mod)',
  noOffsetsAfter('sword').test(repackText),
  `repackText:\n${repackText}`,
);
// ── Repack: shieldA/shieldB (shared-canvas batch) reset to default ──
check(
  'repack: shieldA offsets RESET to default (no offsets line, in fullCanvasRegions)',
  noOffsetsAfter('shieldA').test(repackText),
  `repackText:\n${repackText}`,
);
check(
  'repack: shieldB offsets RESET to default (no offsets line, in fullCanvasRegions)',
  noOffsetsAfter('shieldB').test(repackText),
  `repackText:\n${repackText}`,
);
// ── Repack: hair (single-region, large non-proportional mismatch) — the
// closest mirror of the real kayoko_00 repro (orig 557x830 vs mod 1211x1697)
// — offsets RESET to default, not left at the stale pristine (0,0,50,80).
check(
  'repack: hair offsets RESET to default (no offsets line, single-region large-mismatch full-canvas mod)',
  noOffsetsAfter('hair').test(repackText),
  `repackText:\n${repackText}`,
);
// ── Repack: belt (never selected by any batch) keeps its PRISTINE offsets —
// regression guard that fullCanvasRegions never leaks outside its batch.
check(
  'repack: belt (untouched) offsets PRESERVED as pristine (5, 5, 30, 30)',
  /belt\s*\n(?:\s*index:.*\n)?\s*bounds:[^\n]*\n\s*offsets: 5, 5, 30, 30/.test(repackText),
  `repackText:\n${repackText}`,
);

await browser.close();
server.close();
console.log(`\n${pass} passed, ${fail} failed`);
console.log(`\n--- merge text ---\n${mergeText}`);
console.log(`\n--- repack text ---\n${repackText}`);
process.exit(fail === 0 ? 0 : 1);
