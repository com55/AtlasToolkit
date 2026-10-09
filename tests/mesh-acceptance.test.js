import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// parseSkeleton is pure (no DOM) -- unlike everything else in this feature's
// test suite, this belongs in plain `node --test`, not tests/browser/.
//
// The committed mesh fixture (tests/fixtures/mesh-sample.json, used by
// tests/browser/verify-mesh-mask-acceptance.mjs) is synthetic -- see
// tests/fixtures/gen-mesh-sample.mjs. This test instead checks a real .skel
// when a developer has one at SAMPLE_SKEL_PATH (local-only, never committed).

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SAMPLE_SKEL_PATH = path.resolve(HERE, '..', '.workspaces', 'sample', 'sample_spr.skel');
const VENDOR_PARSER_PATH = path.resolve(HERE, '..', 'www', 'js', 'vendor', 'spine-skeleton-binary', 'index.js');

// The vendored parser is gitignored (pulled fresh via `npm run pull-vendor`,
// see AtlasToolkit's Task 1) -- on a fresh checkout where that hasn't been
// run yet, it doesn't exist. A top-level `import` of it would throw
// ERR_MODULE_NOT_FOUND before this test's own `skip` condition is even
// evaluated, breaking the whole `node --test` run rather than skipping
// cleanly (found by whole-feature scrutinize review, 2026-08-31). Import it
// dynamically, inside the guarded test body, instead.
test(
  'a local .skel parses into Mesh attachments with a usable hullLength (developer-local, self-skips)',
  { skip: !existsSync(SAMPLE_SKEL_PATH) || !existsSync(VENDOR_PARSER_PATH) },
  async () => {
    const { parseSkeleton } = await import('../www/js/vendor/spine-skeleton-binary/index.js');
    const bytes = new Uint8Array(readFileSync(SAMPLE_SKEL_PATH));
    const { attachments } = parseSkeleton(bytes);
    const meshes = [...attachments.values()].filter((a) => a.type === 'Mesh');
    assert.ok(meshes.length > 0, 'expected at least one Mesh attachment');
    for (const mesh of meshes) {
      assert.equal(typeof mesh.hullLength, 'number');
      assert.ok(mesh.hullLength >= 3);
      assert.ok(mesh.hullLength * 2 <= mesh.uvs.length);
    }
  },
);
