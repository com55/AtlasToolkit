// tests/python/gen_js_reference_raster.mjs
//
// Generates the committed JS-oracle fixture for Stage 3's crop_rotate/
// rotate_image parity tests, by running the REAL core-region-ops.js
// cropAndRotate (and atlas-modifier.js's _rotate90CCW/_rotate90CW/
// _rotate180 whole-canvas helpers, which rotate_image's docstring says
// it's equivalent to) in real Chromium via playwright-core, against the
// real committed tests/browser/fixtures/tie-rounding/fixture-tie.png
// (21x21 RGBA -- the same fixture the poc/pyodide-pillow PoC used for
// this exact primitive).
//
// Run manually whenever this fixture needs regenerating (it should not
// need to change once committed -- core-region-ops.js is pinned as the
// oracle until Phase 3 fully replaces it, per the architecture spec §7):
//
//   node tests/python/gen_js_reference_raster.mjs
//
// Self-skips (exit 0, fixture left untouched) if playwright-core can't be
// resolved -- same convention as every file in tests/browser/.
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');
const WWW = path.join(REPO, 'www');
const OUT = path.join(HERE, 'fixtures', 'raster_js_oracle.json');

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
  console.log('SKIP: playwright-core not found (set $PLAYWRIGHT_CORE to its index.js to run). Fixture left unchanged.');
  process.exit(0);
}

const MIME = { '.js': 'text/javascript', '.png': 'image/png', '.html': 'text/html' };
const server = http.createServer((req, res) => {
  const p = decodeURIComponent(req.url.split('?')[0]);
  const filePath = path.join(WWW, p === '/' ? '/index.html' : p);
  if (!filePath.startsWith(WWW) || !fs.existsSync(filePath)) { res.statusCode = 404; return res.end('nf'); }
  res.setHeader('content-type', MIME[path.extname(filePath)] || 'text/plain');
  res.end(fs.readFileSync(filePath));
});
await new Promise((r) => server.listen(0, r));
const ROOT = `http://127.0.0.1:${server.address().port}/`;

const browser = await chromium.launch();
const page = await browser.newPage();
await page.goto(ROOT);

// The fixture PNG lives under tests/browser/fixtures/, outside www/'s
// served root -- hand it to the page as a data: URL instead of trying to
// serve a second directory tree.
const fixturePath = path.join(REPO, 'tests', 'browser', 'fixtures', 'tie-rounding', 'fixture-tie.png');
const fixtureB64 = fs.readFileSync(fixturePath).toString('base64');

