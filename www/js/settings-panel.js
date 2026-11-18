// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
import { AtlasAPI } from './atlas-api.js';
import { HostError } from './engine-client.js';
import { refreshPanelSplit } from './panel-resizer.js';
import { isTouchDevice, isPywebviewDesktop } from './platform.js';
import { isBusy, noteBusyConflict } from './busy-overlay.js';

const PACKERS = [
  ['shelf', 'Shelf', 'Rows, tallest first. Fastest.'],
  ['rectangles', 'Rectangles', 'Fills the gaps between rectangles.'],
  ['polygons', 'Polygons', 'Fills gaps using mesh outlines.'],
  ['rectangles_in_place', 'Rectangles In-Place', 'Keeps the layout when edits fit.'],
  ['polygons_in_place', 'Polygons In-Place', 'Keeps the layout, judged by mesh outline.'],
];
const HINTS = {
  meshAware: "Clears pixels outside each region's mesh outline before packing. Spine never draws them, so regions can sit closer. Needs a .skel.",
  splitComposite: 'When one image replaces several regions, cuts it back into one piece per region: the exact inverse of Forced Resizing.<br>Off: those regions share the whole image.<br>In-Place packers always split.',
  bleed: "Gives transparent pixels next to each image that image's colour (alpha stays 0), as Spine's packer does, so filtering draws no dark edges. Premultiplied pages are left as they are.",
  padding: "Empty pixels kept between images, so a neighbour never shows at a region's edge when the texture is filtered. Default 2.",
  keepLayout: "If a page's only edits are new regions (and renames), existing regions stay where they are and the new ones go into free space; the page grows only if they don't fit.<br>Off: every edited page is repacked.",
};
const LOCK_REASONS = {
  meshAware: v => v.packer.startsWith('polygons') ? 'Polygons packers always pack by mesh outline.' : 'Needs a usable .skel file.',
  splitComposite: () => 'In-Place packers always split composites.',
  keepLayout: () => 'In-Place packers always keep the layout.',
  bleed: () => 'Pages with premultiplied alpha never get bleed.',
};

// `settings`: the engine's applied state. `draft`: the requested values being
// edited (null when the panel shows the applied state); `view`: the engine's
// preview of that draft, so locks and fallbacks come from the one place
// that applies them (AppHost._settings).
let settings = null, draft = null, view = null, fallback = null, timer = null;
let previewSeq = 0;
const openHints = new Set();
export function resetSettingsFallback() { fallback = null; }
const panel = () => document.getElementById('pack-panel');
const chip = () => document.getElementById('btn-pack-chip');
const shown = () => view || settings;
const inner = () => panel()?.querySelector('.pack-inner');
const isOpen = () => !!panel() && !panel().classList.contains('hidden');
// Phones and narrow windows show the panel as a full modal.
const isModal = () => innerWidth <= 600 || (isTouchDevice() && !isPywebviewDesktop());

function changedKeys() {
  if (!draft || !settings?.requested) return [];
  return Object.keys(draft).filter(key => draft[key] !== settings.requested[key]);
}

/** Close at once and drop any draft (mode switches, loads). */
export function closeSettingsPanel() {
  clearTimeout(timer);
  draft = null; view = null;
  panel()?.classList.add('hidden');
  panel()?.classList.remove('pack-attention');
  syncBackdrop();
  chip()?.setAttribute('aria-expanded', 'false');
  if (settings) render();
}

/** Close unless there are unsaved changes: then only Cancel or Save closes
 *  the panel, and the attempt flashes those buttons instead. */
function requestClose() {
  if (!changedKeys().length) { closeSettingsPanel(); return; }
  flashPanel();
}
function flashPanel() {
  const p = panel();
  if (!p) return;
  p.classList.remove('pack-attention');
  void p.offsetWidth;  // restart the animation on repeated attempts
  p.classList.add('pack-attention');
  // Drop the class when done: left on, the animation replays whenever the
  // hidden panel is shown again (display:none restarts CSS animations).
  p.addEventListener('animationend', () => p.classList.remove('pack-attention'), {once: true});
}
/** True while unsaved changes hold the desktop panel open. */
const holding = () => isOpen() && !isModal() && changedKeys().length > 0;

