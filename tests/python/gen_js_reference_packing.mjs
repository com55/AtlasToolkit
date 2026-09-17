/**
 * Cross-language parity helper for atlas_toolkit/domain/packing.py and the
 * repack half of atlas_toolkit/engine/modifier.py -- runs the real
 * www/js/repack-nest.js and www/js/atlas-modifier.js in plain Node (no
 * browser) against a JSON array of {op, args} cases piped in on stdin, and
 * prints the array of results, in the same order, as JSON. Invoked by
 * tests/python/conftest.py's run_packing_oracle fixture via subprocess.
 *
 * This is a SECOND generator alongside gen_js_reference_modifier.mjs
 * (Stage 4) rather than an extension of it: the repack path needs a
 * richer FakeCanvas (transform recording + synthetic getImageData, so
 * _canvasHash's dedup is observable) and an async result loop, and the
 * Stage 4 generator is a working artifact its own four test files depend
 * on.
 *
 * FakeCanvas carries a `content` string standing in for pixel identity:
 * a 9-argument drawImage (cropAndRotate) composes a crop tag onto the
 * SOURCE's content, and a 3-argument drawImage (the pack paste) records
 * [x, y] and composes onto its OWN content. getImageData synthesises
 * deterministic bytes from `content`, so _canvasHash groups exactly the
 * canvases whose content strings match -- which is what dedup parity
 * actually asserts. Hash STRINGS are never compared across runtimes (the
 * two sides hash different synthetic bytes, and JS silently falls back to
 * a differently-formatted djb2 digest without crypto.subtle).
 */
import {
  AtlasModifier,
  _shelfPack,
  _rotateSpriteForPack,
} from '../../www/js/atlas-modifier.js';
import {
  rotate90CW, rotate90CCW, rotate180, dilate, normalizeGap, growCanvas, nestPack,
} from '../../www/js/repack-nest.js';

