/**
 * Extended reference-oracle parity harness (Task 5, Phase F partial).
 *
 * Loads the REAL browser code (core-region-ops.js, atlas-extracter.js,
 * atlas-document.js, atlas-modifier.js) in headless Chromium and asserts
 * parity against ground_truth_ops.json, generated from the REAL main-branch
 * Python (document.py / region_ops.py / repacker.py / modifier.py) by
 * gen-ground-truth-ops.py -- see that script's header for the pinned oracle
 * commit and methodology.
 *
 * Covers (see ground_truth_ops.json's 4 case groups):
 *   - extractCases:  the two Deliverable-1 fixtures (opaque/transparent-only,
 *     .5-rounding-tie) round-tripped through the real AtlasDocument.parse ->
 *     extractRegionFromPage path, plus a default-offsets (no-padding) case.
 *   - mergeCases:    full-canvas / offset-padded / rotated-placement merge.
 *   - repackCases:   single-page repack dedup, multi-page repack no-dedup.
 *   - realworldCases: extract + multi-page-repack spot-checks against real
 *     Blue Archive sprite atlases in .workspaces/ (tolerance-compared, not
 *     exact -- see gen-ground-truth-ops.py's premultiply-alpha rationale).
 *     SELF-SKIPS (case-by-case, not the whole script) if .workspaces/ isn't
 *     present in this environment, since it's untracked local data.
 *
 * Pixel comparison policy:
 *   - exact:true cases (all synthetic fixtures/scenarios, binary alpha only)
 *     assert byte-for-byte RGBA equality.
 *   - exact:false cases (real-world) assert alpha exactly and RGB within
 *     `tolerance` (default 1) wherever alpha != 0 -- browser getImageData
 *     un-premultiplies alpha internally, so a byte-exact RGB comparison
 *     would false-fail on anti-aliased/semi-transparent source pixels even
 *     when the underlying rotation/crop/paste logic is correct.
 *
 * This is intentionally NOT part of `node --test` (needs a browser +
 * playwright-core, which are not repo dependencies). Run it directly:
 *
 *   node test/browser/verify-ops.mjs
 *
 * playwright-core is located the same way verify-rotation.mjs does (see its
 * header comment); if not found, this script SKIPS (exit 0).
 */
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');

// .workspaces is untracked local data living at the TOP-LEVEL AtlasToolkit
// checkout, not inside a git-worktree -- mirrors gen-ground-truth-ops.py's
// WORKSPACES_CANDIDATES search.
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
  console.log('SKIP: playwright-core not found (set $PLAYWRIGHT_CORE to its index.js to run). '
    + 'Pure-Node algebra is still covered by node --test.');
  process.exit(0);
}

const groundTruthPath = path.join(HERE, 'ground_truth_ops.json');
if (!fs.existsSync(groundTruthPath)) {
  console.log('SKIP: ground_truth_ops.json not found -- run `python3 test/browser/gen-ground-truth-ops.py` first.');
  process.exit(0);
}
const gt = JSON.parse(fs.readFileSync(groundTruthPath, 'utf8'));
console.log(`Loaded ground_truth_ops.json (pinned oracle: main@${gt.pinnedSha})`);

