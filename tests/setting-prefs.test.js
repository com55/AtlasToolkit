// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
import test from 'node:test';
import assert from 'node:assert/strict';
import { AtlasAPI, migrateSettingPrefs } from '../www/js/atlas-api.js';

const defaults = {packer: 'auto', keepLayout: true, shareCompositeImage: false};

test('nothing stored uses the new defaults', () => {
  assert.deepEqual(migrateSettingPrefs({}), defaults);
});

for (const packer of ['rectangles', 'polygons']) {
  test(`${packer}_in_place migrates to the base packer and enables keep layout`, () => {
    assert.deepEqual(migrateSettingPrefs({packer: packer + '_in_place', packKeepLayout: false}), {
      ...defaults, packer, keepLayout: true,
    });
  });
}

for (const packer of ['grid', 'unknown']) {
  test(`unknown packer ${packer} migrates to Auto`, () => {
    assert.deepEqual(migrateSettingPrefs({packer}), defaults);
  });
}

for (const split of [false, true]) {
  test(`legacy split ${split} migrates to sharing off (inverse of true, old default for false)`, () => {
    assert.deepEqual(migrateSettingPrefs({packSplitComposite: split}), {
      ...defaults, shareCompositeImage: false,
    });
  });
  test(`explicit sharing ${split} wins over a legacy split preference`, () => {
    assert.equal(migrateSettingPrefs({packShareCompositeImage: split, packSplitComposite: split}).shareCompositeImage, split);
  });
}

for (const packer of ['auto', 'shelf', 'rectangles', 'polygons']) {
  test(`${packer} preserves explicit layout and sharing preferences`, () => {
    assert.deepEqual(migrateSettingPrefs({packer, packKeepLayout: false, packShareCompositeImage: true}), {
      packer, keepLayout: false, shareCompositeImage: true,
    });
  });
}

for (const stored of [
  {},
  {packer: 'polygons_in_place', packKeepLayout: false, packSplitComposite: false},
  {packer: 'unknown', packSplitComposite: true},
  {packer: 'rectangles', packKeepLayout: false, packShareCompositeImage: false, packSplitComposite: false},
]) {
  test(`loader applies and persists migrated preferences: ${JSON.stringify(stored)}`, async t => {
    const saved = {}, applied = [];
    t.mock.method(AtlasAPI, 'get_pref', async (key, fallback) => Object.hasOwn(stored, key) ? stored[key] : fallback);
    t.mock.method(AtlasAPI, 'set_pref', async (key, value) => { saved[key] = value; });
    t.mock.method(AtlasAPI, 'set_settings', async (partial, options) => { applied.push({partial, options}); });
    await AtlasAPI.init_settings_from_prefs();
    const migration = migrateSettingPrefs(stored);
    assert.deepEqual(applied, [{partial: {
      ...migration, padding: 2, meshAware: true, alpha: 'straight', bleed: true,
    }, options: {busy: false}}]);
    assert.equal(saved.packer, migration.packer);
    assert.equal(saved.packKeepLayout, migration.keepLayout);
    assert.equal(saved.packShareCompositeImage, migration.shareCompositeImage);
    assert.equal(Object.hasOwn(saved, 'packSplitComposite'), false);
  });
}

test('old auto-saved shelf + split=false reset to the new defaults once', () => {
  assert.deepEqual(migrateSettingPrefs({packer: 'shelf', packSplitComposite: false}), defaults);
});
test('old install keeps a non-shelf packer, sharing stays off', () => {
  assert.deepEqual(migrateSettingPrefs({packer: 'rectangles', packSplitComposite: false}), {
    ...defaults, packer: 'rectangles',
  });
});
test('shelf chosen on a new install is kept once sharing is stored', () => {
  assert.deepEqual(migrateSettingPrefs({packer: 'shelf', packShareCompositeImage: true}), {
    ...defaults, packer: 'shelf', shareCompositeImage: true,
  });
});
test('old rectangles_in_place with split=false migrates to rectangles + keep layout', () => {
  assert.deepEqual(migrateSettingPrefs({packer: 'rectangles_in_place', packSplitComposite: false}), {
    ...defaults, packer: 'rectangles', keepLayout: true,
  });
});