/** The backdrop dims the page behind the phone modal. */
function syncBackdrop() {
  document.getElementById('pack-backdrop')?.classList.toggle('hidden', !(isOpen() && isModal()));
}

function positionPanel() {
  const p = panel();
  syncBackdrop();
  if (!p || p.classList.contains('hidden')) return;
  const modal = isModal();
  p.classList.toggle('is-touch-modal', modal);
  p.setAttribute('aria-modal', String(modal));
  if (modal) {
    p.style.removeProperty('left');
    p.style.removeProperty('top');
    if (!p.dataset.opened) { p.scrollTop = 0; p.dataset.opened = '1'; }
  } else {
    const r = chip().getBoundingClientRect();
    p.style.left = `${Math.max(8, Math.min(r.left, innerWidth - p.offsetWidth - 8))}px`;
    p.style.top = `${Math.max(8, Math.min(r.bottom + 6, innerHeight - p.offsetHeight - 8))}px`;
  }
}

/** Change one requested value in the draft; nothing repacks until Save. */
async function setDraft(key, value) {
  clearTimeout(timer);
  const next = {...(draft || settings.requested), [key]: value};
  const seq = ++previewSeq;
  try {
    const preview = await AtlasAPI.preview_settings(next);
    if (seq !== previewSeq || !isOpen()) return;
    draft = next; view = preview;
  } catch (e) {
    if (!(e instanceof HostError)) throw e;
    await window.showAlert(e.message, 'Cannot use setting');
  }
  render();
}

async function save() {
  if (isBusy()) { noteBusyConflict(); return; }
  const keys = changedKeys();
  if (!keys.length) { closeSettingsPanel(); return; }
  const partial = Object.fromEntries(keys.map(key => [key, draft[key]]));
  try {
    const result = await AtlasAPI.set_settings(partial);
    if (result?.state && !result.unchanged) await (await import('./modify-mode.js')).onModPreviewReceived(result);
    closeSettingsPanel();
  } catch (e) {
    if (!(e instanceof HostError)) throw e;
    await window.showAlert(e.message, 'Cannot apply setting');
  }
}

function infoButton(key, label) {
  const open = openHints.has(key);
  return `<button type="button" class="info-btn" data-hint="${key}" aria-expanded="${open}" aria-label="About ${label}">${INFO}</button>`;
}
function hint(key, extraClass = '') {
  return `<p class="hint ${extraClass}" id="hint-${key}" ${openHints.has(key) ? '' : 'hidden'}>${HINTS[key]}</p>`;
}
function toggle(value, key, label) {
  const locked = value[`${key}Locked`];
  const reason = locked ? ` data-help="${LOCK_REASONS[key](value)}"` : '';
  return `<div class="opt"><div class="opt-line"><label class="toggle-label ${locked ? 'is-locked' : ''}"${reason}><input type="checkbox" data-setting="${key}" ${value[key] ? 'checked' : ''} ${locked ? 'disabled' : ''}><span class="toggle-switch"></span><span>${label}</span></label>${infoButton(key, label)}</div>${hint(key)}</div>`;
}

export function renderSettings(value) {
  if (!value) return;
  settings = value;
  if (value.fallbackFrom && fallback !== value.fallbackFrom) {
    const inplace = value.fallbackFrom === 'polygons_in_place';
    window.showToast(inplace ? 'Polygons In-Place need a .skel file; using Rectangles In-Place.' : 'Polygons need a .skel file; using Rectangles.', 'info');
  }
  fallback = value.fallbackFrom;
  // The engine changed underneath an open draft (e.g. a .skel arrived): re-preview it.
  if (draft) {
    const seq = ++previewSeq;
    AtlasAPI.preview_settings(draft).then(preview => {
      if (seq === previewSeq && draft) { view = preview; render(); }
    }).catch(() => {});
  }
  render();
}

