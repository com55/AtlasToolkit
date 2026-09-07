/**
 * Browser verification for the mesh-masked repack feature (Tasks 1-6 of the
 * mesh-masked-repack-source plan), exercised end-to-end through the real
 * AtlasProcessor/AtlasModifier repack pipeline in headless Chromium.
 *
 * Loads the REAL AtlasProcessor + AtlasModifier (which need canvas + Image
 * loading, i.e. a DOM) and asserts:
 *   - repack masking leaves packed-canvas dimensions unchanged, and masks
 *     outside-triangle pixels to transparent only when the repack toggle is on
 *   - a shared-canvas mod unions both regions' meshes (keeps pixels inside
 *     either, masks the gap between them)
 *   - a mismatched-aspect mod image still gets masked (accepted edge case)
 *   - toggling the repack mask off then on re-derives fresh each call (never
 *     baked into the shared source canvas)
 *
 * This is intentionally NOT part of `node --test` (it needs a browser +
 * playwright-core, which are not repo dependencies). Run it directly:
 *
 *   node tests/browser/verify-mesh-repack.mjs
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
import { AtlasModifier } from '/www/js/atlas-modifier.js';

function solidDataUrl(w, h, color) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const ctx = c.getContext('2d');
  ctx.fillStyle = color;
  ctx.fillRect(0, 0, w, h);
  return c.toDataURL();
}
function alpha(canvas, x, y) { return canvas.getContext('2d').getImageData(x, y, 1, 1).data[3]; }
// Top-left-half-triangle mask, same shape used by the existing mesh-mask browser tests.
const HALF_TRIANGLE = { uvs: [0, 0, 1, 0, 0, 1], triangles: [0, 1, 2] };
// Small triangle strictly inside the bottom-right corner (u+v >= 1.7) --
// deliberately NOT the complement of HALF_TRIANGLE: together they leave a
// real gap in the middle-right area, which the union test below depends on.
const CORNER_TRIANGLE = { uvs: [1, 1, 0.7, 1, 1, 0.7], triangles: [0, 1, 2] };

window.runCase = async (name) => {
  const results = [];
  const check = (label, cond, detail) => results.push({ label, ok: !!cond, detail });

  if (name === 'repack-masks-pristine-sprite-dimensions-unchanged') {
    const ATLAS_TEXT = 'page1.png\\nsize: 20,20\\nleaf\\nbounds: 0, 0, 20, 20\\n';
    const proc = new AtlasProcessor(ATLAS_TEXT);
    await proc.loadImages({ 'page1.png': solidDataUrl(20, 20, '#f00') });
    const lookup = new Map([['leaf', HALF_TRIANGLE]]);

    // OFF: no masking at all (repackEnabled = false).
    proc.setMeshMaskData(lookup, true, false);
    const modifier = new AtlasModifier(ATLAS_TEXT, 'a.atlas', proc.getPageImage('page1.png'));
    const meshLookupFn = (n) => proc.getRepackMeshGeometry(n);
    const repackedOff = await modifier.repackWithModdedSprites({}, null, meshLookupFn);
    const offOutside = alpha(repackedOff.canvas, 18, 18);

    // ON: masking active (repackEnabled = true).
    proc.setMeshMaskData(lookup, true, true);
    const repackedOn = await modifier.repackWithModdedSprites({}, null, meshLookupFn);
    const onInside = alpha(repackedOn.canvas, 2, 2);
    const onOutside = alpha(repackedOn.canvas, 18, 18);

    check('OFF: packed canvas dimensions unchanged (20x20)', repackedOff.canvas.width === 20 && repackedOff.canvas.height === 20, \`\${repackedOff.canvas.width}x\${repackedOff.canvas.height}\`);
    check('ON: packed canvas dimensions unchanged (20x20)', repackedOn.canvas.width === 20 && repackedOn.canvas.height === 20, \`\${repackedOn.canvas.width}x\${repackedOn.canvas.height}\`);
    check('OFF: outside-triangle pixel stays opaque (no masking)', offOutside === 255, 'offOutside=' + offOutside);
    check('ON: inside-triangle pixel stays opaque', onInside > 200, 'onInside=' + onInside);
    check('ON: outside-triangle pixel is masked to transparent (alpha === 0)', onOutside === 0, 'onOutside=' + onOutside);
  } else if (name === 'shared-canvas-mod-unions-both-regions-meshes') {
    const ATLAS_TEXT = 'page1.png\\nsize: 20,20\\na\\nbounds: 0, 0, 10, 20\\noffsets: 0, 0, 20, 20\\nb\\nbounds: 10, 0, 10, 20\\noffsets: 10, 0, 20, 20\\n';
    const proc = new AtlasProcessor(ATLAS_TEXT);
    await proc.loadImages({ 'page1.png': solidDataUrl(20, 20, '#0f0') });
    // region a covers the top-left half, region b covers only a small
    // bottom-right corner -- deliberately not the complement of the other
    // one, so the union must keep pixels from EITHER while a real gap
    // between them still gets masked away.
    const lookup = new Map([['a', HALF_TRIANGLE], ['b', CORNER_TRIANGLE]]);
    proc.setMeshMaskData(lookup, true, true);
    const meshLookupFn = (n) => proc.getRepackMeshGeometry(n);

    const modImage = solidDataUrl(20, 20, '#00f');
    const modCanvas = document.createElement('canvas');
    modCanvas.width = 20; modCanvas.height = 20;
    modCanvas.getContext('2d').drawImage(await new Promise((res) => { const img = new Image(); img.onload = () => res(img); img.src = modImage; }), 0, 0);
    const moddedSprites = { a: modCanvas, b: modCanvas }; // literal same object, mirrors a real shared-canvas mod

    const modifier = new AtlasModifier(ATLAS_TEXT, 'a.atlas', proc.getPageImage('page1.png'));
    const repacked = await modifier.repackWithModdedSprites(moddedSprites, new Set(['a', 'b']), meshLookupFn);

    const topLeft = alpha(repacked.canvas, 2, 2);      // inside HALF_TRIANGLE only
    const bottomRight = alpha(repacked.canvas, 19, 19); // inside CORNER_TRIANGLE (u+v=1.9, past its 1.7 boundary)
    const midRightGap = alpha(repacked.canvas, 18, 10); // outside BOTH: u+v=1.4 -- above the HALF_TRIANGLE <=1.0 cutoff, below the CORNER_TRIANGLE >=1.7 cutoff

    check('top-left kept (inside triangle a)', topLeft > 200, 'topLeft=' + topLeft);
    check('bottom-right kept (inside triangle b)', bottomRight > 200, 'bottomRight=' + bottomRight);
    check('gap between the two triangles masked away (outside both)', midRightGap === 0, 'midRightGap=' + midRightGap);
  } else if (name === 'mismatched-aspect-mod-image-still-masks-per-accepted-design') {
    // Documents the accepted edge case (spec §3, round 3): a mod image
    // whose shape does not match the authored offsets aspect of the
    // region still gets masked, stretched to the mismatched canvas -- this
    // assertion exists so a future change to this behavior is a
    // deliberate, visible spec revision, not a silent regression.
    const ATLAS_TEXT = 'page1.png\\nsize: 20,20\\ntall\\nbounds: 0, 0, 10, 20\\noffsets: 0, 0, 10, 20\\n';
    const proc = new AtlasProcessor(ATLAS_TEXT);
    await proc.loadImages({ 'page1.png': solidDataUrl(20, 20, '#fa0') });
    const lookup = new Map([['tall', HALF_TRIANGLE]]); // authored for a 10x20 (tall) canvas
    proc.setMeshMaskData(lookup, true, true);
    const meshLookupFn = (n) => proc.getRepackMeshGeometry(n);

    // Mod image is 30x8 (wide) -- not an exact match, not a proportional
    // scale of 10x20 -- resolveModCanvas adopts 30x8 as the new canvas.
    const wideModUrl = solidDataUrl(30, 8, '#0af');
    const wideModImg = await new Promise((res) => { const img = new Image(); img.onload = () => res(img); img.src = wideModUrl; });
    const wideModCanvas = document.createElement('canvas');
    wideModCanvas.width = 30; wideModCanvas.height = 8;
    wideModCanvas.getContext('2d').drawImage(wideModImg, 0, 0);

    const modifier = new AtlasModifier(ATLAS_TEXT, 'a.atlas', proc.getPageImage('page1.png'));
    const repacked = await modifier.repackWithModdedSprites({ tall: wideModCanvas }, new Set(['tall']), meshLookupFn);

    // The packed canvas is the 30x8 mod rounded up to a multiple of 4 (the
    // engine's documented mult-of-4 packed-canvas invariant, applied by
    // _shelfPack to every repack output regardless of masking): 30 -> 32, 8 -> 8.
    // The assertion's point is that masking itself changed no dimensions.
    check('packed canvas keeps the 30x8 mod shape, rounded to mult-of-4 (32x8); masking changed no dimensions', repacked.canvas.width === 32 && repacked.canvas.height === 8, \`\${repacked.canvas.width}x\${repacked.canvas.height}\`);
    const farCorner = alpha(repacked.canvas, 29, 7); // outside the stretched HALF_TRIANGLE shape
    check('masking still runs on the mismatched-aspect canvas (some pixel gets masked)', farCorner === 0, 'farCorner=' + farCorner);
  } else if (name === 'toggle-off-then-on-restores-then-reapplies-live') {
    // The exact scenario the round 1 blocker was about: masking must be
    // re-derived fresh on every call, never baked in once.
    const ATLAS_TEXT = 'page1.png\\nsize: 20,20\\nleaf\\nbounds: 0, 0, 20, 20\\n';
    const proc = new AtlasProcessor(ATLAS_TEXT);
    await proc.loadImages({ 'page1.png': solidDataUrl(20, 20, '#f00') });
    const lookup = new Map([['leaf', HALF_TRIANGLE]]);
    const modifier = new AtlasModifier(ATLAS_TEXT, 'a.atlas', proc.getPageImage('page1.png'));

    const modImg = await new Promise((res) => { const img = new Image(); img.onload = () => res(img); img.src = solidDataUrl(20, 20, '#00f'); });
    const modCanvas = document.createElement('canvas');
    modCanvas.width = 20; modCanvas.height = 20;
    modCanvas.getContext('2d').drawImage(modImg, 0, 0);
    const moddedSprites = { leaf: modCanvas };

    proc.setMeshMaskData(lookup, true, true); // ON
    const meshLookupFn = (n) => proc.getRepackMeshGeometry(n);
    const onResult = await modifier.repackWithModdedSprites(moddedSprites, null, meshLookupFn);
    const onOutside = alpha(onResult.canvas, 18, 18);

    proc.setMeshMaskData(lookup, true, false); // OFF -- re-derive from the SAME unmasked moddedSprites.leaf
    const offResult = await modifier.repackWithModdedSprites(moddedSprites, null, meshLookupFn);
    const offOutside = alpha(offResult.canvas, 18, 18);

    proc.setMeshMaskData(lookup, true, true); // ON again
    const onAgainResult = await modifier.repackWithModdedSprites(moddedSprites, null, meshLookupFn);
    const onAgainOutside = alpha(onAgainResult.canvas, 18, 18);

    check('ON: outside-triangle pixel masked away', onOutside === 0, 'onOutside=' + onOutside);
    check('OFF (after ON): masked-away pixel is back (not baked in)', offOutside === 255, 'offOutside=' + offOutside);
    check('ON again: newly masks the still-unmasked source', onAgainOutside === 0, 'onAgainOutside=' + onAgainOutside);
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

const cases = ['repack-masks-pristine-sprite-dimensions-unchanged', 'shared-canvas-mod-unions-both-regions-meshes', 'mismatched-aspect-mod-image-still-masks-per-accepted-design', 'toggle-off-then-on-restores-then-reapplies-live'];
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
