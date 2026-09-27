import test from 'node:test';
import assert from 'node:assert/strict';

import { nestPack } from '../www/js/repack-nest.js';
import {
  runNestPack,
  terminateJobRunnerForTests,
  jobRunnerOrigin,
} from '../www/js/job-runner.js';

test.describe('job-runner', { concurrency: false }, () => {
  test.after(async () => {
    await terminateJobRunnerForTests();
  });

  function twoRectItems() {
    const a = new Uint8Array(8 * 8).fill(1);
    const b = new Uint8Array(4 * 4).fill(1);
    return [
      { name: 'a', w: 8, h: 8, footprint: a },
      { name: 'b', w: 4, h: 4, footprint: b },
    ];
  }

  test('runNestPack matches nestPack numbers for the same items', async () => {
    const items = twoRectItems();
    const expected = nestPack(items, { gapDistance: 2 });
    const got = await runNestPack(items.map((it) => ({
      name: it.name,
      w: it.w,
      h: it.h,
      footprint: it.footprint.slice(),
    })), { gapDistance: 2 });
    assert.deepEqual(got, expected);
  });

  test('runNestPack copies a shared footprint so the caller can still read it', async () => {
    const shared = new Uint8Array(4).fill(1);
    const items = [{ name: 'one', w: 2, h: 2, footprint: shared }];
    const got = await runNestPack(items, { gapDistance: 1 });
    assert.equal(shared[0], 1);
    assert.equal(got.placements[0].name, 'one');
  });

  test('runNestPack posts incrementing job ids when Worker exists', async () => {
    await terminateJobRunnerForTests();
    const posted = [];
    class FakeWorker {
      constructor() {
        this.onmessage = null;
        this.onerror = null;
        this.onmessageerror = null;
      }
      postMessage(data) {
        posted.push(data);
        queueMicrotask(() => {
          if (this.onmessage) {
            this.onmessage({
              data: { id: data.id, ok: true, result: { canvasW: 0, canvasH: 0, placements: [] } },
            });
          }
        });
      }
      terminate() {}
    }
    const previous = globalThis.Worker;
    globalThis.Worker = FakeWorker;
    try {
      const got = await runNestPack([], {});
      assert.equal(got.canvasW, 0);
      assert.equal(posted.length, 2);
      assert.equal(typeof posted[0].id, 'number');
      assert.equal(typeof posted[1].id, 'number');
      assert.notEqual(posted[0].id, posted[1].id);
      assert.equal(jobRunnerOrigin(), 'module-url');
    } finally {
      if (previous === undefined) delete globalThis.Worker;
      else globalThis.Worker = previous;
      await terminateJobRunnerForTests();
    }
  });
});
