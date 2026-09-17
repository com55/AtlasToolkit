// tests/python/gen_js_reference_mesh_mask.mjs
//
// Generates the committed JS-oracle fixture for Stage 6's
// rasterize_mesh_mask parity tests, by running the REAL
// www/js/region-mesh-mask.js's rasterizeMeshMask in real Chromium via
// playwright-core. Pixel work (not pure geometry/decisions), so this
// cannot use the plain-Node FakeCanvas oracles Stages 4-5 relied on --
// FakeCanvas never renders anything.
//
// Run manually whenever this fixture needs regenerating:
//
//   node tests/python/gen_js_reference_mesh_mask.mjs
//
// Self-skips (exit 0, fixture left untouched) if playwright-core can't be
// resolved -- same convention as gen_js_reference_raster.mjs.
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');
const WWW = path.join(REPO, 'www');
const OUT = path.join(HERE, 'fixtures', 'mesh_mask_js_oracle.json');

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

const MIME = { '.js': 'text/javascript', '.json': 'application/json', '.html': 'text/html' };
const server = http.createServer((req, res) => {
  const p = decodeURIComponent(req.url.split('?')[0]);
  const filePath = path.join(WWW, p === '/' ? '/index.html' : p);
  if (!filePath.startsWith(WWW) || !fs.existsSync(filePath)) { res.statusCode = 404; return res.end('nf'); }
  res.setHeader('content-type', MIME[path.extname(filePath)] || 'text/plain');
  res.end(fs.readFileSync(filePath));
});
await new Promise((r) => server.listen(0, r));
const ROOT = `http://127.0.0.1:${server.address().port}/`;

const meshFixture = JSON.parse(fs.readFileSync(path.join(REPO, 'tests', 'fixtures', 'mesh-sample.json'), 'utf8'));

const browser = await chromium.launch();
const page = await browser.newPage();
await page.goto(ROOT);

const oracle = await page.evaluate(async (sample) => {
  const { rasterizeMeshMask } = await import('./js/region-mesh-mask.js');

  function toAlphaArray(canvas) {
    const ctx = canvas.getContext('2d');
    const { data, width, height } = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const alpha = [];
    for (let i = 3; i < data.length; i += 4) alpha.push(data[i]);
    return { w: width, h: height, alpha };
  }

  const cases = {};

  // Real mesh, square canvas -- baseline real-geometry case.
  cases.mesh_sample = toAlphaArray(rasterizeMeshMask(sample.SAMPLE_1.uvs, sample.SAMPLE_1.triangles, 200, 200));

  // Single triangle, no cancellation -- simplest possible non-trivial case.
  cases.single_triangle = toAlphaArray(rasterizeMeshMask([0, 0, 1, 0, 0, 1], [0, 1, 2], 50, 50));

  // Two triangles covering the IDENTICAL footprint with OPPOSITE winding
  // order -- must fully cancel under nonzero-winding fill. This is the
  // exact case the poc/pyodide-pillow spike (commit 3b30de0) validated:
  // Canvas fill('nonzero') produces 0 opaque pixels here.
  cases.winding_cancel = toAlphaArray(rasterizeMeshMask(
    [0, 0, 1, 0, 0, 1], [0, 1, 2, 2, 1, 0], 50, 50,
  ));

  // Non-square canvas (width != height) with an asymmetric triangle whose
  // footprint is NOT symmetric under a width/height swap -- catches a
  // transposed-axis regression the way this project's CLAUDE.md warns
  // square-only fixtures cannot (the documented scale_x/scale_y swap bug
  // from Stage 4). Triangle occupies u in [0.1, 0.3], v in [0.6, 0.9] --
  // clearly off-center on both axes.
  cases.non_square_axis_check = toAlphaArray(rasterizeMeshMask(
    [0.1, 0.6, 0.3, 0.6, 0.1, 0.9], [0, 1, 2], 300, 100,
  ));

  return cases;
}, meshFixture);

fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, JSON.stringify(oracle));
console.log('Wrote', OUT, 'with', Object.keys(oracle).length, 'cases.');
for (const [name, c] of Object.entries(oracle)) {
  const opaque = c.alpha.filter((a) => a > 0).length;
  console.log(`  ${name}: ${opaque}/${c.alpha.length} opaque`);
}

await browser.close();
server.close();
