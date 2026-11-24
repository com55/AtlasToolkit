// SPDX-FileCopyrightText: 2026 com55
// SPDX-License-Identifier: Apache-2.0
import { AtlasAPI } from './atlas-api.js';
import { HostError } from './engine-client.js';
import { refreshPanelSplit } from './panel-resizer.js';
import { isTouchDevice, isPywebviewDesktop } from './platform.js';
import { isBusy, noteBusyConflict } from './busy-overlay.js';

const PACKERS = [
  ['auto', 'Auto', 'Polygons with a .skel, otherwise Rectangles.'],
  ['shelf', 'Shelf', 'Rows, tallest first. Fastest.'],
  ['rectangles', 'Rectangles', 'Fills the gaps between rectangles.'],
  ['polygons', 'Polygons', 'Fills gaps using mesh outlines.'],
];
const HINTS = {
  meshAware: "Clears pixels outside each region's mesh outline before packing. Spine never draws them, so regions can sit closer. Needs a .skel.",
  shareCompositeImage: 'When one image replaces several regions, every region uses the whole image.<br>Off: the image is cut back into one piece per region.',
  bleed: "Gives transparent pixels next to each image that image's colour (alpha stays 0), as Spine's packer does, so filtering draws no dark edges. Premultiplied pages are left as they are.",
  padding: "Empty pixels kept between images, so a neighbour never shows at a region's edge when the texture is filtered. Default 2.",
  keepLayout: 'Keep existing region positions when possible. Otherwise, repack using the selected packer.',
};
const LOCK_REASONS = {
  meshAware: v => v.packer === 'polygons' ? 'Polygons always packs by mesh outline.'
    : (v.packer === 'auto' && v.polygonsAllowed) ? 'Auto uses Polygons with a .skel, which always packs by mesh outline.'
    : 'Needs a usable .skel file.',
  bleed: () => 'Pages with premultiplied alpha never get bleed.',
};

// `settings`: the engine's applied state. `draft`: the requested values being
// edited (null when the panel shows the applied state); `view`: the engine's
// preview of that draft, so locks and fallbacks come from the one place
// that applies them (AppHost._settings).
let settings = null, draft = null, view = null, fallback = null, timer = null;
let previewSeq = 0;
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
  // Out of the way before the repack's busy overlay paints; it comes back
  // with the draft only if the engine refuses the settings.
  panel()?.classList.add('hidden');
  syncBackdrop();
  try {
    const result = await AtlasAPI.set_settings(partial);
    if (result?.state && !result.unchanged) await (await import('./modify-mode.js')).onModPreviewReceived(result);
    closeSettingsPanel();
  } catch (e) {
    if (!(e instanceof HostError)) { closeSettingsPanel(); throw e; }
    panel()?.classList.remove('hidden');
    syncBackdrop();
    await window.showAlert(e.message, 'Cannot apply setting');
  }
}

function infoButton(key, label) {
  return `<button type="button" class="info-btn" data-info="${key}" aria-haspopup="dialog" aria-label="About ${label}">${INFO}</button>`;
}
function toggle(value, key, label) {
  const locked = value[`${key}Locked`];
  const reason = locked ? ` data-help="${LOCK_REASONS[key](value)}"` : '';
  return `<div class="opt"><div class="opt-line"><label class="toggle-label ${locked ? 'is-locked' : ''}"${reason}><input type="checkbox" data-setting="${key}" ${value[key] ? 'checked' : ''} ${locked ? 'disabled' : ''}><span class="toggle-switch"></span><span>${label}</span></label>${infoButton(key, label)}</div></div>`;
}