function render() {
  if (!inner() || !settings) return;
  const value = shown();
  const applied = settings;
  chip().innerHTML = `${GEAR}<span class="chip-label">Settings</span><span class="chip-sep"></span><span class="chip-value">${PACKERS.find(p => p[0] === applied.packer)[1]} · ${applied.padding}px</span> ▾`;
  const focusedNumber = document.activeElement?.id === 'p-num' ? document.activeElement : null;
  const dirty = changedKeys().length > 0;
  const content = document.createElement('div');
  const packers = PACKERS.map(([key,label,description]) => {
    const disabled = key.startsWith('polygons') && !value.polygonsAllowed;
    return `<button type="button" class="pk-item ${key === value.packer ? 'selected' : ''}" data-packer="${key}" ${disabled ? 'disabled' : ''}><span class="pk-check">${key === value.packer ? CHECK : ''}</span><span>${label}<small>${disabled ? 'Needs a .skel file' : description}</small></span></button>`;
  }).join('');
  const alphaSection = value.alphaVisible ? `<div class="sec"><p class="pack-heading">Image alpha</p><select id="s-alpha"><option value="straight">Straight alpha</option><option value="premultiplied">Premultiplied alpha</option></select></div>` : '';
  // Two columns on a wide panel: the packer list, then the options by topic.
  content.innerHTML = `<div class="pack-cols">
    <div class="pack-col"><div class="sec"><div class="sec-head"><p class="pack-heading">Packer</p><button type="button" class="info-btn" id="btn-packer-info" aria-label="About the packers">${INFO}</button></div><div class="pk-menu">${packers}</div></div></div>
    <div class="pack-col">
      <div class="sec"><p class="pack-heading">Regions</p>${toggle(value, 'meshAware', 'Mesh-Aware')}${toggle(value, 'splitComposite', 'Split Composite')}</div>
      <div class="sec"><p class="pack-heading">Spacing</p><div class="sec-head"><span class="pack-sub">Padding (px)</span>${infoButton('padding', 'Padding')}<button id="btn-pad-reset" type="button" data-help="Reset to default (2)" aria-label="Reset to default (2)">${RESET}</button></div>${hint('padding', 'hint-flush')}<div class="padding-controls"><input type="range" id="p-range" min="1" max="100" value="${Math.min(100,value.padding)}"><input type="number" id="p-num" min="1" max="256" step="1" value="${value.padding}"></div><div class="opt-gap"></div>${toggle(value, 'bleed', 'Bleed')}</div>
      <div class="sec"><p class="pack-heading">Layout</p>${toggle(value, 'keepLayout', 'Keep layout when only adding')}</div>
      ${alphaSection}
    </div>
  </div>
  <div class="pack-foot"><span class="pack-dirty" ${dirty ? '' : 'hidden'}>Unsaved changes</span><button id="pack-cancel" type="button" class="btn-secondary">Cancel</button><button id="pack-save" type="button" class="btn-primary" ${dirty ? '' : 'disabled'}>Save</button></div>`;
  const oldCols = inner().querySelector('.pack-cols');
  if (focusedNumber && oldCols) {
    // Keep the focused padding section in the live DOM so neither focus
    // nor the native number input's caret/draft is disturbed by a re-render.
    const newCols = content.querySelector('.pack-cols');
    const oldChildren = [...oldCols.children];
    const newChildren = [...newCols.children];
    newChildren.forEach((child, index) => {
      const old = oldChildren[index];
      if (old?.contains(focusedNumber)) return;
      if (old) old.replaceWith(child); else oldCols.append(child);
    });
    oldChildren.slice(newChildren.length).forEach(child => {
      if (child.isConnected && !child.contains(focusedNumber)) child.remove();
    });
    inner().querySelector('.pack-foot').replaceWith(content.querySelector('.pack-foot'));
  } else inner().replaceChildren(...content.children);
  panel().querySelectorAll('[data-packer]').forEach(b => b.onclick = () => setDraft('packer', b.dataset.packer));
  panel().querySelectorAll('[data-setting]').forEach(c => c.onchange = () => setDraft(c.dataset.setting, c.checked));
  panel().querySelectorAll('[data-hint]').forEach(b => b.onclick = () => {
    const key = b.dataset.hint;
    const open = !openHints.has(key);
    if (open) openHints.add(key); else openHints.delete(key);
    b.setAttribute('aria-expanded', String(open));
    document.getElementById(`hint-${key}`).hidden = !open;
    positionPanel();
  });
  document.getElementById('btn-packer-info').onclick = showPackerInfo;
  const range = document.getElementById('p-range');
  const number = document.getElementById('p-num');
  range.oninput = () => { number.value = range.value; };
  range.onchange = () => setDraft('padding', Number(range.value));
  number.oninput = () => {
    clearTimeout(timer);
    if (number.value === '' || !number.checkValidity()) return;
    timer = setTimeout(() => setDraft('padding', Number(number.value)), 300);
  };
  number.onblur = () => {
    if (number.value === '' || !number.checkValidity()) number.value = String(shown().padding);
  };
  document.getElementById('btn-pad-reset').onclick = () => setDraft('padding', 2);
  const alpha = document.getElementById('s-alpha');
  if (alpha) { alpha.value = value.alpha; alpha.onchange = () => setDraft('alpha', alpha.value); }
  document.getElementById('pack-cancel').onclick = closeSettingsPanel;
  document.getElementById('pack-save').onclick = save;
  positionPanel();
  refreshPanelSplit();
}

