/**
 * Browser-driven tests for the Advanced Region Editing feature. This file is
 * created by Task 4 (packer integration) and extended by later tasks.
 *
 * Not part of `node --test` (needs a browser + playwright-core). Run via:
 *
 *   node tests/browser/verify-region-editing.mjs
 *
 * playwright-core is located via $PLAYWRIGHT_CORE, a bare import, then
 * common global locations; the script SKIPS (exit 0) if none resolve --
 * same convention as every other file in tests/browser/.
 */
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WWW  = path.resolve(HERE, '..', '..', 'www');

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

const MIME = {
  '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html',
  '.png': 'image/png', '.json': 'application/json', '.ico': 'image/x-icon',
};
const server = http.createServer((req, res) => {
  let p = decodeURIComponent(req.url.split('?')[0]);
  if (p === '/') p = '/index.html';
  const filePath = path.join(WWW, p);
  if (!filePath.startsWith(WWW) || !fs.existsSync(filePath)) { res.statusCode = 404; return res.end('nf'); }
  res.setHeader('content-type', MIME[path.extname(filePath)] || 'text/plain');
  res.end(fs.readFileSync(filePath));
});
await new Promise((r) => server.listen(0, r));
const URL_ROOT = `http://127.0.0.1:${server.address().port}/`;

let pass = 0, fail = 0;
const check = (name, ok, detail = '') => {
  console.log(`${ok ? 'PASS ' : 'FAIL '} ${name}${detail ? ` (${detail})` : ''}`);
  ok ? pass++ : fail++;
};

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(URL_ROOT);
  await page.waitForTimeout(150);

  // --- Task 4: _packAndEmit()/repackWithEffectiveModel() additive contract ---
  const result = await page.evaluate(async () => {
    const { AtlasModifier } = await import('./js/atlas-modifier.js');

    // Minimal single-region, single-page synthetic atlas, in the same
    // proven bounds:-line format tests/browser/verify-region-identity.mjs
    // already uses successfully — no fixture file needed.
    const atlasText = `page.png
size: 20, 20
arm
bounds: 0, 0, 10, 10
`;
    const pageCanvas = document.createElement('canvas');
    pageCanvas.width = 20; pageCanvas.height = 20;
    pageCanvas.getContext('2d').fillRect(0, 0, 10, 10); // "arm"'s pixels

    const modifier = new AtlasModifier(atlasText, 'test.atlas', pageCanvas, 'page.png');

    // Additive-only check: existing repackWithModdedSprites() output shape
    // must be unaffected (canvas + atlasText only ever read by its callers).
    const existing = await modifier.repackWithModdedSprites({}, null);
    const existingKeys = Object.keys(existing).sort();

    // New method: Add a brand-new "helmet" region via effective-model inputs.
    const helmetCanvas = document.createElement('canvas');
    helmetCanvas.width = 8; helmetCanvas.height = 8;
    helmetCanvas.getContext('2d').fillRect(0, 0, 8, 8);

    const effectiveRegionNames = ['arm', 'helmet'];
    const effectiveRegions = {
      arm: { atlasName: 'arm', offsets: null, index: -1, split: null, pad: null, extraPairs: [] },
      helmet: { atlasName: 'helmet', offsets: null, index: -1, split: null, pad: null, extraPairs: [] },
    };
    const packed = await modifier.repackWithEffectiveModel(
      effectiveRegionNames, effectiveRegions, { helmet: helmetCanvas }, {}, null,
    );

    return {
      existingKeys,
      packedKeys: Object.keys(packed).sort(),
      hasBothRegions: packed.atlasText.includes('arm') && packed.atlasText.includes('helmet'),
      regionBoundsHasBoth: !!packed.regionBounds.arm && !!packed.regionBounds.helmet,
      armBoundsShape: packed.regionBounds.arm.length, // must be 5: [x,y,w,h,rotate]
    };
  });

  check('repackWithModdedSprites now also returns regionBounds (additive)', result.existingKeys.includes('regionBounds'));
  check('repackWithEffectiveModel returns canvas+atlasText+regionBounds', result.packedKeys.join(',') === 'atlasText,canvas,regionBounds');
  check('output atlas text contains both the pristine and the added region', result.hasBothRegions);
  check('regionBounds is keyed by internal key for both regions', result.regionBoundsHasBoth);
  check('each regionBounds entry is [x,y,w,h,rotate]', result.armBoundsShape === 5);

  // --- Task 5: _rebuildSinglePageRepack() branches on _hasStructuralBatches() ---
  const result5 = await page.evaluate(async () => {
    const { AtlasSession, AddBatch } = await import('./js/atlas-session.js');
    const { AtlasProcessor } = await import('./js/atlas-extracter.js');

    const atlasText = `page.png
size: 20, 20
arm
bounds: 0, 0, 10, 10
`;
    const pageCanvas = document.createElement('canvas');
    pageCanvas.width = 20; pageCanvas.height = 20;
    pageCanvas.getContext('2d').fillRect(0, 0, 10, 10);
    const toFile = (canvas, name) => new Promise((res) =>
      canvas.toBlob((b) => res(new File([b], name, { type: 'image/png' })), 'image/png'));

    const processor = new AtlasProcessor(atlasText);
    await processor.loadImages({ 'page.png': await toFile(pageCanvas, 'page.png') });
    const session = new AtlasSession(processor, atlasText, 'test.atlas');

    // Pristine-only path: no structural batches.
    const pristineResult = await session._rebuildSinglePageRepack();

    // Push a structural batch directly (this task tests _rebuildSinglePageRepack's
    // branch in isolation — Task 6 wires the real registration/transaction path).
    const helmetCanvas = document.createElement('canvas');
    helmetCanvas.width = 8; helmetCanvas.height = 8;
    helmetCanvas.getContext('2d').fillRect(0, 0, 8, 8);
    session.modBatches.push(new AddBatch('helmet_key', 'Helmet Display Name', helmetCanvas));
    const structuralResult = await session._rebuildSinglePageRepack();

    return {
      pristineWasStructural: pristineResult.wasStructural,
      pristineHasRegionBounds: !!pristineResult.regionBounds,
      structuralWasStructural: structuralResult.wasStructural,
      structuralHasHelmet: structuralResult.text.includes('Helmet Display Name'),
      structuralRegionBoundsKeyedCorrectly: !!structuralResult.regionBounds && !!structuralResult.regionBounds.helmet_key && structuralResult.regionBounds.helmet_key.length === 5,
      structuralOutputUsesDisplayName: structuralResult.text.includes('Helmet Display Name'),
      structuralResultKeys: Object.keys(structuralResult).sort().join(','),
    };
  });

  check('no structural batches -> wasStructural: false', result5.pristineWasStructural === false);
  check('pristine branch also returns regionBounds now (additive)', result5.pristineHasRegionBounds);
  check('structural batch pending -> wasStructural: true', result5.structuralWasStructural === true);
  check('structural rebuild output actually contains the added region', result5.structuralHasHelmet);
  check('regionBounds is keyed by internal key (helmet_key), not by atlasName/label', result5.structuralRegionBoundsKeyedCorrectly);
  check('output atlas text uses the atlasName (display name), proving atlasName not internalKey is written', result5.structuralOutputUsesDisplayName);
  check('structural result has exactly the {canvas, text, regionBounds, wasStructural} shape', result5.structuralResultKeys === 'canvas,regionBounds,text,wasStructural');

  await browser.close();
  server.close();
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail > 0 ? 1 : 0);
})();