export function renderSettings(value) {
  if (!value) return;
  settings = value;
  if (value.fallbackFrom && fallback !== value.fallbackFrom) {
    window.showToast('Polygons need a .skel file; using Rectangles.', 'info');
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
      <div class="sec"><p class="pack-heading">Regions</p>${toggle(value, 'meshAware', 'Mesh-Aware')}${toggle(value, 'shareCompositeImage', 'Share Composite Image')}</div>
      <div class="sec"><p class="pack-heading">Spacing</p><div class="sec-head"><span class="pack-sub">Padding (px)</span>${infoButton('padding', 'Padding')}<button id="btn-pad-reset" type="button" data-help="Reset to default (2)" aria-label="Reset to default (2)">${RESET}</button></div><div class="padding-controls"><input type="range" id="p-range" min="1" max="100" value="${Math.min(100,value.padding)}"><input type="number" id="p-num" min="1" max="256" step="1" value="${value.padding}"></div><div class="opt-gap"></div>${toggle(value, 'bleed', 'Bleed')}</div>
      <div class="sec"><p class="pack-heading">Layout</p>${toggle(value, 'keepLayout', 'Try to Keep Layout')}</div>
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
  panel().querySelectorAll('[data-info]').forEach(b => b.onclick = () => showInfo(b.dataset.info));
  document.getElementById('btn-packer-info').onclick = () => showInfo('packer');
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
// One modal for every info button; its content is swapped per topic.
function infoContent(key) {
  if (key === 'packer') {
    return `<h3 id="packer-info-title">Packers</h3>
      <p class="pi-lead">A packer decides where regions go on the atlas page when you save edits.</p>
      <div class="pi-list">${PACKER_INFO.map(([title, body, svg, skel]) => `<div class="pi-card"><svg viewBox="0 0 170 128" aria-hidden="true"><rect x="1" y="1" width="168" height="126" fill="#1e1e1e" stroke="#555"/>${svg}</svg><div><h4>${title}${skel ? ' <span class="pi-pill">needs .skel</span>' : ''}</h4><p>${body}</p></div></div>`).join('')}</div>`;
  }
  const {title, figs, legend = []} = INFO_PAGES[key];
  const swatch = color => color === 'dash' ? '<i class="pi-dash"></i>' : `<i style="background:${color}"></i>`;
  return `<h3 id="packer-info-title">${title}</h3>
    <p class="pi-lead">${HINTS[key]}</p>
    ${legend.length ? `<div class="pi-legend">${legend.map(([color, text]) => `<span>${swatch(color)}${text}</span>`).join('')}</div>` : ''}
    <div class="info-figs">${figs.map(([caption, svg, w = 170]) => `<figure class="${w > 170 ? 'wide' : ''}"><svg viewBox="0 0 ${w} 110" aria-hidden="true"><rect x="1" y="1" width="${w - 2}" height="108" fill="#1e1e1e" stroke="#555"/>${svg}</svg><figcaption>${caption}</figcaption></figure>`).join('')}</div>`;
}
function showInfo(key) {
  let overlay = packerInfo();
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'packer-info-overlay';
    overlay.innerHTML = `<div id="packer-info" role="dialog" aria-modal="true" aria-labelledby="packer-info-title"><div id="packer-info-body"></div>
      <div class="modal-buttons"><button type="button" class="btn-primary" id="packer-info-ok">OK</button></div></div>`;
    document.body.append(overlay);
    overlay.addEventListener('click', e => { if (e.target === overlay) closePackerInfo(); });
    document.getElementById('packer-info-ok').onclick = closePackerInfo;
  }
  document.getElementById('packer-info-body').innerHTML = infoContent(key);
  overlay.dataset.topic = key;
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
    draft = null; view = null; render();
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

// Sample regions packed by the real packers (shelf_pack / nest_pack, gap 2;
// rectangles with solid footprints, polygons with the shapes). Name: [w, h, outline].
const SHAPES = {
  rounded: [29, 15, '<rect width="28.17" height="14.04" rx="2.25"/>'],
  triangle: [10, 17, '<path d="M0.0 0.0 L0.0 16.34 L9.24 16.34 Z"/>'],
  angle: [60, 34, '<path d="M0.0 0.0 L59.7 0.0 L59.7 34.0 L54.8 34.0 L54.8 8.3 L0.0 8.3 Z"/>'],
  square: [9, 9, '<path d="M0.0 0.0 L8.28 0.0 L8.28 8.28 L0.0 8.28 Z"/>'],
  circle: [9, 9, '<circle cx="4.4" cy="4.4" r="4.4"/>'],
  diamond: [12, 8, '<path d="M0.0 3.6 L5.6 0.0 L11.12 3.6 L5.6 7.36 Z"/>'],
  uframe: [32, 17, '<path d="M0.0 0.0 L6.5 0.0 L6.5 12.0 L24.7 12.0 L24.7 0.0 L31.2 0.0 L31.2 17.0 L0.0 17.0 Z"/>'],
  parallelogram: [21, 27, '<path d="M4.96 0.0 L20.24 0.0 L15.28 26.64 L0.0 26.64 Z"/>'],
  trapezoid: [13, 7, '<path d="M1.6 0.0 L10.84 0.0 L12.43 6.44 L0.0 6.44 Z"/>'],
  chevron: [10, 12, '<path d="M0.0 0.0 L3.8 0.0 L9.24 5.78 L3.8 11.61 L0.0 11.61 L5.45 5.78 Z"/>'],
  sliver: [17, 5, '<path d="M0.0 4.95 L16.28 0.0 L16.28 4.95 Z"/>'],
  bird: [72, 60, '<path d="M0.0 47.6 L7.7 37.4 L25.8 39.1 L36.3 45.0 L52.8 31.6 L62.9 21.4 L43.6 14.8 L51.5 7.9 L58.8 7.7 L63.3 0.0 L65.3 4.1 L71.3 19.5 L66.3 40.4 L52.6 54.8 L22.5 59.4 L24.5 46.7 Z"/>'],
  small1: [14, 13, '<path d="M13.8 6.7 L13.1 11.4 L8.2 12.8 L2.1 10.0 L0.0 8.4 L3.5 3.0 L5.5 0.0 L10.1 2.9 Z"/>'],
  small2: [5, 7, '<path d="M4.4 3.1 L5.0 6.8 L1.2 5.6 L0.0 3.2 L1.4 1.2 L3.4 0.0 Z"/>'],
  small3: [8, 8, '<path d="M8.0 3.5 L4.6 7.5 L0.0 7.0 L0.7 0.0 L5.3 0.0 Z"/>'],
  small4: [12, 12, '<path d="M11.2 5.1 L9.3 11.5 L0.0 9.1 L3.2 3.9 L6.8 0.0 Z"/>'],
  small5: [9, 10, '<path d="M7.8 6.8 L8.3 10.0 L5.0 8.9 L0.0 9.8 L0.4 4.9 L0.7 1.8 L3.7 0.0 L8.0 3.2 Z"/>'],
  small6: [13, 13, '<path d="M12.1 8.0 L8.6 12.3 L4.6 11.2 L1.9 9.8 L0.0 6.5 L1.3 1.6 L4.5 0.0 L9.0 4.7 Z"/>'],
};
// Per packer: page size and [name, x, y, rotate] (rotate as stored on the page).
const LAYOUTS = {
  shelf: {page: [140, 100], items: [['bird',0,0,0], ['angle',74,0,0], ['uframe',0,62,0], ['rounded',34,62,0], ['parallelogram',65,62,90], ['triangle',94,62,0], ['sliver',106,62,0], ['small1',125,62,0], ['trapezoid',0,85,0], ['small6',15,85,0], ['diamond',30,85,0], ['chevron',44,85,0], ['small4',56,85,0], ['small5',70,85,0], ['square',81,85,0], ['circle',92,85,0], ['small3',103,85,0], ['small2',113,85,0]]},
  rectangles: {page: [132, 92], items: [['bird',0,0,0], ['angle',76,0,90], ['uframe',112,0,90], ['rounded',112,36,90], ['parallelogram',0,64,0], ['triangle',24,64,0], ['sliver',36,64,0], ['small1',56,64,0], ['trapezoid',72,64,0], ['small6',88,64,0], ['diamond',104,68,0], ['chevron',120,68,0], ['small4',36,72,0], ['small5',72,76,0], ['square',52,80,0], ['circle',84,80,0], ['small3',96,80,0], ['small2',64,80,0]]},
  polygons: {page: [96, 88], items: [['bird',0,0,0], ['angle',0,52,180], ['uframe',0,0,0], ['rounded',0,20,0], ['parallelogram',72,8,0], ['triangle',36,0,270], ['sliver',72,0,0], ['small1',32,16,0], ['trapezoid',68,40,0], ['small6',80,44,90], ['diamond',60,48,0], ['chevron',8,52,0], ['small4',52,56,0], ['small5',12,0,0], ['square',32,60,0], ['circle',68,56,0], ['small3',32,32,0], ['small2',48,20,0]]},
};

// rotate:90 is stored 90 CCW (as rotate_grid_for_pack maps it).
const turn = (deg, w, h) => ({0: [1,0,0,1,0,0], 90: [0,-1,1,0,0,w], 180: [-1,0,0,-1,w,h], 270: [0,1,-1,0,h,0]})[deg].join(' ');
const SCALE = 1.15;  // the largest page (shelf, 140x100) fits the 170x128 frame
// Each region with its rectangle as a faint dashed line, on its page.
const layout = name => {
  const {page: [pw, ph], items} = LAYOUTS[name];
  return `<g transform="translate(3 3) scale(${SCALE})"><rect width="${pw}" height="${ph}" fill="#262626"/>` + items.map(([key, x, y, deg]) => {
    const [w, h, shape] = SHAPES[key];
    return `<g transform="translate(${x} ${y}) matrix(${turn(deg, w, h)})"><g fill="#3d7fb8">${shape}</g><rect width="${w}" height="${h}" fill="none" stroke="#9cc6ea" stroke-width=".6" stroke-dasharray="2 2" opacity=".4"/></g>`;
  }).join('') + '</g>';
};
const POLYGONS = layout('polygons');
// Figures for the info modals (170x110 each). Blue: regions, orange: an
// edited image, green: an added region; dashed: a region's rectangle.
const B = '#3d7fb8', E = '#d08a2e', A = '#4caf50', DASH = 'fill="none" stroke="#9cc6ea" stroke-width=".8" stroke-dasharray="3 2"';
const label = (x, y, text) => `<text x="${x}" y="${y}" font-size="9" fill="#ccc" text-anchor="middle">${text}</text>`;
// A concave mesh (a C shape) and stray pixels outside it.
const MESH = 'M34 18 L104 18 L104 38 L62 38 L62 70 L104 70 L104 92 L34 92 Z';
const OUTLINE = `<path d="${MESH}" fill="none" stroke="#fff" stroke-width=".8" stroke-dasharray="3 2" opacity=".8"/>`;
const box = (x, y, w, h, fill) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${fill}"/>`;
const STRAY = box(16, 48, 14, 12, B) + box(70, 46, 12, 8, B) + box(80, 58, 10, 8, B);
const arrow = (x, y) => `<path d="M${x} ${y - 4} L${x + 14} ${y - 4} L${x + 14} ${y - 9} L${x + 24} ${y} L${x + 14} ${y + 9} L${x + 14} ${y + 4} L${x} ${y + 4} Z" fill="#888"/>`;
// The replacement image: a head overlapping a body.
const PERSON = (x, y) => `<g transform="translate(${x} ${y})"><rect x="6" y="26" width="28" height="52" rx="5" fill="${B}"/><circle cx="20" cy="18" r="16" fill="#5a9fd8"/></g>`;
const slot = (x, y, w, h) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="8" ${DASH}/>`;
const clip = (id, x, y, w, h, body) => `<clipPath id="${id}"><rect x="${x}" y="${y}" width="${w}" height="${h}"/></clipPath><g clip-path="url(#${id})">${body}</g>`;
const SOURCE = `${label(36, 12, 'Image')}${slot(12, 18, 48, 86)}${PERSON(16, 22)}${arrow(70, 60)}`;
const pixels = (on) => {
  const cells = [];
  for (let row = 0; row < 4; row++) for (let col = 0; col < 7; col++) {
    const x = 14 + col * 20, y = 10 + row * 14, filled = col < 3;
    const fill = filled ? E : (on && col === 3 ? E : '#2a2a2a');
    const op = filled ? 1 : (on && col === 3 ? .35 : 1);
    cells.push(`<rect x="${x}" y="${y}" width="19" height="13" fill="${fill}" opacity="${op}"/>`);
  }
  const [c1, c2, c3] = (on ? `${E};${E};${E}00` : `${E};#3a2a14;${E}00`).split(';');
  const id = on ? 'bleed-on' : 'bleed-off';
  return cells.join('') + `<defs><linearGradient id="${id}"><stop offset="0" stop-color="${c1}"/><stop offset=".55" stop-color="${c2}"/><stop offset="1" stop-color="${c3}"/></linearGradient></defs>`
    + `<rect x="14" y="78" width="139" height="14" fill="url(#${id})"/>` + label(84, 103, 'edge after filtering');
};
// Try to Keep Layout: one page before and after (r: removed, e: edited, n: new).
const ORIGINAL = [[10, 10, 50, 40], [64, 10, 40, 40], [108, 10, 52, 26], [10, 54, 70, 30], [84, 54, 40, 46], [128, 40, 32, 24]];
const GHOST = (x, y, w, h) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="none" stroke="#777" stroke-width=".8" stroke-dasharray="3 2"/>`;
const page = list => list.map(([x, y, w, h, kind]) => kind === 'r' ? GHOST(x, y, w, h) : box(x, y, w, h, kind === 'e' ? E : kind === 'n' ? A : B)).join('');
const INFO_PAGES = {
  meshAware: {title: 'Mesh-Aware', figs: [
    ['Off: the whole image is packed, stray pixels included.',
      `<path d="${MESH}" fill="${B}"/>${STRAY}${OUTLINE}<rect x="14" y="14" width="94" height="82" ${DASH}/>${box(122, 30, 36, 50, B)}`],
    ['On: pixels outside the mesh outline are cleared, so the rectangle shrinks.',
      `<path d="${MESH}" fill="${B}"/>${OUTLINE}<rect x="30" y="14" width="78" height="82" ${DASH}/>${box(116, 30, 36, 50, B)}`],
  ]},
  shareCompositeImage: {title: 'Share Composite Image', figs: [
    ['Off: split before packing; each region gets its own piece.',
      `<g transform="translate(1 0)">${SOURCE}${label(129, 10, 'Head')}${slot(104, 16, 50, 48)}${clip('sc-head', 104, 22, 50, 36, PERSON(109, 22))}`
      + `${label(191, 10, 'Body')}${slot(166, 16, 50, 64)}${clip('sc-body', 166, 20, 50, 52, PERSON(171, -6))}</g>`, 230],
    ['On: the regions share the whole image in packing.',
      `<g transform="translate(32 0)">${SOURCE}${label(129, 12, 'Head + Body')}${slot(104, 18, 50, 86)}${PERSON(109, 22)}</g>`, 230],
  ]},
  bleed: {title: 'Bleed', figs: [
    ['Off: transparent pixels are black, so the edge darkens when filtered.', pixels(false)],
    ['On: they take the image\'s colour (still transparent), so the edge stays clean.', pixels(true)],
  ]},
  padding: {title: 'Padding', figs: [
    ['The gap between images; none is added at the page edges.',
      `${box(27, 13, 50, 34, B)}${box(93, 13, 50, 34, B)}${box(27, 63, 50, 34, B)}${box(93, 63, 50, 34, B)}<g stroke="#ffc107" stroke-width="1"><line x1="78" y1="30" x2="92" y2="30"/><line x1="78" y1="25" x2="78" y2="35"/><line x1="92" y1="25" x2="92" y2="35"/><line x1="52" y1="48" x2="52" y2="62"/><line x1="47" y1="48" x2="57" y2="48"/><line x1="47" y1="62" x2="57" y2="62"/></g>`],
  ]},
  keepLayout: {title: 'Try to Keep Layout', legend: [[E, 'edited'], [A, 'added'], ['dash', 'removed']], figs: [
    ['The original atlas page.', page(ORIGINAL)],
    ["On: regions stay put; the new one goes into free space, such as a removed region's spot.",
      page([ORIGINAL[0], [...ORIGINAL[1], 'e'], ORIGINAL[2], [...ORIGINAL[3], 'r'], ORIGINAL[4], ORIGINAL[5], [14, 58, 56, 22, 'n']])],
    ['Off, or when an edit does not fit: the page is repacked.',
      page([[10, 10, 40, 46], [54, 10, 50, 40], [108, 10, 40, 40, 'e'], [10, 60, 52, 26], [66, 54, 56, 22, 'n'], [126, 54, 32, 24]])],
  ]},
};
const PACKER_INFO = [
  ['Shelf', 'Lays regions out in rows, tallest first. The fastest packer; leaves empty space above shorter regions in a row.',
    layout('shelf')],
  ['Rectangles', 'Puts each region into the first gap its rectangle fits, including gaps between regions already placed. Smaller pages than Shelf.',
    layout('rectangles')],
  ['Polygons', "Packs by each region's mesh outline instead of its rectangle, so shapes nest into each other's empty corners. The smallest pages. A region's rectangle may then hold parts of its neighbours (dashed); Spine draws only inside the mesh.",
    POLYGONS, true],
];
const INFO = '<svg xmlns="http://www.w3.org/2000/svg" height="15" width="15" viewBox="0 -960 960 960" aria-hidden="true"><path fill="currentColor" d="M440-280h80v-240h-80v240Zm40-320q17 0 28.5-11.5T520-640q0-17-11.5-28.5T480-680q-17 0-28.5 11.5T440-640q0 17 11.5 28.5T480-600Zm0 520q-83 0-156-31.5T197-197q-54-54-85.5-127T80-480q0-83 31.5-156T197-763q54-54 127-85.5T480-880q83 0 156 31.5T763-763q54 54 85.5 127T880-480q0 83-31.5 156T763-197q-54 54-127 85.5T480-80Zm0-80q134 0 227-93t93-227q0-134-93-227t-227-93q-134 0-227 93t-93 227q0 134 93 227t227 93Z"/></svg>';
const CHECK = '<svg xmlns="http://www.w3.org/2000/svg" height="16" width="16" viewBox="0 -960 960 960" aria-hidden="true"><path fill="currentColor" d="M382-240 154-468l57-57 171 171 367-367 57 57-424 424Z"/></svg>';
const RESET = '<svg xmlns="http://www.w3.org/2000/svg" height="14" width="14" viewBox="0 -960 960 960" aria-hidden="true"><path fill="currentColor" d="M480-160q-134 0-227-93t-93-227q0-134 93-227t227-93q69 0 132 28.5T720-690v-110h80v280H520v-80h168q-32-56-87.5-88T480-720q-100 0-170 70t-70 170q0 100 70 170t170 70q77 0 139-44t87-116h84q-28 106-114 173t-196 67Z"/></svg>';
const GEAR = '<svg xmlns="http://www.w3.org/2000/svg" height="18" viewBox="0 -960 960 960" width="18"><path fill="currentColor" d="M480-336q60 0 102-42t42-102q0-60-42-102t-102-42q-60 0-102 42t-42 102q0 60 42 102t102 42ZM403-96l-22-114q-23-9-44.5-21T296-259l-110 37-77-133 87-76q-2-12-3-24t-1-25q0-13 1-25t3-24l-87-76 77-133 110 37q19-16 40.5-28t44.5-21l22-114h154l22 114q23 9 44.5 21t40.5 28l110-37 77 133-87 76q2 12 3 24t1 25q0 13-1 25t-3 24l87 76-77 133-110-37q-19 16-40.5 28T579-210L557-96H403Z"/></svg>';
