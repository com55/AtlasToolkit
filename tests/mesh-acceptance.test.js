import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseSkeleton } from '../www/js/vendor/spine-skeleton-binary/index.js';

// parseSkeleton is pure (no DOM) -- unlike everything else in this feature's
// test suite, this belongs in plain `node --test`, not tests/browser/.
//
// tests/fixtures/mesh-sample.json (used by tests/browser/verify-mesh-
// mask-acceptance.mjs) was generated from SAMPLE_SKEL_PATH below via (also
// import writeFileSync from 'node:fs' to run this snippet -- not imported
// above since only the acceptance test itself needs existsSync/readFileSync):
//   const { attachments } = parseSkeleton(new Uint8Array(readFileSync(SAMPLE_SKEL_PATH)));
//   const sample = {};
//   for (const name of ['SAMPLE_1', 'SAMPLE_2', 'SAMPLE_3']) {
//     const a = attachments.get(name);
//     sample[name] = { type: a.type, uvs: a.uvs, triangles: a.triangles };
//   }
//   writeFileSync('tests/fixtures/mesh-sample.json', JSON.stringify(sample, null, 2) + '\n');
// Re-run this snippet to regenerate if the vendored parser or the local
// .skel changes; the fixture holds only numeric uvs/triangles arrays, never
// the licensed binary/texture itself.

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SAMPLE_SKEL_PATH = path.resolve(HERE, '..', '.workspaces', 'sample', 'sample_spr.skel');

test(
  'SAMPLE_1/2/3 parse as Mesh-type attachments (developer-local, self-skips)',
  { skip: !existsSync(SAMPLE_SKEL_PATH) },
  () => {
    const bytes = new Uint8Array(readFileSync(SAMPLE_SKEL_PATH));
    const { attachments } = parseSkeleton(bytes);
    for (const name of ['SAMPLE_1', 'SAMPLE_2', 'SAMPLE_3']) {
      assert.equal(attachments.get(name)?.type, 'Mesh');
    }
  },
);