function packerInfo() {
  return document.getElementById('packer-info-overlay');
}
function closePackerInfo() {
  packerInfo()?.classList.add('hidden');
}
function showPackerInfo() {
  let overlay = packerInfo();
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'packer-info-overlay';
    overlay.innerHTML = `<div id="packer-info" role="dialog" aria-modal="true" aria-labelledby="packer-info-title">
      <h3 id="packer-info-title">Packers</h3>
      <p class="pi-lead">A packer decides where regions go on the atlas page when you save edits.</p>
      <div class="pi-legend"><span><i class="pi-region"></i>region</span><span><i class="pi-same"></i>unchanged</span><span><i class="pi-edit"></i>edited</span><span><i class="pi-add"></i>added</span></div>
      <div class="pi-list">${PACKER_INFO.map(([title, body, svg, skel]) => `<div class="pi-card"><svg viewBox="0 0 170 112" aria-hidden="true"><rect x="1" y="1" width="168" height="110" fill="#1e1e1e" stroke="#555"/>${svg}</svg><div><h4>${title}${skel ? ' <span class="pi-pill">needs .skel</span>' : ''}</h4><p>${body}</p></div></div>`).join('')}</div>
      <div class="modal-buttons"><button type="button" class="btn-primary" id="packer-info-ok">OK</button></div>
    </div>`;
    document.body.append(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) closePackerInfo(); });
    document.getElementById('packer-info-ok').onclick = closePackerInfo;
  }
  overlay.classList.remove('hidden');
  overlay.scrollTop = 0;
  // preventScroll: focusing OK must not scroll a tall modal to its end.
  document.getElementById('packer-info-ok').focus({preventScroll: true});
}

