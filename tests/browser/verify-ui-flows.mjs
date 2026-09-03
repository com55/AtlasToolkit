/**
 * Scripted UI-flow verification driving the REAL index.html DOM (not the
 * AtlasAPI harness that verify-app-e2e.mjs uses).
 *
 * Covers, at a desktop viewport (1440x900):
 *   load -> multi-select (click / ctrl / shift) -> extract-selected download
 *   -> enter edit mode via the mode toggle -> multi-page prev/next switcher
 *   -> mod apply through the real file-chooser (ReplaceSelected) -> sequential
 *   second mod (rebuild-from-pristine regression, at the UI level) -> repack
 *   toggle on/off without a reload -> unsaved-changes guard (cancel + confirm
 *   paths) -> missing-page dialog abort -> Save As... zip download.
 * Then a condensed touch pass (390x844, isMobile+hasTouch): tap-select,
 * edit mode, page switcher, guard dialog.
 *
 * Not part of `node --test` (needs a browser + playwright-core). Run via:
 *
 *   node test/browser/verify-ui-flows.mjs
 *
 * playwright-core is located via $PLAYWRIGHT_CORE, a bare import, then common
 * global locations; the script SKIPS (exit 0) if none resolve.
 */
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WWW  = path.resolve(HERE, '..', '..', 'www');

async function loadChromium() {
  const candidates = [
    process.env.PLAYWRIGHT_CORE,
    'playwright-core',
    'playwright',
    path.join(os.homedir(), '.npm-global/lib/node_modules/@playwright/mcp/node_modules/playwright-core/index.js'),
  ].filter(Boolean);
  for (const c of candidates) {
    try {
      const mod = await import(c);
      const pw = mod.chromium ? mod : mod.default;
      if (pw && pw.chromium) return pw.chromium;
    } catch { /* try next */ }
  }
  return null;
}

const chromium = await loadChromium();
if (!chromium) {
  console.log('SKIP: playwright-core not found (set $PLAYWRIGHT_CORE to its index.js to run).');
  process.exit(0);
}

const MIME = {
  '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html',
  '.png': 'image/png', '.json': 'application/json', '.ico': 'image/x-icon',
};
const server = http.createServer((req, res) => {
  let p = decodeURIComponent(req.url.split('?')[0]);
  if (p === '/') p = '/index.html';
  const filePath = path.join(WWW, p);
  if (!filePath.startsWith(WWW) || !fs.existsSync(filePath)) { res.statusCode = 404; return res.end('nf'); }
  res.setHeader('content-type', MIME[path.extname(filePath)] || 'text/plain');
  res.end(fs.readFileSync(filePath));
});
await new Promise((r) => server.listen(0, r));
const URL_ROOT = `http://127.0.0.1:${server.address().port}/`;

const MOD_PNG = fs.readFileSync(path.join(HERE, 'fixtures/opaque-transparent/fixture-opaque.png'));

let pass = 0, fail = 0;
const check = (name, ok, detail = '') => {
  console.log(`${ok ? 'PASS ' : 'FAIL '} ${name}${detail ? ` (${detail})` : ''}`);
  ok ? pass++ : fail++;
};

