import test from 'node:test';
import assert from 'node:assert/strict';

import { confirmOverwrite, existingOutputNames, writeFilesToFolder } from '../www/js/platform.js';

function missing(name = 'NotFoundError') {
  const error = new Error(name);
  error.name = name;
  return error;
}

/** Directory handle whose existing files are `present`. Records create:true writes. */
function fakeDir(present) {
  const written = [];
  return {
    written,
    async getFileHandle(name, opts = {}) {
      if (opts.create) {
        written.push(name);
        return {
          async createWritable() {
            return { async write() {}, async close() {} };
          },
        };
      }
      const kind = present.get(name);
      if (kind === 'dir') throw missing('TypeMismatchError');
      if (kind === 'file') return { kind: 'file' };
      throw missing('NotFoundError');
    },
  };
}

test('confirmOverwrite skips the dialog when nothing already exists', async () => {
  let calls = 0;
  const ok = await confirmOverwrite([], () => { calls += 1; return false; });
  assert.equal(ok, true);
  assert.equal(calls, 0);
});

test('confirmOverwrite lists only the colliding names and aborts when declined', async () => {
  let prompt = null;
  const ok = await confirmOverwrite(['page.png', 'hero.atlas'], (message, title) => {
    prompt = { message, title };
    return false;
  });
  assert.equal(ok, false);
  assert.equal(prompt.title, 'Overwrite files?');
  assert.match(prompt.message, /page\.png/);
  assert.match(prompt.message, /hero\.atlas/);
  assert.match(prompt.message, /will be replaced/);
});

test('existingOutputNames returns files and same-named directories, not missing names', async () => {
  const present = new Map([['page.png', 'file'], ['notes', 'dir']]);
  const dir = fakeDir(present);
  const found = await existingOutputNames(dir, ['page.png', 'notes', 'new.png', '']);
  assert.deepEqual(found, ['page.png', 'notes']);
});

test('writeFilesToFolder writes nothing when the user declines the overwrite', async () => {
  const dir = fakeDir(new Map([['page.png', 'file']]));
  await assert.rejects(
    () => writeFilesToFolder(
      dir,
      [{ name: 'page.png', data: new Blob(['a']) }, { name: 'fresh.png', data: new Blob(['b']) }],
      async () => false,
    ),
    (error) => error.name === 'AbortError',
  );
  assert.deepEqual(dir.written, []);
});

test('writeFilesToFolder writes every file after the user confirms', async () => {
  const dir = fakeDir(new Map([['page.png', 'file']]));
  let prompted = false;
  await writeFilesToFolder(
    dir,
    [{ name: 'page.png', data: new Blob(['a']) }, { name: 'fresh.png', data: new Blob(['b']) }],
    async (message) => {
      prompted = message.includes('page.png') && !message.includes('fresh.png');
      return true;
    },
  );
  assert.equal(prompted, true);
  assert.deepEqual(dir.written, ['page.png', 'fresh.png']);
});

test('writeFilesToFolder does not ask when no output name exists yet', async () => {
  const dir = fakeDir(new Map());
  let calls = 0;
  await writeFilesToFolder(
    dir,
    [{ name: 'fresh.png', data: 'pixels' }],
    () => { calls += 1; return false; },
  );
  assert.equal(calls, 0);
  assert.deepEqual(dir.written, ['fresh.png']);
});