export function initSettingsPanel() {
  const backdrop = document.createElement('div'); backdrop.id = 'pack-backdrop'; backdrop.className = 'hidden'; document.body.append(backdrop);
  const p = document.createElement('div'); p.id = 'pack-panel'; p.className = 'hidden'; p.setAttribute('role','dialog'); p.setAttribute('aria-label','Settings'); document.body.append(p);
  const card = document.createElement('div'); card.className = 'pack-inner'; p.append(card);
  chip().onclick = () => {
    if (!settings) return;
    if (isOpen()) { requestClose(); return; }
    draft = null; view = null; openHints.clear(); render();
    delete p.dataset.opened;
    p.classList.remove('hidden'); chip().setAttribute('aria-expanded','true'); positionPanel();
  };
  // A clean panel closes on an outside click. The event path, not contains():
  // a re-render can detach the clicked node.
  document.addEventListener('click', e => {
    if (!isOpen()) return;
    const path = e.composedPath();
    if (path.includes(p) || path.includes(chip())) return;
    if (e.target.closest?.('#modal-overlay, #busy-overlay, #packer-info-overlay, .toast, #toast-container, #update-toast')) return;
    requestClose();
  }, true);
  // While held open, presses outside the panel are swallowed before anything
  // else sees them (window capture runs first), so nothing underneath acts or
  // closes the panel; hover still reaches the page (tooltips, hover styles).
  const blockOutside = e => {
    if (!holding() || e.composedPath().includes(p)) return;
    if (e.target.closest?.('#modal-overlay, #busy-overlay, #packer-info-overlay, .toast, #toast-container, #update-toast')) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    if (e.type === 'pointerdown') flashPanel();
  };
  for (const type of ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click', 'auxclick', 'dblclick', 'contextmenu', 'touchstart', 'touchend']) {
    window.addEventListener(type, blockOutside, {capture: true, passive: false});
  }
  window.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if (packerInfo() && !packerInfo().classList.contains('hidden')) { closePackerInfo(); return; }
    if (isOpen()) requestClose();
  });
  window.addEventListener('resize', positionPanel);
  renderSettings(AtlasAPI.get_settings());
}