/** Load a synthetic 2-page atlas through the app's real load path + refresh the region list. */
async function loadFixtureAtlas(page) {
  return page.evaluate(async () => {
    const { AtlasAPI } = await import('./js/atlas-api.js');
    const { loadRegions } = await import('./js/region-list.js');
    const { state } = await import('./js/state.js');

    const solid = (w, h, rgba) => {
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      const ctx = c.getContext('2d');
      ctx.fillStyle = `rgba(${rgba.join(',')})`;
      ctx.fillRect(0, 0, w, h);
      return c;
    };
    const toFile = (canvas, name) => new Promise((res) =>
      canvas.toBlob((b) => res(new File([b], name, { type: 'image/png' })), 'image/png'));

    const atlasText = `page1.png
size: 100, 100
alpha
bounds: 0, 0, 30, 30
beta
bounds: 30, 0, 30, 30
gamma
bounds: 0, 30, 40, 40

page2.png
size: 80, 80
delta
bounds: 0, 0, 40, 40
epsilon
bounds: 40, 0, 30, 30
`;
    const atlasFile = new File([atlasText], 'flows.atlas', { type: 'text/plain' });
    const p1 = await toFile(solid(100, 100, [40, 80, 120, 255]), 'page1.png');
    const p2 = await toFile(solid(80, 80, [120, 40, 80, 255]), 'page2.png');
    const ok = await AtlasAPI.load_atlas_from_file(atlasFile, { 'page1.png': p1, 'page2.png': p2 });
    if (!ok) return { ok: false };
    state.selectedIndices.clear();
    state.lastClickIndex = -1;
    await loadRegions();
    return { ok: true, names: AtlasAPI.get_region_names() };
  });
}

/** Load a synthetic 1-page atlas through the app's real load path (Task 8's multi-page-guard test needs a single-page case; the file's only existing fixture is 2-page). */
async function loadSinglePageFixtureAtlas(page) {
  return page.evaluate(async () => {
    const { AtlasAPI } = await import('./js/atlas-api.js');
    const { loadRegions } = await import('./js/region-list.js');
    const { state } = await import('./js/state.js');

    const solid = (w, h, rgba) => {
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      const ctx = c.getContext('2d');
      ctx.fillStyle = `rgba(${rgba.join(',')})`;
      ctx.fillRect(0, 0, w, h);
      return c;
    };
    const toFile = (canvas, name) => new Promise((res) =>
      canvas.toBlob((b) => res(new File([b], name, { type: 'image/png' })), 'image/png'));

    const atlasText = `page1.png
size: 50, 50
zeta
bounds: 0, 0, 20, 20
omega
bounds: 25, 0, 20, 20
`;
    const atlasFile = new File([atlasText], 'single.atlas', { type: 'text/plain' });
    const p1 = await toFile(solid(50, 50, [80, 40, 200, 255]), 'page1.png');
    const ok = await AtlasAPI.load_atlas_from_file(atlasFile, { 'page1.png': p1 });
    if (!ok) return { ok: false };
    state.selectedIndices.clear();
    state.lastClickIndex = -1;
    await loadRegions();
    return { ok: true, names: AtlasAPI.get_region_names() };
  });
}

const readUi = (page) => page.evaluate(() => ({
  count: document.getElementById('count').innerText,
  items: document.querySelectorAll('.region-item').length,
  selected: [...document.querySelectorAll('.region-item.selected')].map((el) => el.innerText),
  mode: document.getElementById('mode-modify').classList.contains('active') ? 'modify' : 'extract',
  editEnabled: !document.getElementById('mode-modify').disabled,
  extractAllEnabled: !document.getElementById('btn-extract-all').disabled,
  modifyControlsVisible: !document.getElementById('modify-controls').classList.contains('hidden'),
  switcherVisible: !document.getElementById('modify-page-switcher').classList.contains('hidden'),
  indicator: document.getElementById('page-indicator').innerText,
  prevDisabled: document.getElementById('page-prev').disabled,
  nextDisabled: document.getElementById('page-next').disabled,
  saveEnabled: !document.getElementById('btn-save-mod').disabled,
  saveVisible: !document.getElementById('save-split').classList.contains('hidden'),
  saveMenuOpen: document.getElementById('save-menu').classList.contains('open'),
  repackRowVisible: !document.getElementById('repack-options').classList.contains('hidden'),
  resetEnabled: !document.getElementById('btn-reset-mod').disabled,
  saveMergedEnabled: !document.getElementById('btn-save-merged').disabled,
  modalVisible: !document.getElementById('modal-overlay').classList.contains('hidden'),
  previewSrcLen: (document.getElementById('preview-img').src || '').length,
  missingDialog: !!document.querySelector('.missing-images-overlay'),
}));