const oracle = await page.evaluate(async (dataUrl) => {
  const { cropAndRotate } = await import('./js/core-region-ops.js');

  function loadImage(src) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = reject;
      img.src = src;
    });
  }

  function canvasToRGBA(canvas) {
    const ctx = canvas.getContext('2d');
    const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
    return { w: canvas.width, h: canvas.height, rgba: Array.from(data) };
  }

  const img = await loadImage(dataUrl);
  const cases = {};

  // crop_rotate cases: whole 21x21 fixture, each of the four stored
  // rotations, mirroring the PoC's own crop_rotate_meta.json case
  // (rotate=90) plus the three others for full coverage.
  for (const rotate of [0, 90, 180, 270]) {
    const w = 21, h = 21; // fixture-tie.png is square, so w/h are the same
    const out = cropAndRotate(img, 0, 0, w, h, rotate);
    cases[`crop_rotate_${rotate}`] = canvasToRGBA(out);
  }

  // Non-square SUB-RECT crop, same four rotations -- the whole-image cases
  // above are square (w===h always), so they can never actually exercise
  // cropAndRotate's isSwapped dimension-swap branch (cropW/cropH would be
  // identical either way). A regression that dropped the swap would pass
  // every case above undetected -- caught by scrutinize review round 1 on
  // this plan, same class of "opaque-only discriminator" trap this
  // project's CLAUDE.md already documents for mesh masking. x=3,y=2,w=10,
  // h=15 is an arbitrary non-square rect that stays fully inside the
  // 21x21 fixture for every rotate value (max extent needed is
  // x+max(w,h)=3+15=18, y+max(w,h)=2+15=17, both <21).
  for (const rotate of [0, 90, 180, 270]) {
    const out = cropAndRotate(img, 3, 2, 10, 15, rotate);
    cases[`crop_rotate_subrect_${rotate}`] = canvasToRGBA(out);
  }

  // rotate_image whole-canvas cases, matching atlas-modifier.js's
  // _rotate90CCW/_rotate90CW/_rotate180 (atlas-modifier.js:1002-1023) --
  // each built purely via cropAndRotate on the full image, which is
  // exactly rotate_image's own documented derivation (base.py). base.py's
  // rotate_image(handle, degrees) passes `degrees` straight through as
  // crop_rotate's `rotate` argument, so rotate_image(h, 90) produces the
  // SAME output as cropAndRotate(...,90) -- i.e. _rotate90CW, NOT
  // _rotate90CCW. (Scrutinize review round 1 on this plan caught an
  // earlier draft of this generator with these two swapped, which would
  // have made this exact test fail against an implementation that
  // correctly followed base.py's own documented contract -- fixed here by
  // aligning the oracle's case labels to the implementation/docstring,
  // not the other way around.)
  const rotate90CCW = cropAndRotate(img, 0, 0, img.height, img.width, 270);
  const rotate90CW = cropAndRotate(img, 0, 0, img.height, img.width, 90);
  const rotate180 = cropAndRotate(img, 0, 0, img.width, img.height, 180);
  cases['rotate_image_90'] = canvasToRGBA(rotate90CW);   // degrees=90  -> crop_rotate(...,90)
  cases['rotate_image_270'] = canvasToRGBA(rotate90CCW); // degrees=270 -> crop_rotate(...,270)
  cases['rotate_image_180'] = canvasToRGBA(rotate180);

  // paste_partial_over_partial: real Canvas Porter-Duff "source-over"
  // compositing (ctx.drawImage's default globalCompositeOperation) for the
  // ONE alpha regime Task 3's scrutinize review found completely
  // uncovered -- BOTH dest and src alpha strictly between 0 and 255,
  // the only case where the unpremultiply division in "over" actually
  // executes and a Pillow-vs-Canvas fixed-point rounding difference could
  // surface. Uses putImageData (not fillStyle, which parses through a
  // CSS color string and could round differently) to set EXACT straight-
  // alpha pixel values, matching Pillow's own straight-alpha
  // representation bit-for-bit on the input side -- so any difference in
  // the OUTPUT is attributable only to the compositing math itself, not
  // to input rounding. dest=(255,0,0,128) (opaque-ish red),
  // src=(0,0,255,128) (opaque-ish blue) -- the exact values scrutinize's
  // own hand-derivation used.
  function makeSolidCanvas(w, h, r, g, b, a) {
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const ctx = c.getContext('2d');
    const imgData = ctx.createImageData(w, h);
    for (let i = 0; i < imgData.data.length; i += 4) {
      imgData.data[i] = r; imgData.data[i + 1] = g; imgData.data[i + 2] = b; imgData.data[i + 3] = a;
    }
    ctx.putImageData(imgData, 0, 0);
    return c;
  }
  const pasteDest = makeSolidCanvas(1, 1, 255, 0, 0, 128);
  const pasteSrc = makeSolidCanvas(1, 1, 0, 0, 255, 128);
  pasteDest.getContext('2d').drawImage(pasteSrc, 0, 0); // default source-over
  cases['paste_partial_over_partial'] = canvasToRGBA(pasteDest);

  return cases;
}, `data:image/png;base64,${fixtureB64}`);

fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(oracle));
console.log('Wrote', OUT, 'with', Object.keys(oracle).length, 'cases.');

await browser.close();
server.close();
