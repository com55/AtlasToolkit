// tests/python/gen_js_reference_footprint_mesh.mjs
//
// Generates the committed JS-oracle fixture for Stage 6's
// footprint_for_canonical mesh-branch parity tests, by running the REAL
// repack-nest.js's footprintForCanonical (with its real injected
// dependencies from atlas-modifier.js) in real Chromium via
// playwright-core. Pixel work -- see gen_js_reference_mesh_mask.mjs for
// why this can't be a plain-Node FakeCanvas oracle.
//
// Run manually whenever this fixture needs regenerating:
//
//   node tests/python/gen_js_reference_footprint_mesh.mjs
//
// Self-skips (exit 0) if playwright-core can't be resolved.
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');
const WWW = path.join(REPO, 'www');
const OUT = path.join(HERE, 'fixtures', 'footprint_mesh_js_oracle.json');

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
  console.log('SKIP: playwright-core not found. Fixture left unchanged.');
  process.exit(0);
}

const MIME = { '.js': 'text/javascript', '.html': 'text/html' };
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

const oracle = await page.evaluate(async () => {
  const { footprintForCanonical } = await import('./js/repack-nest.js');
  const { _combineMeshGeometry, _groupNamesBySpriteIdentity, maskCropRectForOffsets } =
    await import('./js/atlas-modifier.js');

  function makeCanvas(w, h) {
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    return c;
  }
  function toAlphaArray(mask, w, h) {
    // footprintForCanonical returns a Uint8Array mask directly (not a
    // canvas) -- just copy it to a plain array for JSON.
    return { w, h, mask: Array.from(mask) };
  }

  // Both AXIS-ALIGNED (two triangles tiling a rectangle, same construction
  // as engine/modifier.py's mask_raw_sprite tests' LEFT_HALF_RECT) --
  // deliberately NOT a diagonal-edged triangle. footprintForCanonical's
  // pixel content here gets compared byte-for-byte against this real
  // Chromium output; a diagonal edge falls across partially-covered
  // pixels that Canvas antialiases (alpha in (0,255), counted "in" by
  // thresholdAlpha) but a hard-edge Pillow rasterizer never produces --
  // confirmed empirically during this plan's scrutinize review: TRI_A's
  // diagonal disagreed on 10/400 pixels against a correct hard-edge
  // implementation of the exact algorithm this stage specifies. An
  // axis-aligned rectangle split by an INTERIOR diagonal (shared between
  // its two triangles, same winding) has no such edge in the final
  // unioned mask -- only its four straight sides are boundaries, and
  // those land exactly on integer pixel columns/rows for the even
  // canvas dimensions every case below uses, so Canvas produces a clean
  // hard edge with no antialiasing to disagree about.
  const RECT_A = { uvs: [0, 0, 0.5, 0, 0, 1, 0.5, 1], triangles: [0, 1, 2, 1, 3, 2] }; // opaque for u < 0.5 (left half)
  const RECT_B = { uvs: [0, 0.5, 1, 0.5, 0, 1, 1, 1], triangles: [0, 1, 2, 1, 3, 2] }; // opaque for v >= 0.5 (bottom half)

  const cases = {};

  // Case 1: single sprite, mesh geometry, NO offsets -- straight
  // rasterize+threshold, no crop-back.
  {
    const sprites = { a: makeCanvas(20, 20) };
    const regions = { a: { offsets: null } };
    const meshLookupFn = (name) => (name === 'a' ? RECT_A : null);
    const mask = footprintForCanonical(['a'], {
      sprites, moddedSprites: null, addedSprites: null, regions, meshLookupFn,
      combineMeshGeometry: _combineMeshGeometry,
      groupNamesBySpriteIdentity: _groupNamesBySpriteIdentity,
      maskCropRectForOffsets,
    });
    cases.single_no_offsets = toAlphaArray(mask, 20, 20);
  }

  // Case 2: single sprite, mesh geometry, WITH offsets -- exercises the
  // crop-back path (rasterize at full orig size, then crop to the
  // sprite's own sub-rect).
  {
    const sprites = { a: makeCanvas(10, 8) };
    // offsets = [offX, offY, origW, origH]
    const offsets = [3, 2, 20, 16];
    const regions = { a: { offsets } };
    const meshLookupFn = (name) => (name === 'a' ? RECT_B : null);
    const mask = footprintForCanonical(['a'], {
      sprites, moddedSprites: null, addedSprites: null, regions, meshLookupFn,
      combineMeshGeometry: _combineMeshGeometry,
      groupNamesBySpriteIdentity: _groupNamesBySpriteIdentity,
      maskCropRectForOffsets,
    });
    cases.single_with_offsets = toAlphaArray(mask, 10, 8);
  }

  // Case 3: shared-canvas-mod group -- two names point at the SAME
  // modded canvas object, must get ONE combined mask applied to both.
  {
    const sharedCanvas = makeCanvas(20, 20);
    const sprites = { a: sharedCanvas, b: sharedCanvas };
    const moddedSprites = { a: sharedCanvas, b: sharedCanvas };
    const regions = { a: { offsets: null }, b: { offsets: null } };
    const meshLookupFn = (name) => (name === 'a' ? RECT_A : name === 'b' ? RECT_B : null);
    const mask = footprintForCanonical(['a', 'b'], {
      sprites, moddedSprites, addedSprites: null, regions, meshLookupFn,
      combineMeshGeometry: _combineMeshGeometry,
      groupNamesBySpriteIdentity: _groupNamesBySpriteIdentity,
      maskCropRectForOffsets,
    });
    cases.shared_canvas_mod_group = toAlphaArray(mask, 20, 20);
  }

  // Case 4: dimension-incompatible dedup member -- 'b' has a different
  // size than the canonical 'a', must be excluded from the footprint
  // entirely rather than corrupting it.
  {
    const sprites = { a: makeCanvas(20, 20), b: makeCanvas(10, 10) };
    const regions = { a: { offsets: null }, b: { offsets: null } };
    const meshLookupFn = (name) => (name === 'a' ? RECT_A : RECT_B);
    const mask = footprintForCanonical(['a', 'b'], {
      sprites, moddedSprites: null, addedSprites: null, regions, meshLookupFn,
      combineMeshGeometry: _combineMeshGeometry,
      groupNamesBySpriteIdentity: _groupNamesBySpriteIdentity,
      maskCropRectForOffsets,
    });
    cases.dimension_incompatible_member_excluded = toAlphaArray(mask, 20, 20);
  }

  // Case 5: one member of the dedup group has NO mesh data -- that
  // member falls back to a solid mask, unioned with the others (never
  // makes the whole group bail -- that's only combineMeshGeometry's rule
  // for a SHARED-CANVAS group, not this per-name path).
  {
    const sprites = { a: makeCanvas(20, 20), b: makeCanvas(20, 20) };
    const regions = { a: { offsets: null }, b: { offsets: null } };
    const meshLookupFn = (name) => (name === 'a' ? RECT_A : null);
    const mask = footprintForCanonical(['a', 'b'], {
      sprites, moddedSprites: null, addedSprites: null, regions, meshLookupFn,
      combineMeshGeometry: _combineMeshGeometry,
      groupNamesBySpriteIdentity: _groupNamesBySpriteIdentity,
      maskCropRectForOffsets,
    });
    cases.member_without_mesh_falls_back_solid = toAlphaArray(mask, 20, 20);
  }

  return cases;
});

fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(oracle));
console.log('Wrote', OUT, 'with', Object.keys(oracle).length, 'cases.');

await browser.close();
server.close();