// ─── Desktop pass ─────────────────────────────────────────────────────────────
const browser = await chromium.launch({ headless: true });
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  // The app prefers showSaveFilePicker (a native dialog automation can't
  // drive) when available. Remove it so _downloadBlob takes the anchor
  // download fallback — same code path a non-FS-API browser (Firefox) gets —
  // which Playwright can observe via the 'download' event.
  await ctx.addInitScript(() => { try { delete window.showSaveFilePicker; } catch { /* ignore */ } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(URL_ROOT, { waitUntil: 'networkidle' });

  // 1. Load
  const loaded = await loadFixtureAtlas(page);
  check('desktop: atlas loads through real load path', loaded.ok && loaded.names.length === 5, `names=${loaded.names?.length}`);
  let ui = await readUi(page);
  check('desktop: region list rendered + count badge', ui.items === 5 && ui.count === '5', `items=${ui.items} count=${ui.count}`);
  check('desktop: Edit toggle + Extract All enabled after load', ui.editEnabled && ui.extractAllEnabled);

  // 2. Multi-select: click, shift-click, ctrl-click
  const items = page.locator('.region-item');
  await items.nth(0).click();
  await items.nth(2).click({ modifiers: ['Shift'] });
  await page.waitForTimeout(120); // 50ms preview debounce
  ui = await readUi(page);
  check('desktop: shift-click range selects 3', ui.selected.length === 3, ui.selected.join(','));
  check('desktop: composite preview rendered', ui.previewSrcLen > 100);
  check('desktop: Save Image enabled with a live preview', ui.saveMergedEnabled);
  await items.nth(1).click({ modifiers: ['Control'] });
  await page.waitForTimeout(120);
  ui = await readUi(page);
  check('desktop: ctrl-click toggles one off (2 left)', ui.selected.length === 2, ui.selected.join(','));

  // 3. Extract selected -> browser download
  await items.nth(0).click(); // single selection
  await page.waitForTimeout(120);
  const dl1 = page.waitForEvent('download');
  await page.click('#btn-extract-sel');
  const download1 = await dl1;
  check('desktop: Extract Selected downloads a PNG', download1.suggestedFilename().endsWith('.png'), download1.suggestedFilename());

  // 4. Enter edit mode via the toggle
  await page.click('#mode-modify');
  await page.waitForTimeout(200);
  ui = await readUi(page);
  check('desktop: mode toggle enters edit mode', ui.mode === 'modify' && ui.modifyControlsVisible);
  check('desktop: repack row + Save As appear in edit mode', ui.repackRowVisible && ui.saveVisible);
  check('desktop: Save As chevron is visible', await page.locator('#btn-save-menu').isVisible());
  await page.click('#btn-save-menu');
  await page.waitForTimeout(80);
  ui = await readUi(page);
  check('desktop: chevron opens Copy .skel menu', ui.saveMenuOpen);
  await page.click('#sidebar-head');
  await page.waitForTimeout(80);
  ui = await readUi(page);
  check('desktop: click away closes save menu', !ui.saveMenuOpen);
  check('desktop: multi-page switcher visible, Page 1 / 2, prev disabled', ui.switcherVisible && ui.indicator === 'Page 1 / 2' && ui.prevDisabled && !ui.nextDisabled, ui.indicator);

  // 5. Page switcher next/prev
  const srcBefore = await page.evaluate(() => document.getElementById('preview-img').src.length);
  await page.click('#page-next');
  await page.waitForTimeout(200);
  ui = await readUi(page);
  const srcAfter = await page.evaluate(() => document.getElementById('preview-img').src.length);
  check('desktop: next -> Page 2 / 2, next disabled, preview changed', ui.indicator === 'Page 2 / 2' && ui.nextDisabled && srcAfter !== srcBefore, ui.indicator);
  await page.click('#page-prev');
  await page.waitForTimeout(200);
  ui = await readUi(page);
  check('desktop: prev -> back to Page 1 / 2', ui.indicator === 'Page 1 / 2');

  // 6. Mod apply through the REAL file chooser (ReplaceSelected)
  await items.nth(0).click(); // select "alpha" (page1)
  await page.waitForTimeout(120);
  const chooser1 = page.waitForEvent('filechooser');
  await page.click('#btn-modify-sel');
  await (await chooser1).setFiles({ name: 'mod1.png', mimeType: 'image/png', buffer: MOD_PNG });
  await page.waitForTimeout(400);
  ui = await readUi(page);
  check('desktop: mod apply #1 enables Save As + Reset', ui.saveEnabled && ui.resetEnabled);

  // Reset: confirm dialog -> pristine edit view (Save As / Reset disabled again)
  await page.click('#btn-reset-mod');
  await page.waitForTimeout(150);
  ui = await readUi(page);
  check('desktop: Reset asks for confirmation', ui.modalVisible);
  await page.click('#btn-modal-confirm');
  await page.waitForTimeout(300);
  ui = await readUi(page);
  check('desktop: Reset restores pristine edit view', ui.mode === 'modify' && !ui.saveEnabled && !ui.resetEnabled);

  // Re-apply mod #1 so the sequential test below still starts from one batch
  await items.nth(0).click();
  await page.waitForTimeout(120);
  const chooser1b = page.waitForEvent('filechooser');
  await page.click('#btn-modify-sel');
  await (await chooser1b).setFiles({ name: 'mod1.png', mimeType: 'image/png', buffer: MOD_PNG });
  await page.waitForTimeout(400);

  // Sequential second mod on a different region (UI-level rebuild-from-pristine regression)
  await items.nth(1).click(); // "beta"
  await page.waitForTimeout(120);
  const chooser2 = page.waitForEvent('filechooser');
  await page.click('#btn-modify-sel');
  await (await chooser2).setFiles({ name: 'mod2.png', mimeType: 'image/png', buffer: MOD_PNG });
  await page.waitForTimeout(400);
  const twoBatches = await page.evaluate(async () => {
    const { AtlasAPI } = await import('./js/atlas-api.js');
    return AtlasAPI.has_pending_modifications();
  });
  check('desktop: sequential mod apply #2 accepted, pending mods true', twoBatches);
  check('desktop: no page errors after sequential mods', errors.length === 0, errors.join('; '));

  // 7. Repack toggle on/off without a reload (the checkbox input is visually
  // hidden behind the custom switch — click its label like a user would; the
  // toggle lives in the right panel's #repack-options row, like the Python app)
  const navBefore = await page.evaluate(() => performance.getEntriesByType('navigation').length);
  await page.click('#repack-options .toggle-label');
  await page.waitForTimeout(500);
  await page.click('#repack-options .toggle-label');
  await page.waitForTimeout(500);
  const navAfter = await page.evaluate(() => performance.getEntriesByType('navigation').length);
  check('desktop: repack toggle on/off, no reload', navBefore === navAfter && errors.length === 0);

  // 8. Unsaved-changes guard: cancel keeps edit mode, confirm exits
  await page.click('#mode-extract');
  await page.waitForTimeout(150);
  ui = await readUi(page);
  check('desktop: guard modal appears on exit with pending mods', ui.modalVisible);
  await page.click('#btn-modal-cancel');
  await page.waitForTimeout(150);
  ui = await readUi(page);
  check('desktop: cancel keeps edit mode', !ui.modalVisible && ui.mode === 'modify');
  await page.click('#mode-extract');
  await page.waitForTimeout(150);
  await page.click('#btn-modal-confirm');
  await page.waitForTimeout(200);
  ui = await readUi(page);
  check('desktop: confirm discards and returns to view mode', ui.mode === 'extract' && !ui.modifyControlsVisible);

  // 9. Missing-page dialog: load an atlas with no image -> cancel aborts cleanly
  const missingResult = page.evaluate(async () => {
    const { AtlasAPI } = await import('./js/atlas-api.js');
    const atlasFile = new File(['orphan.png\nsize: 10, 10\nlone\nbounds: 0, 0, 5, 5\n'], 'orphan.atlas', { type: 'text/plain' });
    return AtlasAPI.load_atlas_from_file(atlasFile, {});
  });
  await page.waitForTimeout(300);
  ui = await readUi(page);
  check('desktop: missing-page dialog appears', ui.missingDialog);
  await page.click('.missing-images-buttons .btn-secondary'); // Cancel
  const aborted = await missingResult;
  await page.waitForTimeout(200);
  ui = await readUi(page);
  check('desktop: cancel aborts load, previous atlas intact', aborted === false && ui.items === 5, `items=${ui.items}`);

  // 10. Save As... -> zip download; saved state clears the guard
  await page.click('#mode-modify');
  await page.waitForTimeout(200);
  await items.nth(0).click();
  await page.waitForTimeout(120);
  const chooser3 = page.waitForEvent('filechooser');
  await page.click('#btn-modify-sel');
  await (await chooser3).setFiles({ name: 'mod3.png', mimeType: 'image/png', buffer: MOD_PNG });
  await page.waitForTimeout(400);
  const dl2 = page.waitForEvent('download');
  await page.click('#btn-save-mod');
  const download2 = await dl2;
  check('desktop: Save As downloads a zip', download2.suggestedFilename().endsWith('.zip'), download2.suggestedFilename());
  await page.waitForTimeout(300);
  const afterSave = await page.evaluate(() => ({
    status: document.getElementById('status-text').innerText,
    toast: [...document.querySelectorAll('#toast-container .toast')].map((el) => el.innerText),
  }));
  const toastText = afterSave.toast.join('\n');
  check('desktop: save outcome is toasted', /Saved/i.test(toastText), toastText);
  check(
    'desktop: status bar does not echo the toast',
    afterSave.status && toastText && afterSave.status !== toastText && !/^Saved\b/i.test(afterSave.status),
    `status=${afterSave.status}`,
  );
  await page.click('#mode-extract');
  await page.waitForTimeout(150);
  ui = await readUi(page);
  check('desktop: after save, exit needs no guard', !ui.modalVisible && ui.mode === 'extract');

  check('desktop: zero page errors across all flows', errors.length === 0, errors.join('; '));
  await ctx.close();
}

// ─── Touch pass (condensed) ───────────────────────────────────────────────────
{
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, acceptDownloads: true,
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(URL_ROOT, { waitUntil: 'networkidle' });

  const loaded = await loadFixtureAtlas(page);
  check('touch: atlas loads', loaded.ok, '');

  // Tap = toggle-select per the touch input model
  await page.locator('.region-item').nth(0).tap();
  await page.waitForTimeout(350); // tap-vs-longpress settle + preview debounce
  let ui = await readUi(page);
  check('touch: tap selects a region', ui.selected.length === 1, ui.selected.join(','));

  await page.locator('#mode-modify').tap();
  await page.waitForTimeout(250);
  ui = await readUi(page);
  check('touch: edit mode + page switcher on stacked layout', ui.mode === 'modify' && ui.switcherVisible && ui.indicator === 'Page 1 / 2', ui.indicator);
  check('touch: Save As split is visible', ui.saveVisible);
  await page.locator('#btn-save-menu').tap();
  await page.waitForTimeout(80);
  ui = await readUi(page);
  check('touch: chevron opens Copy .skel menu', ui.saveMenuOpen);
  await page.locator('#sidebar-head').tap();
  await page.waitForTimeout(80);
  ui = await readUi(page);
  check('touch: tap away closes save menu', !ui.saveMenuOpen);

  await page.locator('#page-next').tap();
  await page.waitForTimeout(250);
  ui = await readUi(page);
  check('touch: page switcher works by tap', ui.indicator === 'Page 2 / 2');

  // Guard: apply a mod first so the guard has something to protect.
  // (Touch tap = TOGGLE-select: alpha is already selected from the earlier
  // tap, so tap a different region — re-tapping alpha would empty the
  // selection and ReplaceSelected would refuse to open the chooser.)
  await page.locator('.region-item').nth(1).tap();
  await page.waitForTimeout(350);
  const chooser = page.waitForEvent('filechooser');
  await page.locator('#btn-modify-sel').tap();
  await (await chooser).setFiles({ name: 'mod.png', mimeType: 'image/png', buffer: MOD_PNG });
  await page.waitForTimeout(400);
  await page.locator('#mode-extract').tap();
  await page.waitForTimeout(150);
  ui = await readUi(page);
  check('touch: guard modal on exit', ui.modalVisible);
  await page.locator('#btn-modal-confirm').tap();
  await page.waitForTimeout(200);
  ui = await readUi(page);
  check('touch: confirm exits to view mode', ui.mode === 'extract');

  check('touch: zero page errors', errors.length === 0, errors.join('; '));
  await ctx.close();
}

// ─── Task 8: Advance Mode toggle, multi-page guard, #chk-repack force/release ──
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto(URL_ROOT, { waitUntil: 'networkidle' });

  const single = await loadSinglePageFixtureAtlas(page);
  check('task8: single-page fixture loaded', single.ok);
  const caretVisibleAtLoadSingle = await page.isVisible('#mode-edit-caret');
  check('Advance Mode caret is visible at load time for single-page, before entering edit mode', caretVisibleAtLoadSingle === true);
  await page.click('#mode-modify');
  await page.waitForTimeout(150);
  const caretVisibleSingle = await page.isVisible('#mode-edit-caret');
  check('Advance Mode entry point is visible for a single-page atlas', caretVisibleSingle === true);

  await page.click('#mode-edit-caret');
  // #chk-advance-mode is display:none (styled as a .toggle-switch); the real
  // user control is the wrapping label. Clicking it toggles the checkbox and
  // fires the change handler that reveals the toolbar.
  await page.click('#advance-mode-row');
  const toolbarVisible = await page.isVisible('#advance-toolbar');
  check('Advance Mode toolbar shows once the checkbox is toggled on', toolbarVisible === true);

  const multi = await loadFixtureAtlas(page);
  check('task8: multi-page fixture (re)loaded', multi.ok);
  const caretHiddenAtLoadMulti = await page.isVisible('#mode-edit-caret');
  check('Advance Mode caret is hidden at load time for multi-page, before entering edit mode', caretHiddenAtLoadMulti === false);
  await page.click('#mode-extract'); // exit back to view mode before re-entering, matching how
                                      // a real user would switch atlases between edit sessions
  await page.waitForTimeout(100);
  await page.click('#mode-modify');
  await page.waitForTimeout(150);
  const caretVisibleMulti = await page.isVisible('#mode-edit-caret');
  check('Advance Mode entry point is hidden entirely for a multi-page atlas', caretVisibleMulti === false);

  // add_region() is only supported on single-page atlases, so switch back to the
  // single-page fixture before exercising the structural add.
  await loadSinglePageFixtureAtlas(page);
  const resetResult = await page.evaluate(async () => {
    const { AtlasAPI } = await import('./js/atlas-api.js');
    const solid = (w, h, rgba) => {
      const c = document.createElement('canvas'); c.width = w; c.height = h;
      c.getContext('2d').fillStyle = `rgba(${rgba.join(',')})`; c.getContext('2d').fillRect(0, 0, w, h);
      return c;
    };
    const toFile = (canvas, name) => new Promise((res) =>
      canvas.toBlob((b) => res(new File([b], name, { type: 'image/png' })), 'image/png'));
    const beforeAdd = AtlasAPI.get_region_names().length;
    const helmetFile = await toFile(solid(6, 6, [10, 200, 10, 255]), 'helmet.png');
    await AtlasAPI.add_region(helmetFile, 'testHelmet');
    const afterAdd = AtlasAPI.get_region_names().length;
    return { beforeAdd, afterAdd };
  });
  check('task8: add_region() via direct API call added one region (setup for the reset-refresh check)', resetResult.afterAdd === resetResult.beforeAdd + 1);
  // No UI path to trigger Reset here yet (Tasks 9-11 build the confirm handlers) -- reload the
  // pristine single-page fixture instead of driving the Reset button, to confirm loadRegions()
  // (which resetModify()/exitEditMode() now both call via refreshStructuralUi) really does drop
  // the structurally-added region back out of the effective list once the session is replaced.
  const afterReload = await loadSinglePageFixtureAtlas(page);
  check('task8: reloading drops the structurally-added region back out (effective model resets with the session)',
    afterReload.ok === true && afterReload.names.length === resetResult.beforeAdd && !afterReload.names.some((r) => r.key === 'testHelmet'));

  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  check('task8: zero page errors', errors.length === 0, errors.join('; '));
  await ctx.close();
}

