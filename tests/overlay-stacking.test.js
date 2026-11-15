// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

test('busy overlay sits below modals and above other interactive overlays', () => {
  const css = readFileSync(new URL('../www/style.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)];
  const zIndex = selector => {
    const rule = rules.find(([, selectors, body]) => selectors.split(',').some(s => s.trim() === selector) && /z-index\s*:/.test(body));
    assert.ok(rule, selector + ' rule with z-index exists');
    return Number(rule[2].match(/z-index\s*:\s*(\d+)/)[1]);
  };
  const busy = zIndex('#busy-overlay');
  for (const selector of ['#modal-overlay', '#add-region-modal', '#rename-modal']) {
    assert.ok(busy < zIndex(selector), selector + ' must be above busy');
  }
  for (const selector of ['#context-menu', '#drop-overlay', '.missing-images-overlay']) {
    assert.ok(busy > zIndex(selector), selector + ' must be below busy');
  }
});