const SHELF = [[6,6,44,38],[54,6,36,32],[94,6,30,26],[128,6,34,22],[6,48,40,28],[50,48,48,24],[102,48,26,20],[132,48,22,18],[6,80,56,20],[66,80,30,16]];
const RECTS = [[6,6,44,38],[54,6,36,32],[94,6,30,26],[128,6,34,22],[128,32,26,20],[94,36,30,18],[54,42,36,24],[6,48,44,28],[94,58,40,16],[138,56,24,20],[6,80,56,20],[66,70,24,18],[94,78,30,16]];
const rects = (list, fill) => list.map(([x,y,w,h]) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${fill}"/>`).join('');
const POLY = '<path d="M8 8 L52 8 L52 22 L30 44 L8 44 Z"/><path d="M34 46 L56 24 L56 60 L34 60 Z"/><path d="M60 8 L96 8 L84 40 L60 40 Z"/><path d="M88 42 L100 10 L132 10 L132 42 Z"/><circle cx="150" cy="26" r="14"/><path d="M8 50 L30 50 L30 96 L8 96 Z"/><path d="M60 46 L110 46 L110 62 L60 62 Z"/><path d="M114 48 L160 48 L137 92 Z"/><path d="M36 66 L106 66 L90 100 L36 100 Z"/>';
const PACKER_INFO = [
  ['Shelf', 'Lays regions out in rows, tallest first. The fastest packer; leaves empty space above shorter regions in a row.',
    rects(SHELF, '#3d7fb8') + '<g stroke="#888" stroke-dasharray="3 3"><line x1="4" y1="46" x2="166" y2="46"/><line x1="4" y1="78" x2="166" y2="78"/></g>'],
  ['Rectangles', 'Puts each region into the first gap its rectangle fits, including gaps between regions already placed. Smaller pages than Shelf.',
    rects(RECTS, '#3d7fb8')],
  ['Polygons', "Packs by each region's mesh outline instead of its rectangle, so shapes nest into each other's empty corners. The smallest pages. A region's rectangle may then hold parts of its neighbours (dashed); Spine draws only inside the mesh.",
    `<g fill="#3d7fb8">${POLY}</g><rect x="34" y="24" width="22" height="36" fill="none" stroke="#9cc6ea" stroke-dasharray="2 2" opacity=".8"/>`, true],
  ['Rectangles In-Place', "Keeps the page's layout when every edit fits in place: a replaced image stays where it was, a removed region leaves a gap, and added regions go into free space. A replacement fits when it is the size Extract gives and draws nothing outside the region. Otherwise the page is repacked with Rectangles.",
    rects([[6,6,44,38],[94,6,30,26],[128,6,34,22],[6,48,40,28],[102,48,26,20],[6,80,56,20]], '#5a5a5a') + rects([[54,6,36,32],[50,48,48,24]], '#d08a2e')
      + '<rect x="132" y="48" width="22" height="18" fill="none" stroke="#777" stroke-dasharray="3 2"/>' + rects([[70,80,40,22]], '#4caf50') + '<text x="90" y="95" font-size="12" fill="#fff" text-anchor="middle">+</text>'],
  ['Polygons In-Place', "Keeps the page's layout when every edit fits in place, judging each edit by the region's mesh outline: pixels outside it are ignored. Replaced images stay put, removed regions leave a gap, added regions go into free space. Otherwise the page is repacked with Polygons.",
    '<g fill="#5a5a5a"><path d="M8 8 L52 8 L52 22 L30 44 L8 44 Z"/><path d="M88 42 L100 10 L132 10 L132 42 Z"/><circle cx="150" cy="26" r="14"/><path d="M8 50 L30 50 L30 96 L8 96 Z"/><path d="M36 66 L106 66 L90 100 L36 100 Z"/></g><path d="M60 8 L96 8 L84 40 L60 40 Z" fill="#d08a2e"/><path d="M60 46 L110 46 L110 62 L60 62 Z" fill="#d08a2e"/><path d="M114 48 L160 48 L137 92 Z" fill="#4caf50"/><text x="137" y="66" font-size="12" fill="#fff" text-anchor="middle">+</text>', true],
];
const INFO = '<svg xmlns="http://www.w3.org/2000/svg" height="15" width="15" viewBox="0 -960 960 960" aria-hidden="true"><path fill="currentColor" d="M440-280h80v-240h-80v240Zm40-320q17 0 28.5-11.5T520-640q0-17-11.5-28.5T480-680q-17 0-28.5 11.5T440-640q0 17 11.5 28.5T480-600Zm0 520q-83 0-156-31.5T197-197q-54-54-85.5-127T80-480q0-83 31.5-156T197-763q54-54 127-85.5T480-880q83 0 156 31.5T763-763q54 54 85.5 127T880-480q0 83-31.5 156T763-197q-54 54-127 85.5T480-80Zm0-80q134 0 227-93t93-227q0-134-93-227t-227-93q-134 0-227 93t-93 227q0 134 93 227t227 93Z"/></svg>';
const CHECK = '<svg xmlns="http://www.w3.org/2000/svg" height="16" width="16" viewBox="0 -960 960 960" aria-hidden="true"><path fill="currentColor" d="M382-240 154-468l57-57 171 171 367-367 57 57-424 424Z"/></svg>';
const RESET = '<svg xmlns="http://www.w3.org/2000/svg" height="14" width="14" viewBox="0 -960 960 960" aria-hidden="true"><path fill="currentColor" d="M480-160q-134 0-227-93t-93-227q0-134 93-227t227-93q69 0 132 28.5T720-690v-110h80v280H520v-80h168q-32-56-87.5-88T480-720q-100 0-170 70t-70 170q0 100 70 170t170 70q77 0 139-44t87-116h84q-28 106-114 173t-196 67Z"/></svg>';
const GEAR = '<svg xmlns="http://www.w3.org/2000/svg" height="18" viewBox="0 -960 960 960" width="18"><path fill="currentColor" d="M480-336q60 0 102-42t42-102q0-60-42-102t-102-42q-60 0-102 42t-42 102q0 60 42 102t102 42ZM403-96l-22-114q-23-9-44.5-21T296-259l-110 37-77-133 87-76q-2-12-3-24t-1-25q0-13 1-25t3-24l-87-76 77-133 110 37q19-16 40.5-28t44.5-21l22-114h154l22 114q23 9 44.5 21t40.5 28l110-37 77 133-87 76q2 12 3 24t1 25q0 13-1 25t-3 24l87 76-77 133-110-37q-19 16-40.5 28T579-210L557-96H403Z"/></svg>';