// ─── Task 9: Rename flow ──────────────────────────────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(URL_ROOT, { waitUntil: 'networkidle' });

  const loaded = await loadSinglePageFixtureAtlas(page);
  check('task9: single-page fixture loaded', loaded.ok);
  await page.click('#mode-modify');
  await page.waitForTimeout(150);

  await page.locator('.region-item').nth(0).click();
  await page.click('#mode-edit-caret');
  await page.click('#advance-mode-row');
  await page.click('#btn-rename-region');
  await page.fill('#rename-name-input', 'forearm');
  const saveEnabled = await page.isEnabled('#rename-confirm-btn');
  check('valid name enables Save', saveEnabled === true);
  await page.click('#rename-confirm-btn');
  // The confirm handler runs rename_region (a forced full repack) before the
  // sidebar rebuilds — that repack takes well over 150ms, so poll for the
  // renamed label instead of a fixed sleep (a longer fixed wait is still flaky).
  await page.waitForFunction(() => {
    const el = document.querySelector('.region-item.selected');
    return !!el && el.innerText.includes('forearm');
  }, undefined, { timeout: 5000 });
  const selectedLabel = await page.evaluate(() =>
    document.querySelector('.region-item.selected')?.innerText);
  check('sidebar shows the new label immediately', !!selectedLabel && selectedLabel.includes('forearm'), selectedLabel);

  // invalid name shows inline error, keeps Save disabled
  await page.locator('.region-item').nth(0).click();
  await page.click('#btn-rename-region');
  await page.fill('#rename-name-input', '');
  const errorVisible = await page.isVisible('#rename-name-error');
  const saveDisabled = await page.isDisabled('#rename-confirm-btn');
  check('blank name shows inline error', errorVisible === true);
  check('blank name keeps Save disabled', saveDisabled === true);
  await page.click('#rename-cancel-btn');

  // >1 selected shows a toast and does nothing else
  await page.locator('.region-item').nth(0).click();
  await page.locator('.region-item').nth(1).click({ modifiers: ['Control'] });
  await page.click('#btn-rename-region');
  await page.waitForTimeout(150);
  const toastText = await page.evaluate(() =>
    [...document.querySelectorAll('#toast-container .toast')].map((el) => el.innerText).join('\n'));
  check('multi-select shows the exact spec toast copy', toastText.includes('Select a single region to rename.'), toastText);

  check('task9: zero page errors', errors.length === 0, errors.join('; '));
  await ctx.close();
}

await browser.close();
server.close();
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
