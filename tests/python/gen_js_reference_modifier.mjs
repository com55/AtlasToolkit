/**
 * Cross-language parity helper for atlas_toolkit/engine/modifier.py -- runs
 * the real www/js/atlas-modifier.js in plain Node (no browser) against a
 * JSON array of {op, args} cases piped in on stdin, and prints the array of
 * results, in the same order, as JSON. Invoked by
 * tests/python/conftest.py's run_modifier_oracle fixture via subprocess.
 *
 * AtlasModifier's constructor and _prepareModImage touch HTMLCanvasElement
 * and document, which Node lacks. FakeCanvas is the minimum stand-in:
 * `instanceof HTMLCanvasElement` succeeds (so _toCanvas returns it as-is),
 * .width/.height carry sizes, and getContext('2d').drawImage(img, x, y) is
 * RECORDED as [x, y] rather than executed. This helper pins geometric
 * decisions only (sizes, paste positions, flags) -- paste's pixel semantics
 * are covered separately by the Stage 3 real-Chromium raster oracle.
 */
import { AtlasDocument } from '../../www/js/atlas-document.js';
import {
  AtlasModifier,
  canvasSizeMatch,
  selectedShareCanvas,
  pickAnchorOffsets,
  resolveModCanvas,
  repackOffsetsForRegion,
  maskCropRectForOffsets,
  _combineMeshGeometry,
  _groupNamesBySpriteIdentity,
} from '../../www/js/atlas-modifier.js';

class FakeCanvas {
  constructor(width = 0, height = 0) {
    this.width = width;
    this.height = height;
    this.draws = [];
  }

  getContext() {
    return { drawImage: (_img, x, y) => { this.draws.push([x, y]); } };
  }
}
globalThis.HTMLCanvasElement = FakeCanvas;
globalThis.document = { createElement: () => new FakeCanvas() };

function newModifier(atlasText, realW, realH, targetPage) {
  return new AtlasModifier(atlasText, 'oracle.atlas', new FakeCanvas(realW, realH), targetPage);
}

const OPS = {
  canvasSizeMatch: (args) => canvasSizeMatch(...args),
  selectedShareCanvas: ([selectedOffsets]) => selectedShareCanvas(selectedOffsets),
  pickAnchorOffsets: ([offsetsWithValues]) => pickAnchorOffsets(offsetsWithValues),
  resolveModCanvas: (args) => {
    const r = resolveModCanvas(...args);
    return {
      orig_canvas_w: r.origCanvasW,
      orig_canvas_h: r.origCanvasH,
      base_orig_w: r.baseOrigW,
      base_orig_h: r.baseOrigH,
      off_x: r.offX,
      off_y: r.offY,
      is_full_canvas: r.isFullCanvas,
    };
  },
  repackOffsetsForRegion: ([name, fullNames, pristineOffsets, packedW, packedH]) =>
    repackOffsetsForRegion(name, fullNames === null ? null : new Set(fullNames), pristineOffsets, packedW, packedH),
  maskCropRectForOffsets: (args) => maskCropRectForOffsets(...args),
  combineMeshGeometry: ([names, lookup]) =>
    _combineMeshGeometry(names, (name) => (Object.hasOwn(lookup, name) ? lookup[name] : null)),
  groupNamesBySpriteIdentity: ([spriteIds, moddedIds]) => {
    // Same id -> the literal same object, mirroring a shared-canvas mod
    // batch storing one canvas under several names.
    const objects = new Map();
    const objectFor = (id) => {
      if (!objects.has(id)) objects.set(id, { id });
      return objects.get(id);
    };
    const toObjects = (ids) => Object.fromEntries(Object.entries(ids).map(([name, id]) => [name, objectFor(id)]));
    return _groupNamesBySpriteIdentity(toObjects(spriteIds), moddedIds === null ? null : toObjects(moddedIds));
  },
  modifierScoped: ([atlasText, realW, realH, targetPage]) => {
    const m = newModifier(atlasText, realW, realH, targetPage);
    return {
      region_names: m.regionNames,
      regions: Object.fromEntries(m.regionNames.map((name) => {
        const r = m.regions[name];
        return [name, {
          bounds: r.bounds,
          offsets: r.offsets,
          rotate: r.rotate,
          page: r.page,
          atlas_name: r.atlasName,
          index: r.index,
          split: r.split,
          pad: r.pad,
          extra_pairs: r.extraPairs.map((pair) => [pair.key, pair.values]),
        }];
      })),
      page_sizes: AtlasDocument.parse(m.atlasText).pages.map((p) => [p.filename, p.size]),
    };
  },
  prepareModImage: ([atlasText, realW, realH, targetPage, modW, modH, selected]) => {
    const m = newModifier(atlasText, realW, realH, targetPage);
    const prep = m._prepareModImage(new FakeCanvas(modW, modH), selected);
    return {
      canvas_w: prep.canvas.width,
      canvas_h: prep.canvas.height,
      draws: prep.canvas.draws,
      mod_w: prep.modW,
      mod_h: prep.modH,
      shared_canvas_mod: prep.sharedCanvasMod,
      is_full_canvas: prep.isFullCanvas,
    };
  },
};

let input = '';
process.stdin.setEncoding('utf8');
for await (const chunk of process.stdin) input += chunk;

const results = JSON.parse(input).map(({ op, args }) => {
  if (!Object.hasOwn(OPS, op)) throw new Error(`unknown op: ${op}`);
  return OPS[op](args);
});
process.stdout.write(JSON.stringify(results));
