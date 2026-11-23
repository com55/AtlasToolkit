// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const html = fs.readFileSync(new URL('../www/index.html', import.meta.url), 'utf8');
const css = fs.readFileSync(new URL('../www/style.css', import.meta.url), 'utf8');

// An installed PWA on Android can draw under the navigation bar; the page
// opts in to the safe-area insets and keeps its content inside them.
test('the viewport opts in to safe-area insets', () => {
  const meta = html.match(/<meta name="viewport" content="([^"]+)"/)[1];
  assert.match(meta, /viewport-fit=cover/);
});

test('the page body is padded by every safe-area inset', () => {
  const body = css.match(/\nbody \{([^}]*)\}/)[1];
  for (const side of ['top', 'right', 'bottom', 'left']) {
    assert.match(body, new RegExp(`env\\(safe-area-inset-${side},\\s*0px\\)`), side);
  }
  assert.match(body, /box-sizing:\s*border-box/);
});