function bytesFor(content, w, h) {
  let hash = 2166136261;
  for (let i = 0; i < content.length; i++) {
    hash ^= content.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  const out = new Uint8Array(Math.max(0, w * h * 4));
  for (let i = 0; i < out.length; i++) out[i] = (hash >>> ((i % 4) * 8)) & 0xff;
  return out;
}

class FakeCanvas {
  constructor(width = 0, height = 0, content = null) {
    this.width = width;
    this.height = height;
    this.content = content ?? 'blank';
    this.draws = [];
    this.transforms = [];
  }

  getContext() {
    const self = this;
    return {
      imageSmoothingEnabled: true,
      globalCompositeOperation: 'source-over',
      save() {}, restore() {},
      translate(x, y) { self.transforms.push(['translate', x, y]); },
      rotate(angle) { self.transforms.push(['rotate', angle]); },
      drawImage(src, ...rest) {
        if (rest.length >= 8) {
          const [sx, sy, sw, sh] = rest;
          self.content = `${src.content}|crop(${sx},${sy},${sw},${sh})`;
        } else {
          const [x, y] = rest;
          self.draws.push([x, y]);
          self.content = `${self.content}+${src.content}@${x},${y}`;
        }
      },
      getImageData(_x, _y, w, h) {
        return { data: bytesFor(self.content, w, h) };
      },
    };
  }
}
globalThis.HTMLCanvasElement = FakeCanvas;
globalThis.document = { createElement: () => new FakeCanvas() };

const toMask = (values) => Uint8Array.from(values);

function newModifier(atlasText, realW, realH, targetPage) {
  return new AtlasModifier(atlasText, 'oracle.atlas', new FakeCanvas(realW, realH, 'base'), targetPage);
}

function spriteMap(specs) {
  const sprites = {};
  for (const [name, spec] of Object.entries(specs)) {
    sprites[name] = new FakeCanvas(spec.w, spec.h, spec.content);
  }
  return sprites;
}

const shapeResult = (r) => ({
  canvas_w: r.canvas.width,
  canvas_h: r.canvas.height,
  draws: r.canvas.draws,
  region_bounds: r.regionBounds,
  atlas_text: r.atlasText,
});

const OPS = {
  shelfPack: ([items]) => _shelfPack(items),

  rotateMask: ([which, values, w, h]) => {
    const fn = { cw: rotate90CW, ccw: rotate90CCW, half: rotate180 }[which];
    return Array.from(fn(toMask(values), w, h));
  },

  dilate: ([values, w, h, gap]) => {
    const r = dilate(toMask(values), w, h, gap);
    return { mask: Array.from(r.mask), w: r.w, h: r.h };
  },

  normalizeGap: ([value]) => normalizeGap(value),

  growCanvas: ([canvasW, canvasH, seedRot, gap]) => {
    const occupied = new Uint8Array((canvasW + 2 * gap) * (canvasH + 2 * gap));
    const r = growCanvas(canvasW, canvasH, occupied, seedRot, gap);
    return { canvas_w: r.canvasW, canvas_h: r.canvasH, occupied_len: r.occupied.length };
  },

  nestPack: ([items, opts]) => {
    const built = items.map((item) => ({
      name: item.name, w: item.w, h: item.h, footprint: toMask(item.footprint),
    }));
    try {
      return nestPack(built, opts ?? {});
    } catch (e) {
      return { error: e.message };
    }
  },

  jsKeyOrder: ([names]) => {
    const o = {};
    for (const name of names) o[name] = true;
    return Object.keys(o);
  },

  rotateSpriteForPack: ([w, h, deg]) => {
    const sprite = new FakeCanvas(w, h, 'sprite');
    const rotated = _rotateSpriteForPack(sprite, deg);
    return {
      w: rotated.width,
      h: rotated.height,
      same_object: rotated === sprite,
      transforms: rotated.transforms,
    };
  },

  packAndEmit: ([atlasText, realW, realH, targetPage, specs, fullCanvasNames, nestOptions]) => {
    const m = newModifier(atlasText, realW, realH, targetPage);
    const { pageInfo, regionNames, regions } = m._parseScoped(m.atlasText);
    return m._packAndEmit(
      spriteMap(specs), pageInfo, regionNames, regions,
      fullCanvasNames === null ? null : new Set(fullCanvasNames),
      nestOptions ?? null,
    ).then(shapeResult);
  },

  repackWithModdedSprites: ([atlasText, realW, realH, targetPage, moddedSpecs, fullCanvasNames]) => {
    const m = newModifier(atlasText, realW, realH, targetPage);
    return m.repackWithModdedSprites(
      moddedSpecs === null ? null : spriteMap(moddedSpecs),
      fullCanvasNames === null ? null : new Set(fullCanvasNames),
      null, null,
    ).then((r) => ({ ...shapeResult(r), scoped_names: m.regionNames }));
  },

  repackWithEffectiveModel: ([atlasText, realW, realH, targetPage, dropNames, addedSpecs, moddedSpecs]) => {
    const m = newModifier(atlasText, realW, realH, targetPage);
    const dropped = new Set(dropNames ?? []);
    const effectiveRegions = {};
    for (const name of m.regionNames) {
      if (!dropped.has(name)) effectiveRegions[name] = m.regions[name];
    }
    const effectiveNames = m.regionNames.filter((n) => !dropped.has(n));
    for (const [key, spec] of Object.entries(addedSpecs ?? {})) {
      effectiveRegions[key] = {
        name: key, atlasName: spec.atlas_name, page: targetPage,
        bounds: [0, 0, spec.w, spec.h], offsets: null, rotate: 0,
        index: -1, split: null, pad: null, extraPairs: [],
      };
      effectiveNames.push(key);
    }
    const added = {};
    for (const [key, spec] of Object.entries(addedSpecs ?? {})) {
      added[key] = new FakeCanvas(spec.w, spec.h, spec.content);
    }
    return m.repackWithEffectiveModel(
      effectiveNames, effectiveRegions, added,
      moddedSpecs === null ? null : spriteMap(moddedSpecs),
      null, null, null,
    ).then(shapeResult);
  },
};

let input = '';
process.stdin.setEncoding('utf8');
for await (const chunk of process.stdin) input += chunk;

const results = [];
for (const { op, args } of JSON.parse(input)) {
  if (!Object.hasOwn(OPS, op)) throw new Error(`unknown op: ${op}`);
  results.push(await OPS[op](args));
}
process.stdout.write(JSON.stringify(results));