const HARNESS = `<!doctype html><meta charset=utf8><body><script type="module">
import { AtlasModifier, repackMultiPage } from '/www/js/atlas-modifier.js';
import { extractRegionFromPage } from '/www/js/core-region-ops.js';
import { AtlasDocument } from '/www/js/atlas-document.js';

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

async function loadRealImage(url) {
  const img = new Image();
  const p = new Promise((res, rej) => { img.onload = res; img.onerror = rej; });
  img.src = url;
  await p;
  const canvas = document.createElement('canvas');
  canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
  canvas.getContext('2d').drawImage(img, 0, 0);
  return canvas;
}

window.runExtractCase = (cse) => {
  const src = decodeGrid(cse.source);
  const a = cse.args;
  const out = extractRegionFromPage(
    src,
    { x: a.x, y: a.y, w: a.w, h: a.h, rotate: a.rotate, offsets: a.offsets },
    { scaleX: a.scaleX, scaleY: a.scaleY }
  );
  return encodeGrid(out);
};

window.runRealworldExtractCase = async (cse, pngUrl) => {
  const pageImg = await loadRealImage(pngUrl);
  const doc = AtlasDocument.parse(cse.atlasText);
  let region = null;
  for (const p of doc.pages) {
    const found = p.regions.find(r => r.name === cse.regionName);
    if (found) { region = found; break; }
  }
  // Mirror AtlasProcessor.loadImages's real-vs-declared-size scale detection
  // (see gen-ground-truth-ops.py's matching comment for why this matters --
  // skipping it produced hollow/miscomputed cases for NP0229, whose declared
  // page size doesn't match its real PNG dimensions).
  const out = extractRegionFromPage(
    pageImg,
    { x: region.x, y: region.y, w: region.w, h: region.h, rotate: region.rotate, offsets: region.offsets },
    { scaleX: cse.scaleX, scaleY: cse.scaleY }
  );
  return encodeGrid(out);
};

window.runMergeCase = async (cse) => {
  const baseCanvas = decodeGrid(cse.baseImage);
  const modCanvas = decodeGrid(cse.modImage);
  const modifier = new AtlasModifier(cse.atlasText, 'dummy.atlas', baseCanvas);
  const { mergedCanvas, atlasText } = modifier.mergeModImage(modCanvas, cse.selectedRegions);
  return { canvas: encodeGrid(mergedCanvas), atlasText };
};

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
  const urlPath = decodeURIComponent(req.url.split('?')[0]);
  if (urlPath.startsWith('/__workspaces__/')) {
    if (!WORKSPACES_ROOT) { res.statusCode = 404; return res.end('no workspaces'); }
    const rel = urlPath.slice('/__workspaces__/'.length);
    const filePath = path.join(WORKSPACES_ROOT, rel);
    if (!filePath.startsWith(WORKSPACES_ROOT) || !fs.existsSync(filePath)) { res.statusCode = 404; return res.end('nf'); }
    res.setHeader('content-type', 'image/png');
    return res.end(fs.readFileSync(filePath));
  }
  const filePath = path.join(ROOT, urlPath);
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

let pass = 0, fail = 0, skip = 0;

// Premultiply RGB by alpha (0-255 domain, matching what round(RGB*alpha/255)
// a browser's internal premultiply/unpremultiply pipeline effectively does).
// Comparing in THIS space, not raw un-premultiplied RGB, is what the brief's
// "compare using premultiplied alpha values" alternative means: at low alpha
// (e.g. a=17), unpremultiplied RGB has huge quantization error (a ±1 step in
// the premultiplied domain becomes a ±15 step once divided back out), so a
// fixed ±1 raw-RGB tolerance false-fails on correct pixels. Premultiplied
// values are small integers close together regardless of alpha, so a small
// fixed tolerance there is the mathematically appropriate check. Verified
// empirically during harness development: e.g. got=(135,15,15,17) vs
// expected=(136,17,17,17) raw-RGB-mismatches at tolerance=1, but both
// premultiply to the identical (9,1,1) -- confirming it's premultiply/
// unpremultiply round-trip lossiness, not a port bug (see task-5-report.md).
function premultiply(v, a) {
  return Math.round((v * a) / 255);
}

function comparePixels(name, got, expected, exact, tolerance) {
  if (got.w !== expected.w || got.h !== expected.h) {
    return `size mismatch: got ${got.w}x${got.h}, expected ${expected.w}x${expected.h}`;
  }
  const gb = Buffer.from(got.b64, 'base64');
  const eb = Buffer.from(expected.b64, 'base64');
  if (gb.length !== eb.length) return `byte length mismatch: got ${gb.length}, expected ${eb.length}`;
  const tol = tolerance ?? 1;
  for (let i = 0; i < eb.length; i += 4) {
    const [gr, gg, gbl, ga] = [gb[i], gb[i + 1], gb[i + 2], gb[i + 3]];
    const [er, eg, ebl, ea] = [eb[i], eb[i + 1], eb[i + 2], eb[i + 3]];
    if (ga !== ea) return `pixel ${i / 4} alpha mismatch: got ${ga}, expected ${ea}`;
    if (exact) {
      if (gr !== er || gg !== eg || gbl !== ebl) {
        return `pixel ${i / 4} RGB mismatch (exact mode): got (${gr},${gg},${gbl},${ga}), expected (${er},${eg},${ebl},${ea})`;
      }
    } else if (ea !== 0) {
      const gpr = premultiply(gr, ga), gpg = premultiply(gg, ga), gpb = premultiply(gbl, ga);
      const epr = premultiply(er, ea), epg = premultiply(eg, ea), epb = premultiply(ebl, ea);
      if (Math.abs(gpr - epr) > tol || Math.abs(gpg - epg) > tol || Math.abs(gpb - epb) > tol) {
        return `pixel ${i / 4} premultiplied-RGB outside tolerance ${tol}: `
          + `got (${gr},${gg},${gbl},${ga})->premult(${gpr},${gpg},${gpb}), `
          + `expected (${er},${eg},${ebl},${ea})->premult(${epr},${epg},${epb})`;
      }
    }
  }
  return null;
}

function check(name, err) {
  if (!err) { pass++; console.log(`PASS  ${name}`); }
  else { fail++; console.log(`FAIL  ${name}\n      ${err}`); }
}

// ── extractCases ────────────────────────────────────────────────────────
for (const cse of gt.extractCases) {
  const got = await page.evaluate((c) => window.runExtractCase(c), cse);
  check(cse.name, comparePixels(cse.name, got, cse.expected, cse.exact, cse.tolerance));
}

// ── mergeCases ───────────────────────────────────────────────────────────
for (const cse of gt.mergeCases) {
  const { canvas, atlasText } = await page.evaluate((c) => window.runMergeCase(c), cse);
  const pixErr = comparePixels(cse.name, canvas, cse.expectedCanvas, cse.exact, cse.tolerance);
  check(`${cse.name} [pixels]`, pixErr);
  check(`${cse.name} [atlas text]`, atlasText.trim() === cse.expectedAtlasText.trim() ? null
    : `atlas text mismatch:\n--- got ---\n${atlasText}\n--- expected ---\n${cse.expectedAtlasText}`);
}

// ── repackCases ──────────────────────────────────────────────────────────
for (const cse of gt.repackCases) {
  if (cse.op === 'repackSinglePage') {
    const { canvas, atlasText } = await page.evaluate((c) => window.runRepackSingleCase(c), cse);
    check(`${cse.name} [pixels]`, comparePixels(cse.name, canvas, cse.expectedCanvas, cse.exact, cse.tolerance));
    check(`${cse.name} [atlas text]`, atlasText.trim() === cse.expectedAtlasText.trim() ? null
      : `atlas text mismatch:\n--- got ---\n${atlasText}\n--- expected ---\n${cse.expectedAtlasText}`);
    if (cse.assertDedupBoundsEqual) {
      const [a, b] = cse.assertDedupBoundsEqual;
      const reA = new RegExp(`${a}\\s*\\n\\s*bounds:\\s*([\\d, ]+)`);
      const reB = new RegExp(`${b}\\s*\\n\\s*bounds:\\s*([\\d, ]+)`);
      const ma = atlasText.match(reA), mb = atlasText.match(reB);
      check(`${cse.name} [dedup: ${a} bounds === ${b} bounds]`,
        ma && mb && ma[1] === mb[1] ? null : `${a}=${ma && ma[1]} ${b}=${mb && mb[1]}\n${atlasText}`);
    }
  } else if (cse.op === 'repackMultiPage') {
    const { pages, atlasText } = await page.evaluate((c) => window.runRepackMultiCase(c), cse);
    for (let i = 0; i < cse.expectedPages.length; i++) {
      check(`${cse.name} [page ${i} pixels]`, comparePixels(cse.name, pages[i], cse.expectedPages[i], cse.exact, cse.tolerance));
    }
    check(`${cse.name} [atlas text]`, atlasText.trim() === cse.expectedAtlasText.trim() ? null
      : `atlas text mismatch:\n--- got ---\n${atlasText}\n--- expected ---\n${cse.expectedAtlasText}`);
    if (cse.spriteNames.includes('dupeA') && cse.spriteNames.includes('dupeB')) {
      const hasA = new RegExp(`dupeA\\s*\\n\\s*bounds:`).test(atlasText);
      const hasB = new RegExp(`dupeB\\s*\\n\\s*bounds:`).test(atlasText);
      check(`${cse.name} [no-dedup: both dupeA and dupeB present]`, hasA && hasB ? null : `hasA=${hasA} hasB=${hasB}\n${atlasText}`);
    }
  }
}

// ── realworldCases ───────────────────────────────────────────────────────
if (!WORKSPACES_ROOT && gt.realworldCases.length > 0) {
  console.log(`SKIP ${gt.realworldCases.length} real-world case(s): .workspaces/ not found in this environment.`);
  skip += gt.realworldCases.length;
} else {
  for (const cse of gt.realworldCases) {
    if (cse.op === 'extractRegionFromPage') {
      const atlasRelMatch = cse.name.match(/\[([^\]]+\.atlas)\]/);
      const atlasRel = atlasRelMatch[1];
      const pngRel = atlasRel.replace(/\.atlas$/, '.png');
      const pngUrl = `/__workspaces__/${pngRel}`;
      const got = await page.evaluate(({ c, u }) => window.runRealworldExtractCase(c, u), { c: cse, u: pngUrl });
      check(cse.name, comparePixels(cse.name, got, cse.expected, cse.exact, cse.tolerance));
    } else if (cse.op === 'repackMultiPage') {
      const { pages, atlasText } = await page.evaluate((c) => window.runRepackMultiCase(c), cse);
      for (let i = 0; i < cse.expectedPages.length; i++) {
        check(`${cse.name} [page ${i} pixels]`, comparePixels(cse.name, pages[i], cse.expectedPages[i], cse.exact, cse.tolerance));
      }
      check(`${cse.name} [atlas text]`, atlasText.trim() === cse.expectedAtlasText.trim() ? null
        : `atlas text mismatch:\n--- got ---\n${atlasText}\n--- expected ---\n${cse.expectedAtlasText}`);
    }
  }
}

await browser.close();
server.close();
console.log(`\n${pass} passed, ${fail} failed, ${skip} skipped`);
process.exit(fail === 0 ? 0 : 1);
