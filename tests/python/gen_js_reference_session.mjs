// Real Chromium oracle for Stage 7 extraction and masked-repack parity.
// Run with: node tests/python/gen_js_reference_session.mjs
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');
const WWW = path.join(REPO, 'www');
const OUT = path.join(REPO, 'tests', 'fixtures', 'session_js_oracle.json');

async function loadChromium() {
  const candidates = [process.env.PLAYWRIGHT_CORE, 'playwright-core', 'playwright',
    path.join(os.homedir(), '.npm-global/lib/node_modules/@playwright/mcp/node_modules/playwright-core/index.js')].filter(Boolean);
  for (const candidate of candidates) {
    try {
      const mod = await import(candidate);
      const pw = mod.chromium ? mod : mod.default;
      if (pw?.chromium) return pw.chromium;
    } catch { /* try next installed location */ }
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
  const requested = decodeURIComponent(req.url.split('?')[0]);
  const filePath = path.join(WWW, requested === '/' ? '/index.html' : requested);
  if (!filePath.startsWith(WWW) || !fs.existsSync(filePath)) { res.statusCode = 404; return res.end('not found'); }
  res.setHeader('content-type', MIME[path.extname(filePath)] || 'text/plain');
  res.end(fs.readFileSync(filePath));
});
let browser;
try {
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  const oracle = await page.evaluate(async () => {
    const { AtlasDocument } = await import('./js/atlas-document.js');
    const { AtlasProcessor } = await import('./js/atlas-extracter.js');
    const { extractRegionFromPage } = await import('./js/core-region-ops.js');
    const { AtlasModifier } = await import('./js/atlas-modifier.js');
    const { deriveEffectiveModel } = await import('./js/effective-region-model.js');
    const { AtlasSession, AddBatch, RemoveBatch, RenameBatch } = await import('./js/atlas-session.js');
    const { validateRegionName } = await import('./js/region-name-validation.js');
    const { normalizeGap } = await import('./js/repack-nest.js');
    const { autoConvertAtlas } = await import('./js/atlas-converter.js');
    // Same left-half rectangle, with the descending-screen diagonal avoided:
    // Chromium can emit alpha 254 along that internal seam, even on repeated
    // readback. This triangulation gives binary coverage at all corpus sizes.
    // rgba() pins that prerequisite instead of tolerating alpha differences.
    const rect = { uvs: [0, 0, .5, 0, 0, 1, .5, 1], triangles: [0, 1, 3, 0, 3, 2] };

    function canvas(w, h, pixels) {
      const c = document.createElement('canvas'); c.width = w; c.height = h;
      const ctx = c.getContext('2d');
      if (pixels) { const data = new Uint8ClampedArray(pixels); ctx.putImageData(new ImageData(data, w, h), 0, 0); }
      return c;
    }
    function rgba(c) {
      const pixels = Array.from(c.getContext('2d').getImageData(0, 0, c.width, c.height).data);
      ensure(pixels.every((v, i) => i % 4 !== 3 || v === 0 || v === 255), 'oracle requires binary alpha');
      return pixels;
    }
    function ensure(condition, message) { if (!condition) throw new Error(message); }
    async function loadedProcessor(text, source) {
      const processor = new AtlasProcessor(text);
      await processor.loadImages({ 'p.png': source.toDataURL('image/png') });
      ensure(processor.getPageImage('p.png'), 'source image failed to load');
      return processor;
    }
    const cases = {};
    const extractionDefs = [
      ['plain', 'p.png\nsize: 8,6\nr\n  bounds: 1,1,3,2', [8, 6], [1, 1, 3, 2, 0], null, null],
      ['rotate90', 'p.png\nsize: 8,6\nr\n  bounds: 1,1,3,2\n  rotate: 90', [8, 6], [1, 1, 3, 2, 90], null, null],
      ['rotate180', 'p.png\nsize: 8,6\nr\n  bounds: 1,1,3,2\n  rotate: 180', [8, 6], [1, 1, 3, 2, 180], null, null],
      ['rotate270', 'p.png\nsize: 8,6\nr\n  bounds: 1,1,3,2\n  rotate: 270', [8, 6], [1, 1, 3, 2, 270], null, null],
      ['scaled_half_even', 'p.png\nsize: 8,4\nr\n  bounds: 1,1,3,3', [12, 2], [1, 1, 3, 3, 0], null, null],
      ['zero_size_fallback', 'p.png\nsize: 0,0\nr\n  bounds: 1,1,3,2', [8, 6], [1, 1, 3, 2, 0], null, null],
      ['zero_width_fallback', 'p.png\nsize: 0,3\nr\n  bounds: 1,1,3,2', [8, 6], [1, 1, 3, 2, 0], null, null],
      ['zero_height_fallback', 'p.png\nsize: 4,0\nr\n  bounds: 1,1,3,2', [8, 6], [1, 1, 3, 2, 0], null, null],
      ['offsets', 'p.png\nsize: 8,6\nr\n  bounds: 1,1,3,2\n  offsets: 1,1,6,5', [8, 6], [1, 1, 3, 2, 0], [1, 1, 6, 5], null],
      ['scaled_offsets', 'p.png\nsize: 8,4\nr\n  bounds: 1,1,3,3\n  offsets: 1,1,5,7', [12, 2], [1, 1, 3, 3, 0], [1, 1, 5, 7], null],
      ['clipped_offsets', 'p.png\nsize: 8,6\nr\n  bounds: 1,1,3,2\n  offsets: -1,1,2,2', [8, 6], [1, 1, 3, 2, 0], [-1, 1, 2, 2], null],
      ['masked_unpadded', 'p.png\nsize: 8,6\nr\n  bounds: 1,1,4,2', [8, 6], [1, 1, 4, 2, 0], null, rect],
      ['masked_padded', 'p.png\nsize: 8,6\nr\n  bounds: 1,1,4,2\n  offsets: 1,1,6,4', [8, 6], [1, 1, 4, 2, 0], [1, 1, 6, 4], rect],
    ];
    for (const [name, text, size, bounds, offsets, mesh] of extractionDefs) {
      const sourcePixels = Array.from({ length: size[0] * size[1] }, (_, i) => {
        const x = i % size[0], y = Math.floor(i / size[0]); return [x + 1, y + 2, (x + y + 3) % 256, 255];
      }).flat();
      const source = canvas(...size, sourcePixels);
      const proc = await loadedProcessor(text, source);
      const region = proc.regions.r;
      const pageInfo = proc._pageMap['p.png'];
      if (mesh) proc.setMeshMaskData(new Map([['r', mesh]]), true, false);
      const actual = proc.extractRegion('r');
      const direct = extractRegionFromPage(proc.getPageImage('p.png'), region, pageInfo, mesh);
      ensure(JSON.stringify(rgba(actual)) === JSON.stringify(rgba(direct)), 'processor/core mismatch: ' + name);
      cases[name] = { kind: 'extract', params: { atlas: text, source_size: size, source_rgba: sourcePixels, bounds, offsets, mesh }, output: { width: actual.width, height: actual.height, rgba: rgba(actual) } };
    }

    // The real preview method reverses images before drawing; opaque inputs
    // make order observable without depending on alpha rounding.
    {
      const text = 'p.png\nsize: 2,1\nr\n  bounds: 0,0,1,1\nb\n  bounds: 1,0,1,1';
      const source = canvas(2, 1, [240, 10, 20, 255, 5, 30, 220, 255]);
      const proc = await loadedProcessor(text, source);
      const url = await proc.getPreviewDataURL(['r', 'b']);
      const bitmap = await createImageBitmap(await (await fetch(url)).blob());
      const preview = canvas(bitmap.width, bitmap.height); preview.getContext('2d').drawImage(bitmap, 0, 0);
      cases.opaque_reverse_preview = { kind: 'preview', params: { atlas: text, source_size: [2, 1], source_rgba: rgba(source), names: ['r', 'b'] }, output: { width: preview.width, height: preview.height, rgba: rgba(preview) } };
      URL.revokeObjectURL(url); bitmap.close();
    }

    // One corpus for both repack APIs and both packers. Record the raw
    // input AND Canvas readback to expose normalization of hidden RGB.
    const w = 8, h = 4;
    const first = Array.from({ length: w * h }, (_, i) => {
      const x = i % w, y = Math.floor(i / w);
      return [20 + x, 40 + y, 60 + x + y, 255];
    }).flat();
    const second = first.map((v, i) => i % 4 === 0 && Math.floor(i / 4) % w >= 4 ? v + 30 : v);
    const atlas = 'p.png\nsize: 24,8\na\n  bounds: 0,0,8,4\nb\n  bounds: 8,0,8,4';
    for (const route of ['pixel', 'rename']) for (const nest of [false, true]) {
      for (const variant of ['masked', 'maskoff', 'changed_visible', 'hidden_unmasked']) {
        const a = [...first], b = [...second];
        if (variant === 'changed_visible') b[0] += 1;
        if (variant === 'hidden_unmasked') {
          for (let pixel = 0; pixel < w * h; pixel++) {
            if (pixel % w >= 4) a[pixel * 4 + 3] = b[pixel * 4 + 3] = 0;
          }
        }
        const basePixels = new Uint8ClampedArray(24 * 8 * 4);
        for (let y = 0; y < h; y++) {
          basePixels.set(a.slice(y * w * 4, (y + 1) * w * 4), y * 24 * 4);
          basePixels.set(b.slice(y * w * 4, (y + 1) * w * 4), (y * 24 + w) * 4);
        }
        const masked = variant === 'masked' || variant === 'changed_visible';
        const meshLookup = masked ? () => rect : null;
        const modifier = new AtlasModifier(atlas, 'p.atlas', canvas(24, 8, basePixels), 'p.png');
        const batch = { type: 'rename', targetKey: 'b', newAtlasName: 'bee' };
        const effective = route === 'rename' ? deriveEffectiveModel(modifier.regions, [batch]) : null;
        const nestOptions = { enabled: nest, gapDistance: 0 };
        const result = effective
          ? await modifier.repackWithEffectiveModel(effective.regionNames, effective.regions, {}, {}, null, meshLookup, nestOptions)
          : await modifier.repackWithModdedSprites({}, null, meshLookup, nestOptions);
        const key = route + '_' + variant + '_nest_' + (nest ? 'on' : 'off');
        const dedup = variant === 'masked' || variant === 'hidden_unmasked';
        ensure((JSON.stringify(result.regionBounds.a) === JSON.stringify(result.regionBounds.b)) === dedup, 'unexpected dedup: ' + key);
        const pixels = rgba(result.canvas);
        ensure(pixels.some((value, i) => i % 4 === 3 && value === 255), 'lost visible pattern: ' + key);
        if (masked || variant === 'hidden_unmasked') {
          ensure(pixels.some((value, i) => i % 4 === 3 && value === 0), 'lost alpha cutout: ' + key);
        }
        const inputLeft = a.filter((_, i) => Math.floor(i / 4) % w < 4);
        const otherLeft = b.filter((_, i) => Math.floor(i / 4) % w < 4);
        const inputRight = a.filter((_, i) => Math.floor(i / 4) % w >= 4);
        const otherRight = b.filter((_, i) => Math.floor(i / 4) % w >= 4);
        const sourceIdentity = {
          same_left: inputLeft.every((v, i) => v === otherLeft[i]),
          different_right: inputRight.some((v, i) => v !== otherRight[i]),
          right_alpha: inputRight.filter((_, i) => i % 4 === 3),
        };
        ensure(sourceIdentity.same_left === (variant !== 'changed_visible') && sourceIdentity.different_right, 'invalid source pair: ' + key);
        cases[key] = {
          kind: 'repack',
          params: {
            atlas, route, variant, source_size: [24, 8], source_rgba: Array.from(basePixels),
            sources: { a, b }, source_readback: { a: rgba(canvas(w, h, a)), b: rgba(canvas(w, h, b)) },
            source_identity: sourceIdentity, batch: effective ? batch : null,
            mesh_enabled: masked, mesh: rect, nest_enabled: nest, gap_distance: 0,
          },
          output: {
            width: result.canvas.width, height: result.canvas.height, rgba: pixels,
            region_order: Object.keys(result.regionBounds), bounds: result.regionBounds,
            effective_order: effective?.regionNames ?? null,
            labels: AtlasDocument.parse(result.atlasText).pages[0].regions.map(r => r.atlasName),
            atlas_text: result.atlasText,
          },
        };
      }
    }
    // AtlasSession exposes modify-state rather than Python's byte API. Compose
    // the real JS host seams; do not invent load/save/snapshot methods on it.
    function imageRecord(image) {
      const output = canvas(image.naturalWidth || image.width, image.naturalHeight || image.height);
      output.getContext('2d').drawImage(image, 0, 0);
      return { width: output.width, height: output.height, rgba: rgba(output) };
    }
    function snapshot(session) {
      const proc = session.processor;
      const model = deriveEffectiveModel(proc.regions, session.modBatches);
      const nest = proc.getNestOptions();
      return {
        regions: model.regionNames.map(key => ({ key, label: model.labels[key] ?? key,
          page_filename: model.regions[key].pageFilename ?? proc.pages[0].filename })),
        pages: proc.pages.map(p => p.filename), is_multi_page: session.isMultiPage,
        modified_keys: model.regionNames.filter(key => model.modifiedKeys.has(key)),
        has_pending_modifications: session.hasPendingModifications(),
        has_output: session.hasMergedOutput(), generation: session.modGeneration,
        // Python's snapshot contract prefers published bounds. JS's modify-mode
        // getter itself remains pristine; use the actual pack result when active.
        modify_region_bounds: session.repacked?.regionBounds ?? session.getModifyRegionBounds(),
        mesh_available: !!proc._meshLookup?.size, mesh_mask_enabled: !!proc._maskEnabled,
        mesh_aware_repack_enabled: !!proc._repackMaskEnabled,
        nest_regions_enabled: nest.enabled, nest_gap_distance: nest.gapDistance,
      };
    }
    const readDefs = [
      { name: 'session_two_pages', atlas: 'z.png\nsize: 6,4\n10\n  bounds: 0,0,2,2\nsame\n  bounds: 2,0,2,2\n\na.png\nsize: 4,4\n2\n  bounds: 0,0,2,2\nsame\n  bounds: 2,0,2,2',
        sizes: [['z.png', 6, 4], ['a.png', 4, 4]], selection: ['same#2', 'same', 'missing'] },
      { name: 'session_scaled', atlas: 'p.png\nsize: 8,4\nscaled\n  bounds: 1,1,3,3\n  offsets: 1,1,5,7\n  custom: keep, me',
        sizes: [['p.png', 12, 2]], selection: ['scaled', 'missing'] },
      { name: 'session_old_format', atlas: 'p.png\nsize: 6,4\nlegacy\n  rotate: false\n  xy: 1,1\n  size: 3,2\n  orig: 5,4\n  offset: 1,1\n  index: 2',
        sizes: [['p.png', 6, 4]], selection: ['legacy', 'missing'] },
    ];
    for (const def of readDefs) {
      const converted = autoConvertAtlas(def.atlas);
      const doc = AtlasDocument.parse(converted); // preserve raw save metadata
      const proc = new AtlasProcessor(converted);
      const sources = def.sizes.map(([filename, width, height], pageIndex) => {
        const pixels = Array.from({ length: width * height }, (_, i) =>
          [20 + i % width + pageIndex * 70, 30 + Math.floor(i / width), 40 + i, 255]).flat();
        return { filename, width, height, rgba: pixels };
      });
      // Deliberately reverse input map order. Page preview/save order comes from
      // the document, not the upload map; Python also ignores the corrupt extra.
      const inputOrder = [...sources].reverse();
      const imageMap = Object.fromEntries(inputOrder.map(s =>
        [s.filename, canvas(s.width, s.height, s.rgba).toDataURL('image/png')]));
      await proc.loadImages(imageMap);
      ensure(doc.pages.every(p => proc.getPageImage(p.filename)), 'missing session page');
      const session = new AtlasSession(proc, converted, 'fixture.atlas');
      const before = snapshot(session);
      const names = [...Object.keys(proc.regions), 'missing'];
      const extracted = {};
      for (const key of names) {
        const result = proc.extractRegion(key);
        if (result) extracted[key] = imageRecord(result);
      }
      const previewUrl = await proc.getPreviewDataURL(def.selection);
      const bitmap = await createImageBitmap(await (await fetch(previewUrl)).blob());
      const selectionPreview = imageRecord(bitmap);
      bitmap.close(); URL.revokeObjectURL(previewUrl);
      const savedPages = Object.fromEntries(doc.pages.map(p =>
        [p.filename, imageRecord(proc.getPageImage(p.filename))]));
      const savedText = doc.serialize();
      session.markSaved();
      const afterSave = snapshot(session);
      session.clearModifyState();
      cases[def.name] = { kind: 'session_read',
        params: { atlas: def.atlas, sources: inputOrder, names, selection: def.selection },
        output: { snapshot: before, after_save: afterSave, after_clear: snapshot(session),
          extracts: extracted, default_preview: imageRecord(session.getModifyPageImage(0)),
          selection_preview: selectionPreview, saved_text: savedText, saved_pages: savedPages },
      };
    }
    function patterned(width, height, seed) {
      return { width, height, rgba: Array.from({ length: width * height }, (_, i) =>
        [seed + i % width, 20 + Math.floor(i / width), 40 + i, 255]).flat() };
    }
    function alphaPattern(width, height, seed) {
      return { width, height, rgba: Array.from({ length: width * height }, (_, i) =>
        [seed + i % width, 30 + Math.floor(i / width), 50 + i, i % 3 === 0 ? 0 : 255]).flat() };
    }
    async function previewRecord(proc, names) {
      const url = await proc.getPreviewDataURL(names);
      if (!url) return null;
      const bitmap = await createImageBitmap(await (await fetch(url)).blob());
      try { return imageRecord(bitmap); }
      finally { bitmap.close(); URL.revokeObjectURL(url); }
    }
    const pixelDefs = [
      { name: 'session_pixel_overlap',
        atlas: 'p.png\nsize: 12,4\na\n  bounds: 0,0,2,2\nb\n  bounds: 2,0,2,2\nc\n  bounds: 4,0,2,2\nd\n  bounds: 6,0,2,2',
        source: patterned(12, 4, 10), selection: ['a', 'c'],
        mods: [{ ...patterned(3, 2, 100), names: ['a', 'b'] },
          { ...patterned(2, 3, 160), names: ['b', 'c'] }] },
      { name: 'session_pixel_full_canvas',
        atlas: 'p.png\nsize: 8,4\nr\n  bounds: 1,1,2,1\n  offsets: 1,1,4,3\nkeep\n  bounds: 4,0,2,1\n  offsets: 0,1,3,3',
        source: patterned(8, 4, 10), selection: ['r', 'keep'],
        mods: [{ ...patterned(4, 3, 100), names: ['r'] },
          { ...patterned(2, 1, 160), names: ['r'] }] },
    ];
    for (const def of pixelDefs) {
      const proc = await loadedProcessor(def.atlas,
        canvas(def.source.width, def.source.height, def.source.rgba));
      const session = new AtlasSession(proc, def.atlas, 'fixture.atlas');
      const names = Object.keys(proc.regions);
      const actions = [{ op: 'initial' }, { op: 'repack' }, { op: 'save' },
        { op: 'apply', mod: 0 }, { op: 'apply', mod: 1 }, { op: 'repack' },
        { op: 'save' }, { op: 'repack' }, { op: 'empty' }, { op: 'clear' }];
      const steps = [];
      for (const action of actions) {
        let result = null, saved = null;
        if (action.op === 'apply') {
          const mod = def.mods[action.mod];
          result = await session.processModImage(canvas(mod.width, mod.height, mod.rgba), mod.names);
          if (def.name === 'session_pixel_full_canvas' && action.mod === 0) {
            const prepared = session.modBatches.at(-1).prepared;
            ensure(prepared.isFullCanvas && !prepared.sharedCanvasMod, 'single-selection full canvas discriminator');
          }
        } else if (action.op === 'repack') result = await session.rerunRepack();
        else if (action.op === 'save') {
          const text = session.active?.text ?? AtlasDocument.parse(def.atlas).serialize();
          const source = session.active?.canvas ?? proc.getPageImage('p.png');
          saved = { text, pages: { 'p.png': imageRecord(source) } };
          session.markSaved();
        } else if (action.op === 'empty') {
          ensure(await session.processModImage(null, []) === null, 'empty apply must be a no-op');
        } else if (action.op === 'clear') session.clearModifyState();
        const extracts = Object.fromEntries(names.map(key => [key, imageRecord(proc.extractRegion(key))]));
        steps.push({ action, snapshot: snapshot(session), saved,
          result: result ? { regions: result.regions, region_pages: result.regionPages,
            pages: result.pages, image: imageRecord(session.active.canvas) } : null,
          default_preview: imageRecord(session.active?.canvas ?? session.getModifyPageImage(0)),
          selection_preview: await previewRecord(proc, def.selection), extracts });
        if (result?.image) URL.revokeObjectURL(result.image);
      }
      cases[def.name] = { kind: 'session_pixel',
        params: { atlas: def.atlas, source: def.source, mods: def.mods, selection: def.selection },
        output: { steps } };
    }
    // Structural batches are the actual engine seam. Validation and Add-key
    // reservation below compose the host policy verified in atlas-api.js:
    // _generateAddInternalKey / add_region / rename_region (no file-picker I/O).
    async function structuralAction(session, action, sources) {
      const effective = deriveEffectiveModel(session.processor.regions, session.modBatches);
      if (action.op === 'remove' || action.op === 'remove_last') {
        return session.applyStructuralBatch(new RemoveBatch(action.key));
      }
      const others = effective.regionNames.filter(key => action.op !== 'rename' || key !== action.key)
        .map(key => effective.labels[key] ?? key);
      const validation = validateRegionName(action.name, others);
      ensure(validation.ok, 'invalid oracle structural name: ' + validation.reason);
      if (action.op === 'rename') {
        return session.applyStructuralBatch(new RenameBatch(action.key, validation.value));
      }
      const reserved = new Set(Object.keys(session.processor.regions));
      for (const batch of session.modBatches) if (batch.type === 'add') reserved.add(batch.internalKey);
      let key = validation.value, suffix = 2;
      while (reserved.has(key)) key = `${validation.value}_${suffix++}`;
      const source = sources[action.source];
      return session.applyStructuralBatch(new AddBatch(key, validation.value,
        canvas(source.width, source.height, source.rgba)));
    }
    const numericSource = patterned(12, 4, 10);
    const numericAdd = { width: 2, height: 2, rgba: [0, 1].flatMap(y =>
      numericSource.rgba.slice((y * 12 + 2) * 4, (y * 12 + 4) * 4)) };
    const structuralDefs = [
      { name: 'session_structural_numeric',
        atlas: 'p.png\nsize: 12,4\n10\n bounds: 0,0,2,2\n2\n bounds: 2,0,2,2\na\n bounds: 4,0,2,2',
        source: numericSource, sources: [numericAdd, patterned(3, 1, 160)], selection: ['2', '1'], nest: false,
        actions: [{ op: 'initial' }, { op: 'rename', key: '2', name: 'two' },
          { op: 'add', name: ' 1 ', source: 0 }, { op: 'save' },
          { op: 'rename', key: '1', name: 'one' },
          { op: 'apply', source: 1, names: ['1'] }, // Add source still overrides this mod.
          { op: 'add', name: '2', source: 1 }, // Pristine key2 reserved: new key2_2.
          { op: 'remove', key: '1' }, { op: 'remove', key: '2_2' },
          { op: 'save' }, { op: 'repack' }, { op: 'clear' }] },
      { name: 'session_structural_scaled',
        atlas: 'p.png\nsize: 8,12\na\n bounds: 1,2,2,4\n offsets: 1,2,5,8\n index: 3\n split: 1,2,3,4\n custom: keep, pair\nkeep\n bounds: 4,0,2,2',
        source: patterned(12, 6, 20), sources: [patterned(3, 1, 120)], selection: ['a', 'added'], nest: false,
        actions: [{ op: 'initial' }, { op: 'rename', key: 'a', name: 'scaled' },
          { op: 'add', name: 'added', source: 0 }, { op: 'save' },
          { op: 'remove', key: 'keep' }, { op: 'repack' }, { op: 'save' }] },
      ...[false, true].map(nest => ({ name: `session_structural_last_remove_${nest ? 'nest' : 'shelf'}`,
        atlas: 'p.png\nsize: 4,3\nr\n bounds: 1,1,2,1', source: patterned(4, 3, 30),
        sources: [patterned(2, 1, 90)], selection: ['r'], nest,
        actions: [{ op: 'initial' }, { op: 'apply', source: 0, names: ['r'] },
          { op: 'save' }, { op: 'remove_last', key: 'r' }, { op: 'save' }] })),
      { name: 'session_cross_slice',
        atlas: 'p.png\nsize: 14,8\n10\n bounds: 0,0,4,4\n2\n bounds: 0,4,4,4\na\n bounds: 6,0,6,4',
        source: alphaPattern(14, 8, 10), sources: [alphaPattern(2, 3, 100)],
        mod: alphaPattern(6, 4, 170), mesh: rect, selection: ['a', '1'], nest: false,
        actions: [{ op: 'initial' }, { op: 'rename', key: '2', name: 'two' },
          { op: 'add', name: '1', source: 0 },
          { op: 'data', value: true }, { op: 'crop', value: true },
          { op: 'mesh', value: true }, { op: 'nest', value: true }, { op: 'gap', value: '2.9' },
          { op: 'apply', source: 'mod', names: ['a'] }, { op: 'save' },
          { op: 'crop', value: false }, { op: 'mesh', value: false },
          { op: 'crop', value: true }, { op: 'nest', value: false },
          { op: 'apply', source: 'mod', names: ['a'] },
          { op: 'remove', key: '1' }, { op: 'save' }, { op: 'clear' },
          { op: 'add', name: 'postclear', source: 0 }, { op: 'save' }] },
    ];
    for (const def of structuralDefs) {
      const proc = await loadedProcessor(def.atlas, canvas(def.source.width, def.source.height, def.source.rgba));
      proc.setNestOptions(def.nest, 4);
      const session = new AtlasSession(proc, def.atlas, 'fixture.atlas');
      const steps = [];
      for (const action of def.actions) {
        let result = null, saved = null, rejected = false;
        if (['add', 'rename', 'remove'].includes(action.op)) {
          result = await structuralAction(session, action, def.sources);
        } else if (action.op === 'remove_last') {
          const before = JSON.stringify(snapshot(session)), active = session.active;
          const pixels = JSON.stringify(imageRecord(active.canvas));
          try { result = await structuralAction(session, action, def.sources); }
          catch (error) {
            ensure(error.message === 'canvas.toBlob failed', 'last-region failure must be zero-canvas encoding');
            rejected = true;
          }
          ensure(rejected && session.active === active && JSON.stringify(snapshot(session)) === before,
            'last-region removal must fail and restore saved state');
          ensure(JSON.stringify(imageRecord(active.canvas)) === pixels, 'rollback must retain previous pixels');
        } else if (action.op === 'apply') {
          const source = action.source === 'mod' ? def.mod : def.sources[action.source];
          result = await session.processModImage(canvas(source.width, source.height, source.rgba), action.names);
        } else if (action.op === 'repack') result = await session.rerunRepack();
        else if (def.name === 'session_cross_slice' && action.op === 'data') {
          proc.setMeshMaskData(action.value ? new Map([['a', def.mesh]]) : null,
            proc._maskEnabled, proc._repackMaskEnabled);
        } else if (def.name === 'session_cross_slice' && action.op === 'crop') {
          proc.setMeshMaskData(proc._meshLookup, !!action.value, proc._repackMaskEnabled);
        } else if (def.name === 'session_cross_slice' && action.op === 'mesh') {
          proc.setMeshMaskData(proc._meshLookup, proc._maskEnabled, !!action.value);
          if (session.modBatches.length) result = await session.rerunRepack();
        } else if (def.name === 'session_cross_slice' && (action.op === 'nest' || action.op === 'gap')) {
          const nest = proc.getNestOptions();
          proc.setNestOptions(action.op === 'nest' ? !!action.value : nest.enabled,
            action.op === 'gap' ? normalizeGap(action.value) : nest.gapDistance);
          if (session.modBatches.length) result = await session.rerunRepack();
        }
        else if (action.op === 'save') {
          saved = { text: session.active.text, pages: { 'p.png': imageRecord(session.active.canvas) } };
          session.markSaved();
        } else if (action.op === 'clear') session.clearModifyState();
        if (def.name === 'session_structural_numeric' && action.op === 'add' && action.name === ' 1 ') {
          const model = deriveEffectiveModel(proc.regions, session.modBatches);
          ensure(JSON.stringify(model.regionNames) === JSON.stringify(['2', '10', 'a', '1']), 'numeric Add array order');
          ensure(JSON.stringify(session.repacked.regionBounds['2']) === JSON.stringify(session.repacked.regionBounds['1']), 'numeric Add dedup');
          ensure(AtlasDocument.parse(session.active.text).pages[0].regions[0].atlasName === 'two', 'canonical order retained');
        }
        const readNames = [...new Set([...Object.keys(proc.regions), ...def.selection])];
        const extracts = {};
        for (const key of readNames) {
          const image = proc.extractRegion(key);
          if (image) extracts[key] = imageRecord(image);
        }
        steps.push({ action, rejected, snapshot: snapshot(session), saved,
          default_preview: imageRecord(session.active?.canvas ?? proc.getPageImage('p.png')),
          selection_preview: await previewRecord(proc, def.selection), read_names: readNames, extracts });
        if (def.name === 'session_cross_slice') {
          const step = steps.at(-1);
          step.result = result ? { regions: result.regions,
            region_pages: result.regionPages, pages: result.pages,
            image: imageRecord(session.active.canvas) } : null;
        }
        if (result?.image) URL.revokeObjectURL(result.image);
      }
      cases[def.name] = { kind: 'session_structural',
        params: { atlas: def.atlas, source: def.source, sources: def.sources, selection: def.selection, nest: def.nest,
          ...(def.mod ? { mod: def.mod } : {}), ...(def.mesh ? { mesh: def.mesh } : {}) },
        output: { steps } };
    }
    // Task5 host composition follows atlas-api _maybeRerunRepack: options
    // rerun for ANY registered batch, while cropping/data attachment never do.
    for (const route of ['pixel', 'structural']) {
      const atlas = 'p.png\nsize: 16,4\na\n bounds: 0,0,8,4\nb\n bounds: 8,0,8,4';
      const source = patterned(16, 4, 10), mod = patterned(8, 4, 120);
      const proc = await loadedProcessor(atlas, canvas(source.width, source.height, source.rgba));
      const session = new AtlasSession(proc, atlas, 'fixture.atlas');
      const selection = ['a'], names = ['a', 'b'];
      const lookup = { a: rect }; // b intentionally has no mesh: ordinary solid.
      const actions = [
        { op: 'initial' }, { op: 'data', value: {} },
        { op: 'crop', value: true }, { op: 'mesh', value: true },
        { op: 'nest', value: true }, { op: 'gap', value: '2.9' },
        { op: 'data', value: lookup }, { op: route },
        { op: 'save' }, { op: 'crop', value: false },
        { op: 'mesh', value: false }, { op: 'save' },
        { op: 'nest', value: false }, { op: 'gap', value: ' 7 ' },
        { op: 'mesh', value: true }, { op: 'mesh', value: true },
        { op: 'data', value: null }, { op: 'save' }, { op: 'repack' },
        { op: 'data', value: lookup }, { op: 'nest', value: true },
        { op: 'crop', value: true }, { op: 'save' }, { op: 'clear' },
        { op: 'gap', value: 0 },
      ];
      const steps = [];
      for (const action of actions) {
        let result = null, saved = null;
        const before = snapshot(session), previousActive = session.active;
        if (action.op === 'data') {
          proc.setMeshMaskData(action.value === null ? null : new Map(Object.entries(action.value)),
            proc._maskEnabled, proc._repackMaskEnabled);
        } else if (action.op === 'crop') {
          proc.setMeshMaskData(proc._meshLookup, !!action.value, proc._repackMaskEnabled);
        } else if (action.op === 'mesh') {
          proc.setMeshMaskData(proc._meshLookup, proc._maskEnabled, !!action.value);
          if (session.modBatches.length) result = await session.rerunRepack();
        } else if (action.op === 'nest' || action.op === 'gap') {
          const nest = proc.getNestOptions();
          proc.setNestOptions(action.op === 'nest' ? !!action.value : nest.enabled,
            action.op === 'gap' ? normalizeGap(action.value) : nest.gapDistance);
          if (session.modBatches.length) result = await session.rerunRepack();
        } else if (action.op === 'pixel') {
          result = await session.processModImage(canvas(mod.width, mod.height, mod.rgba), ['a']);
        } else if (action.op === 'structural') {
          result = await session.applyStructuralBatch(new RenameBatch('a', 'renamed'));
        } else if (action.op === 'repack') result = await session.rerunRepack();
        else if (action.op === 'save') {
          saved = { text: session.active.text, pages: { 'p.png': imageRecord(session.active.canvas) } };
          session.markSaved();
        } else if (action.op === 'clear') session.clearModifyState();
        const snap = snapshot(session);
        if (action.op === 'crop' || action.op === 'data') {
          ensure(session.active === previousActive && snap.generation === before.generation,
            'cropping/data must not replace active output');
        }
        const extracts = Object.fromEntries(names.map(key => [key, imageRecord(proc.extractRegion(key))]));
        if (proc._maskEnabled && proc._meshLookup?.has('a')) {
          ensure(extracts.a.rgba[3] === 255 && extracts.a.rgba[7 * 4 + 3] === 0,
            'cropping must keep left and remove right alpha');
        } else ensure(extracts.a.rgba[7 * 4 + 3] === 255, 'unmasked extraction must restore right alpha');
        ensure(extracts.b.rgba.every((v, i) => i % 4 !== 3 || v === 255), 'mesh miss must remain solid');
        steps.push({ action, snapshot: snap, saved,
          result: result ? { regions: result.regions, region_pages: result.regionPages,
            pages: result.pages, image: imageRecord(session.active.canvas) } : null,
          default_preview: imageRecord(session.active?.canvas ?? proc.getPageImage('p.png')),
          selection_preview: await previewRecord(proc, selection), extracts });
        if (result?.image) URL.revokeObjectURL(result.image);
      }
      cases['session_mesh_nest_' + route] = { kind: 'session_mesh_nest',
        params: { atlas, source, mod, selection, names }, output: { steps } };
    }
    // Multi-page replace (Python spec 2026-10-21 §3.10): the app packs each
    // page that owns a modified region on its own (_rebuildMultiPageRepack);
    // a composite spanning pages gets one prepared copy per page; untouched
    // pages keep their pristine canvas.
    // f is never modded: the repacked page one must carry its pristine pixels.
    const multiAtlas = 'one.png\nsize: 12,4\na\n  bounds: 0,0,2,2\nb\n  bounds: 2,0,3,2\nf\n  bounds: 5,0,3,3\n'
      + '\ntwo.png\nsize: 8,6\nc\n  bounds: 0,0,2,3\n  offsets: 0,1,3,4\nd\n  bounds: 2,0,2,2\n'
      + '\nthree.png\nsize: 6,6\ne\n  bounds: 0,0,2,2\n';
    const multiSizes = [['one.png', 12, 4], ['two.png', 8, 6], ['three.png', 6, 6]];
    const multiMods = [{ ...patterned(3, 2, 100), names: ['a'] },
      { ...patterned(4, 3, 160), names: ['b', 'c'] }];
    for (const nest of [false, true]) {
      const sources = multiSizes.map(([filename, width, height], i) =>
        ({ filename, ...patterned(width, height, 10 + 60 * i) }));
      const proc = new AtlasProcessor(multiAtlas);
      await proc.loadImages(Object.fromEntries(sources.map(s =>
        [s.filename, canvas(s.width, s.height, s.rgba).toDataURL('image/png')])));
      ensure(sources.every(s => proc.getPageImage(s.filename)), 'missing multi-page image');
      if (nest) proc.setNestOptions(true, 0);
      const session = new AtlasSession(proc, multiAtlas, 'fixture.atlas');
      ensure(session.isMultiPage, 'fixture must be multi-page');
      for (const mod of multiMods) {
        await session.processModImage(canvas(mod.width, mod.height, mod.rgba), mod.names);
      }
      const pages = Object.fromEntries(proc.pages.map((p, i) => [p.filename, imageRecord(session.active.pages[i])]));
      ensure(JSON.stringify(pages['three.png'].rgba) === JSON.stringify(sources[2].rgba), 'untouched page must stay pristine');
      cases['session_multipage_' + (nest ? 'nest' : 'shelf')] = { kind: 'session_multipage',
        params: { atlas: multiAtlas, sources, mods: multiMods, nest },
        output: { text: session.active.text, pages } };
    }
    return cases;
  });
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, `${JSON.stringify({ schema: 1, cases: oracle })}\n`);
  console.log(`Wrote ${OUT} with ${Object.keys(oracle).length} cases.`);
  for (const name of Object.keys(oracle)) console.log(`  ${name}`);
} finally {
  if (browser) await browser.close();
  if (server.listening) await new Promise((resolve) => server.close(resolve));
}
