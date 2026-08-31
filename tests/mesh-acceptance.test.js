import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// parseSkeleton is pure (no DOM) -- unlike everything else in this feature's
// test suite, this belongs in plain `node --test`, not tests/browser/.
//
// tests/fixtures/ch0169-mesh-sample.json (used by tests/browser/verify-mesh-
// mask-acceptance.mjs) was generated from CH0169_SKEL_PATH below via (also
// import writeFileSync from 'node:fs' to run this snippet -- not imported
// above since only the acceptance test itself needs existsSync/readFileSync):
//   const { attachments } = parseSkeleton(new Uint8Array(readFileSync(CH0169_SKEL_PATH)));
//   const sample = {};
//   for (const name of ['CH0169_1', 'CH0169_2', 'CH0169_3']) {
//     const a = attachments.get(name);
//     sample[name] = { type: a.type, uvs: a.uvs, triangles: a.triangles };
//   }
//   writeFileSync('tests/fixtures/ch0169-mesh-sample.json', JSON.stringify(sample, null, 2) + '\n');
// Re-run this snippet to regenerate if the vendored parser or the local
// .skel changes; the fixture holds only numeric uvs/triangles arrays, never
// the licensed binary/texture itself.

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CH0169_SKEL_PATH = path.resolve(HERE, '..', '.workspaces', 'CH0169', 'CH0169_spr.skel');
const VENDOR_PARSER_PATH = path.resolve(HERE, '..', 'www', 'js', 'vendor', 'spine-skeleton-binary', 'index.js');

// The vendored parser is gitignored (pulled fresh via `npm run pull-vendor`,
// see AtlasToolkit's Task 1) -- on a fresh checkout where that hasn't been
// run yet, it doesn't exist. A top-level `import` of it would throw
// ERR_MODULE_NOT_FOUND before this test's own `skip` condition is even
// evaluated, breaking the whole `node --test` run rather than skipping
// cleanly (found by whole-feature scrutinize review, 2026-08-31). Import it
// dynamically, inside the guarded test body, instead.
test(
  'CH0169_1/2/3 parse as Mesh-type attachments (developer-local, self-skips)',
  { skip: !existsSync(CH0169_SKEL_PATH) || !existsSync(VENDOR_PARSER_PATH) },
  async () => {
    const { parseSkeleton } = await import('../www/js/vendor/spine-skeleton-binary/index.js');
    const bytes = new Uint8Array(readFileSync(CH0169_SKEL_PATH));
    const { attachments } = parseSkeleton(bytes);
    for (const name of ['CH0169_1', 'CH0169_2', 'CH0169_3']) {
      assert.equal(attachments.get(name)?.type, 'Mesh');
    }
  },
);
