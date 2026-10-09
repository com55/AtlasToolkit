import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PYPROJECT = fs.readFileSync(path.join(ROOT, 'pyproject.toml'), 'utf8');
const VERSION = (PYPROJECT.match(/^version\s*=\s*"([^"]+)"/m) || [])[1];

test('web_version.py prints the pyproject.toml version', () => {
  const r = spawnSync('uv', ['run', 'python', 'scripts/web_version.py'], {
    cwd: ROOT,
    encoding: 'utf8',
  });
  assert.equal(r.status, 0, r.stderr);
  assert.equal(r.stdout.trim(), VERSION);
});

test('web_version.py --stamp injects title and app-version meta', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'atlas-stamp-'));
  const indexPath = path.join(dir, 'index.html');
  fs.writeFileSync(indexPath, '<!doctype html><html><head>\n    <title>Atlas Toolkit</title>\n  </head></html>\n');
  const r = spawnSync('uv', ['run', 'python', 'scripts/web_version.py', '--stamp', indexPath], {
    cwd: ROOT,
    encoding: 'utf8',
  });
  assert.equal(r.status, 0, r.stderr);
  const stamped = fs.readFileSync(indexPath, 'utf8');
  assert.match(stamped, new RegExp(`<title>Atlas Toolkit v${VERSION}</title>`));
  assert.match(stamped, new RegExp(`<meta name="app-version" content="${VERSION}" />`));
  const r2 = spawnSync('uv', ['run', 'python', 'scripts/web_version.py', '--stamp', indexPath], {
    cwd: ROOT,
    encoding: 'utf8',
  });
  assert.equal(r2.status, 0, r2.stderr);
  assert.equal((fs.readFileSync(indexPath, 'utf8').match(/app-version/g) || []).length, 1);
});
