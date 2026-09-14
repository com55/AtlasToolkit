// Builds minimal, from-scratch (not real Spine export) synthetic .skel-format
// binary fixtures for the 3.8 and 4.2 wire formats, using the REAL vendored
// JS parser (www/js/vendor/spine-skeleton-binary) to self-verify each one
// round-trips before writing it out. No real/licensed skeleton data anywhere
// in this file or its output -- every value is a small hand-chosen number.
//
// Run from repo root (or anywhere): node tests/fixtures/gen-synthetic-skeletons.mjs
// Writes fixtures next to this script, not the invoking shell's cwd -- a
// scrutinize review of the Stage 2 plan caught an earlier version of this
// script writing to a hardcoded machine-specific scratch path instead, which silently
// desynced "regenerate the fixture" from "the fixture tests actually load."
import { writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseSkeleton } from '../../www/js/vendor/spine-skeleton-binary/index.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));

class Writer {
  #chunks = [];
  #textEncoder = new TextEncoder();

  byte(v) { this.#chunks.push(Uint8Array.of(v & 0xff)); return this; }
  ubyte(v) { return this.byte(v); }
  short(v) {
    const b = new Uint8Array(2);
    new DataView(b.buffer).setInt16(0, v, false);
    this.#chunks.push(b);
    return this;
  }
  int32(v) {
    const b = new Uint8Array(4);
    new DataView(b.buffer).setInt32(0, v, false);
    this.#chunks.push(b);
    return this;
  }
  float(v) {
    const b = new Uint8Array(4);
    new DataView(b.buffer).setFloat32(0, v, false);
    this.#chunks.push(b);
    return this;
  }
  bool(v) { return this.byte(v ? 1 : 0); }

  // optimizePositive=true varint -- the only mode this generator needs
  // (every varint field on these code paths uses readVarint(true)).
  varint(v) {
    let value = v >>> 0;
    for (;;) {
      let b = value & 0x7f;
      value >>>= 7;
      if (value !== 0) {
        this.byte(b | 0x80);
      } else {
        this.byte(b);
        break;
      }
    }
    return this;
  }

  // byteCount 0 = null, 1 = empty, >=2 = (byteCount-1) UTF-8 bytes.
  str(s) {
    if (s === null) { this.varint(0); return this; }
    const bytes = this.#textEncoder.encode(s);
    this.varint(bytes.length + 1);
    if (bytes.length > 0) this.#chunks.push(bytes);
    return this;
  }

  // 0 = null ref, N = strings[N-1] -- caller passes the 1-based index directly.
  strRef(indexOrNull) { return this.varint(indexOrNull ?? 0); }

  toBytes() {
    let total = 0;
    for (const c of this.#chunks) total += c.length;
    const out = new Uint8Array(total);
    let off = 0;
    for (const c of this.#chunks) { out.set(c, off); off += c.length; }
    return out;
  }
}

function buildSkeleton38() {
  const w = new Writer();
  w.str('');        // hash
  w.str('3.8.75');  // version
  // walkHeaderAndConstraints38
  w.float(0).float(0).float(0).float(0); // x,y,width,height
  w.bool(false);                          // nonessential
  w.varint(3);                            // numStrings
  w.str('mesh_a');
  w.str('region_b');
  w.str('path_c');
  w.varint(0); // numBones
  w.varint(0); // numSlots
  w.varint(0); // ik constraints
  w.varint(0); // transform constraints
  w.varint(0); // path constraints
  // default skin
  w.varint(1);        // slotCount
  w.varint(0);         // slotIndex (unused)
  w.varint(3);          // attachmentCount
  // attachment 1: Mesh named "mesh_a"
  w.strRef(1);            // attachmentName -> strings[0] = "mesh_a"
  w.strRef(null);          // name ref -> null, falls back to attachmentName
  w.ubyte(2);                // typeIndex 2 = Mesh
  w.strRef(null);              // path ref -> null, falls back to name = "mesh_a"
  w.int32(0);                   // color
  w.varint(3);                   // vertexCount
  w.float(0).float(0).float(1).float(0).float(0.5).float(1); // uvs: 6 floats
  w.varint(1);                    // triangleCount
  w.short(0);                      // triangles[0]
  w.bool(false);                    // readVertices38: unweighted
  w.float(0).float(0).float(10).float(0).float(5).float(10); // 6 vertex floats
  w.varint(0); // hullLength
  // nonessential=false -> no edges/width/height block
  // attachment 2: Region named "region_b"
  w.strRef(2);            // attachmentName -> strings[1] = "region_b"
  w.strRef(null);          // name ref -> null, falls back to attachmentName
  w.ubyte(0);                // typeIndex 0 = Region
  w.strRef(null);              // path ref -> null, falls back to name = "region_b"
  w.float(0).float(0).float(0).float(1).float(1).float(0).float(0); // rotation,x,y,scaleX,scaleY,width,height
  w.int32(0); // color
  // attachment 3: Path named "path_c" -- vertexCount=4 deliberately NOT a
  // multiple of 3 (a single open Bezier curve segment has exactly 4 control
  // points: 3n+1 for n segments). read-skeleton-38.js's real lengthCount
  // calc is float division (`vertexCount / 3`) with a `<` loop bound, i.e.
  // effectively ceil(vertexCount/3) = 2 for vertexCount=4, NOT
  // floor(vertexCount/3) = 1 -- this is exactly the case a scrutinize
  // review of the Stage 2 plan found the Python port getting wrong via
  // floor (`//`) division, and this fixture exists specifically to make
  // that divergence fail a test instead of passing silently.
  w.strRef(3);            // attachmentName -> strings[2] = "path_c"
  w.strRef(null);          // name ref -> null, falls back to attachmentName
  w.ubyte(4);                // typeIndex 4 = Path
  w.bool(false);               // closed
  w.bool(false);                // constantSpeed
  w.varint(4);                   // vertexCount = 4 (not a multiple of 3)
  w.bool(false);                  // readVertices38: unweighted
  w.float(0).float(0).float(1).float(0).float(2).float(0).float(3).float(0); // floatCount=8
  // lengthCount = ceil(4/3) = 2 -- two floats follow
  w.float(0.25).float(0.75);
  // nonessential=false -> no trailing int32
  w.varint(0); // otherSkinCount
  return w.toBytes();
}

function buildSkeleton42() {
  const w = new Writer();
  w.int32(0).int32(0); // hash: 2 int32s
  w.str('4.2.0');       // version
  // walkHeaderAndConstraints42
  w.float(0).float(0).float(0).float(0); // x,y,width,height
  w.float(1);                             // referenceScale
  w.bool(false);                           // nonessential
  w.varint(2);                              // numStrings
  w.str('mesh_a');
  w.str('region_b');
  w.varint(0); // numBones
  w.varint(0); // numSlots
  w.varint(0); // ik constraints
  w.varint(0); // transform constraints
  w.varint(0); // path constraints
  w.varint(0); // physics constraints (4.2-only, no equivalent in 3.8)
  // default skin
  w.varint(1);        // slotCount
  w.varint(0);         // slotIndex (unused)
  w.varint(2);          // attachmentCount
  // attachment 1: Mesh named "mesh_a" -- flags = type index 2 only (0b010),
  // every optional bit (name ref/path ref/color/sequence/weighted) unset.
  w.strRef(1);   // attachmentName -> strings[0] = "mesh_a"
  w.ubyte(2);
  const hullLength = 3;
  w.varint(hullLength);
  w.varint(3);       // vertexCount
  w.float(0).float(0).float(10).float(0).float(5).float(10); // vertices (unweighted)
  w.float(0).float(0).float(1).float(0).float(0.5).float(1);  // uvs, floatCount=6
  // triangleCount = (floatCount - hullLength - 2) * 3 = (6-3-2)*3 = 3
  w.varint(0); w.varint(1); w.varint(2);
  // attachment 2: Region named "region_b" -- flags = type index 0 only, no
  // optional bits set.
  w.strRef(2);   // attachmentName -> strings[1] = "region_b"
  w.ubyte(0);
  w.float(0).float(0).float(0).float(1).float(1).float(0); // 6 unconditional floats
  w.varint(0); // otherSkinCount
  return w.toBytes();
}

const s38 = buildSkeleton38();
const r38 = parseSkeleton(s38);
console.log('3.8 version:', r38.version);
console.log('3.8 mesh_a:', r38.attachments.get('mesh_a'));
console.log('3.8 region_b:', r38.attachments.get('region_b'));
console.log('3.8 path_c:', r38.attachments.get('path_c'));
console.log('3.8 attachment count:', r38.attachments.size);
if (r38.attachments.size !== 3) throw new Error('expected 3 attachments in 3.8 fixture');

const s42 = buildSkeleton42();
const r42 = parseSkeleton(s42);
console.log('4.2 version:', r42.version);
console.log('4.2 mesh_a:', r42.attachments.get('mesh_a'));
console.log('4.2 region_b:', r42.attachments.get('region_b'));
console.log('4.2 attachment count:', r42.attachments.size);

writeFileSync(path.join(HERE, 'synthetic-skeleton-38.skel'), s38);
writeFileSync(path.join(HERE, 'synthetic-skeleton-42.skel'), s42);
console.log('wrote fixtures to', HERE, '-- sizes:', s38.length, s42.length);
