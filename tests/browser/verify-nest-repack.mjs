/**
 * Browser verification for the "Nest Regions" repack feature
 * (mesh-silhouette-nesting plan). Exercises the real footprintForCanonical
 * (Canvas-touching, via rasterizeMeshMask) and, from Task 6 onward, the
 * full repack pipeline with Nest Regions enabled, in headless Chromium.
 *
 * This is intentionally NOT part of `node --test` (needs a browser +
 * playwright-core). Run directly:
 *
 *   node tests/browser/verify-nest-repack.mjs
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
  console.log('SKIP: playwright-core not found (set $PLAYWRIGHT_CORE to its index.js to run).');
  process.exit(0);
}

const HARNESS = `<!doctype html><meta charset=utf8><body><script type="module">
import { footprintForCanonical } from '/www/js/repack-nest.js';
import { _combineMeshGeometry, _groupNamesBySpriteIdentity, maskCropRectForOffsets } from '/www/js/atlas-modifier.js';
import { AtlasProcessor } from '/www/js/atlas-extracter.js';
import { AtlasModifier } from '/www/js/atlas-modifier.js';

function solidCanvas(w, h) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  return c;
}
function maskToSet(mask, w, h) {
  const set = new Set();
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (mask[y * w + x]) set.add(x + ',' + y);
  return set;
}
// Small triangle covering only the top-left quadrant's own corner.
const CORNER = { uvs: [0, 0, 0.5, 0, 0, 0.5], triangles: [0, 1, 2] };

window.runCase = async (name) => {
  const results = [];
  const check = (label, cond, detail) => results.push({ label, ok: !!cond, detail });

  if (name === 'dimension-mismatch-member-skipped') {
    // Two names in one dedup group; 'b' has different actual sprite
    // dimensions than the canonical 'a' -- must be skipped, not crash or
    // produce a wrongly-shaped mask.
    const sprites = { a: solidCanvas(10, 10), b: solidCanvas(6, 6) };
    const regions = { a: { offsets: null }, b: { offsets: null } };
    const lookup = new Map([['a', CORNER], ['b', CORNER]]);
    const meshLookupFn = (n) => lookup.get(n) ?? null;
    let threw = false;
    let mask;
    try {
      mask = footprintForCanonical(['a', 'b'], {
        sprites, moddedSprites: {}, addedSprites: {}, regions, meshLookupFn,
        combineMeshGeometry: _combineMeshGeometry,
        groupNamesBySpriteIdentity: _groupNamesBySpriteIdentity,
        maskCropRectForOffsets,
      });
    } catch (e) { threw = true; }
    check('does not throw', !threw);
    check('mask length matches canonical (10x10=100), not the mismatched member', mask && mask.length === 100, mask && mask.length);
  } else if (name === 'null-meshLookupFn-returns-solid-mask-no-throw') {
    const sprites = { a: solidCanvas(4, 5) };
    const regions = { a: { offsets: null } };
    let threw = false;
    let mask;
    try {
      mask = footprintForCanonical(['a'], {
        sprites, moddedSprites: {}, addedSprites: {}, regions, meshLookupFn: null,
        combineMeshGeometry: _combineMeshGeometry,
        groupNamesBySpriteIdentity: _groupNamesBySpriteIdentity,
        maskCropRectForOffsets,
      });
    } catch (e) { threw = true; }
    check('does not throw with a null meshLookupFn', !threw);
    check('returns a fully-solid mask (Gap A fallback)', mask && mask.length === 20 && Array.from(mask).every(v => v === 1), mask && Array.from(mask || []).join(''));
  } else if (name === 'toggle-off-shelfpack-output-is-correct') {
    // Distinct, FILLED, differently-shaped sprites -- 'tall' is taller than
    // wide (exercises _shelfPack's own rotation logic). Verifies the
    // toggle-off path (both nestOptions omitted and explicitly
    // {enabled:false} -- both take the SAME _shelfPack branch, so this is
    // NOT a nest-vs-shelf comparison) produces objectively correct output:
    // real pixel content lands at the reported bounds. This is what proves
    // adding the nestOptions parameter/branch to _packAndEmit did not
    // silently corrupt the untouched _shelfPack path -- the previous version
    // of this test compared two branches that are always identical to each
    // other regardless of what the code does, so it could never fail.
    const ATLAS_TEXT = 'page1.png\\nsize: 20,20\\ntall\\nbounds: 0, 0, 6, 10\\nwide\\nbounds: 6, 0, 8, 8\\n';
    const proc = new AtlasProcessor(ATLAS_TEXT);
    const page = document.createElement('canvas');
    page.width = 20; page.height = 20;
    const pctx = page.getContext('2d');
    pctx.fillStyle = '#f00'; pctx.fillRect(0, 0, 6, 10);
    pctx.fillStyle = '#00f'; pctx.fillRect(6, 0, 8, 8);
    await proc.loadImages({ 'page1.png': page.toDataURL() });
    const img = proc.getPageImage('page1.png');
    const modifier = new AtlasModifier(ATLAS_TEXT, 'a.atlas', img);
    const r1 = await modifier.repackWithModdedSprites({}, null, null, null);
    const r2 = await modifier.repackWithModdedSprites({}, null, null, { enabled: false, gapDistance: 4 });
    check('nestOptions omitted vs. explicitly disabled produce identical canvas size',
      r1.canvas.width === r2.canvas.width && r1.canvas.height === r2.canvas.height,
      r1.canvas.width + 'x' + r1.canvas.height + ' vs ' + r2.canvas.width + 'x' + r2.canvas.height);
    check('same atlas text either way', r1.atlasText === r2.atlasText);
    for (const r of [r1, r2]) {
      const [tx, ty] = r.regionBounds.tall;
      const [wx, wy] = r.regionBounds.wide;
      const px = (x, y) => { const d = r.canvas.getContext('2d').getImageData(x, y, 1, 1).data; return [d[0], d[1], d[2], d[3]]; };
      const tallPixel = px(tx + 1, ty + 1);
      const widePixel = px(wx + 1, wy + 1);
      check('tall region pixel at its reported bounds is red',
        tallPixel[0] > 200 && tallPixel[2] < 50, 'rgba=' + tallPixel.join(','));
      check('wide region pixel at its reported bounds is blue',
        widePixel[2] > 200 && widePixel[0] < 50, 'rgba=' + widePixel.join(','));
    }
  } else if (name === 'toggle-on-no-mesh-uses-gap-a-space') {
    // No mesh anywhere in this fixture: meshLookupFn is null, so
    // footprintForCanonical falls back to a solid mask for every item and the
    // nest path still runs end-to-end through the full pipeline. We can't
    // assert the canvas is SMALLER than shelf packing here — with solid
    // footprints a large item's dilated mask fills the padded grid, so nest
    // packing legitimately grows the canvas to place the small item (nesting's
    // advantage only shows with concave mesh-derived footprints). Instead we
    // assert the nest path is well-formed: both items are placed, the canvas
    // dims are positive multiples of 4, and the two placements don't overlap.
    const ATLAS_TEXT = 'page1.png\\nsize: 40,40\\nbig\\nbounds: 0, 0, 20, 20\\nsmall\\nbounds: 20, 0, 4, 4\\n';
    const proc = new AtlasProcessor(ATLAS_TEXT);
    const canvas = document.createElement('canvas');
    canvas.width = 40; canvas.height = 40;
    const pctx = canvas.getContext('2d');
    pctx.fillStyle = '#0f0'; pctx.fillRect(0, 0, 20, 20);   // 'big': solid green
    pctx.fillStyle = '#ff0'; pctx.fillRect(20, 0, 4, 4);    // 'small': solid yellow
    await proc.loadImages({ 'page1.png': canvas.toDataURL() });
    const img = proc.getPageImage('page1.png');
    const modifier = new AtlasModifier(ATLAS_TEXT, 'a.atlas', img);
    const nestResult = await modifier.repackWithModdedSprites({}, null, null, { enabled: true, gapDistance: 1 });
    const { canvasW, canvasH } = { canvasW: nestResult.canvas.width, canvasH: nestResult.canvas.height };
    check('nest path places the big item',
      Array.isArray(nestResult.regionBounds.big), 'regionBounds.big=' + JSON.stringify(nestResult.regionBounds.big));
    check('nest path places the small item',
      Array.isArray(nestResult.regionBounds.small), 'regionBounds.small=' + JSON.stringify(nestResult.regionBounds.small));
    check('nest canvas dims are positive multiples of 4',
      canvasW > 0 && canvasH > 0 && canvasW % 4 === 0 && canvasH % 4 === 0, 'canvas=' + canvasW + 'x' + canvasH);
    const effBox = (b) => {
      const [x, y, w, h, rot] = b;
      const ew = (rot === 90 || rot === 270) ? h : w;
      const eh = (rot === 90 || rot === 270) ? w : h;
      return { x, y, w: ew, h: eh };
    };
    const bigB = effBox(nestResult.regionBounds.big);
    const smallB = effBox(nestResult.regionBounds.small);
    const overlap = bigB.x < smallB.x + smallB.w && smallB.x < bigB.x + bigB.w
                 && bigB.y < smallB.y + smallB.h && smallB.y < bigB.y + bigB.h;
    check('nest placements do not overlap',
      !overlap, 'big=' + JSON.stringify(bigB) + ' small=' + JSON.stringify(smallB));
    const px = (x, y) => { const d = nestResult.canvas.getContext('2d').getImageData(x, y, 1, 1).data; return [d[0], d[1], d[2], d[3]]; };
    const bigPixel = px(bigB.x + 1, bigB.y + 1);
    const smallPixel = px(smallB.x + 1, smallB.y + 1);
    check('big region pixel at its reported nest-mode bounds is green',
      bigPixel[1] > 200 && bigPixel[0] < 50, 'rgba=' + bigPixel.join(','));
    check('small region pixel at its reported nest-mode bounds is yellow',
      smallPixel[0] > 200 && smallPixel[1] > 200 && smallPixel[2] < 50, 'rgba=' + smallPixel.join(','));
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

const cases = ['dimension-mismatch-member-skipped', 'null-meshLookupFn-returns-solid-mask-no-throw', 'toggle-off-shelfpack-output-is-correct', 'toggle-on-no-mesh-uses-gap-a-space'];
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
    for (const r of results) console.log(`      ${r.ok ? 'ok  ' : 'FAIL'} ${r.label}${r.detail ? ' (' + r.detail + ')' : ''}`);
  }
}

await browser.close();
server.close();
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
