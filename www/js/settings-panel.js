import { AtlasAPI } from './atlas-api.js';
import { HostError } from './engine-client.js';
import { refreshPanelSplit } from './panel-resizer.js';
import { isTouchDevice, isPywebviewDesktop } from './platform.js';
import { isBusy, noteBusyConflict } from './busy-overlay.js';

const PACKERS = [
  ['shelf', 'Shelf', 'Rows of rectangles. Fastest.'],
  ['rectangles', 'Rectangles', 'Fills gaps between rectangles.'],
  ['polygons', 'Polygons', 'Fills gaps using each mesh outline.'],
  ['rectangles_in_place', 'Rectangles In-Place', 'Edits that fit stay where they are.'],
  ['polygons_in_place', 'Polygons In-Place', 'Same, cut to each mesh outline.'],
];
let settings = null, fallback = null, timer = null;
const panel = () => document.getElementById('pack-panel');
const chip = () => document.getElementById('btn-pack-chip');
export function closeSettingsPanel() {
  clearTimeout(timer);
  panel()?.classList.add('hidden');
  document.getElementById('pack-backdrop')?.classList.add('hidden');
  chip()?.setAttribute('aria-expanded', 'false');
}
function positionPanel() {
  const p = panel();
  if (!p || p.classList.contains('hidden')) return;
  const modal = innerWidth <= 600 || (isTouchDevice() && !isPywebviewDesktop());
  document.getElementById('pack-backdrop').classList.toggle('hidden', !modal);
  p.classList.toggle('is-touch-modal', modal);
  p.setAttribute('aria-modal', String(modal));
  if (modal) {
    p.style.removeProperty('left');
    p.style.removeProperty('top');
  } else {
    const r = chip().getBoundingClientRect();
    p.style.left = `${Math.max(8, Math.min(r.left, innerWidth - p.offsetWidth - 8))}px`;
    p.style.top = `${Math.max(8, Math.min(r.bottom + 6, innerHeight - p.offsetHeight - 8))}px`;
  }
}
async function apply(key, value) {
  if (isBusy()) { noteBusyConflict(); renderSettings(settings); return; }
  clearTimeout(timer);
  const previous = settings;
  try {
    const result = await AtlasAPI.set_settings({[key]:value});
    if (result?.state) await (await import('./modify-mode.js')).onModPreviewReceived(result);
    renderSettings(AtlasAPI.get_settings());
  } catch (e) {
    renderSettings(previous);
    if (!(e instanceof HostError)) throw e;
    await window.showAlert(e.message, 'Cannot apply setting');
  }
}
function toggle(key, label, help) {
  const locked = settings[`${key}Locked`];
  return `<label class="toggle-label ${locked ? 'is-locked' : ''}" title="${help}"><input type="checkbox" data-setting="${key}" ${settings[key] ? 'checked' : ''} ${locked ? 'disabled' : ''}><span class="toggle-switch"></span><span>${label}</span></label>`;
}
export function renderSettings(value) {
  if (!value) return;
  settings = value;
  if (value.fallbackFrom && fallback !== value.fallbackFrom) {
    const inplace = value.fallbackFrom === 'polygons_in_place';
    window.showToast(inplace ? 'Polygons In-Place need a .skel file; using Rectangles In-Place.' : 'Polygons need a .skel file; using Rectangles.', 'info');
  }
  fallback = value.fallbackFrom;
  if (!panel()) return;
  chip().innerHTML = `${GEAR}<span>Settings</span><span class="chip-sep"></span><span class="chip-value">${PACKERS.find(p => p[0] === value.packer)[1]} · ${value.padding}px</span> ▾`;
  panel().innerHTML = `<div class="sec"><p class="pack-heading">Packer</p><div class="pk-menu">${PACKERS.map(([key,label,description]) => {
    const disabled = key.startsWith('polygons') && !value.polygonsAllowed;
    return `<button type="button" class="pk-item ${key === value.packer ? 'selected' : ''}" data-packer="${key}" ${disabled ? 'disabled' : ''}><span class="pk-check">${key === value.packer ? CHECK : ''}</span><span>${label}<small>${disabled ? 'Needs a .skel file' : description}</small></span></button>`;
  }).join('')}</div></div><div class="sec">${toggle('meshAware','Mesh-Aware','Cut each region to its mesh outline before packing.')}${toggle('splitComposite','Split Composite','Treat a composite image as the inverse of Forced Resizing and split it back per region.')}</div>
  <div class="sec"><div class="sec-head"><p class="pack-heading">Padding (px)</p><button id="btn-pad-reset" type="button" title="Reset to default (2)">${RESET}</button></div><div class="padding-controls"><input type="range" id="p-range" min="1" max="100" value="${Math.min(100,value.padding)}"><input type="number" id="p-num" min="1" max="256" step="1" value="${value.padding}"></div></div>
  <div class="sec">${toggle('keepLayout','Keep layout when only adding','A page with only additions and renames keeps every existing region where it is.')}<p class="hint">New regions go into free space.<br>Off: every edited page is repacked.</p></div>
  ${value.alphaVisible ? `<div class="sec"><p class="pack-heading">Image alpha</p><select id="s-alpha"><option value="straight">Straight alpha</option><option value="premultiplied">Premultiplied alpha</option></select></div>` : ''}<button id="pack-close" type="button">Close</button>`;
  panel().querySelectorAll('[data-packer]').forEach(b => b.onclick = () => apply('packer', b.dataset.packer));
  panel().querySelectorAll('[data-setting]').forEach(c => c.onchange = () => apply(c.dataset.setting, c.checked));
  const range = document.getElementById('p-range'), number = document.getElementById('p-num');
  range.oninput = () => { number.value = range.value; };
  range.onchange = () => apply('padding', Number(range.value));
  number.oninput = () => { clearTimeout(timer); timer = setTimeout(() => apply('padding', Number(number.value)),300); };
  document.getElementById('btn-pad-reset').onclick = () => apply('padding',2);
  const alpha = document.getElementById('s-alpha');
  if (alpha) { alpha.value = value.alpha; alpha.onchange = () => apply('alpha',alpha.value); }
  document.getElementById('pack-close').onclick = closeSettingsPanel;
  positionPanel();
  refreshPanelSplit();
}
export function initSettingsPanel() {
  const backdrop = document.createElement('div'); backdrop.id = 'pack-backdrop'; backdrop.className = 'hidden'; document.body.append(backdrop);
  const p = document.createElement('div'); p.id = 'pack-panel'; p.className = 'hidden'; p.setAttribute('role','dialog'); p.setAttribute('aria-label','Settings'); document.body.append(p);
  chip().onclick = () => {
    if (!settings) return;
    if (!p.classList.contains('hidden')) { closeSettingsPanel(); return; }
    p.classList.remove('hidden'); chip().setAttribute('aria-expanded','true'); positionPanel();
  };
  document.addEventListener('click', e => { if (!p.contains(e.target) && !chip().contains(e.target)) closeSettingsPanel(); });
  window.addEventListener('keydown', e => { if (e.key === 'Escape') closeSettingsPanel(); });
  window.addEventListener('resize',positionPanel);
  renderSettings(AtlasAPI.get_settings());
}
const CHECK = '<svg xmlns="http://www.w3.org/2000/svg" height="16" width="16" viewBox="0 -960 960 960" aria-hidden="true"><path fill="currentColor" d="M382-240 154-468l57-57 171 171 367-367 57 57-424 424Z"/></svg>';
const RESET = '<svg xmlns="http://www.w3.org/2000/svg" height="14" width="14" viewBox="0 -960 960 960" aria-hidden="true"><path fill="currentColor" d="M480-160q-134 0-227-93t-93-227q0-134 93-227t227-93q69 0 132 28.5T720-690v-110h80v280H520v-80h168q-32-56-87.5-88T480-720q-100 0-170 70t-70 170q0 100 70 170t170 70q77 0 139-44t87-116h84q-28 106-114 173t-196 67Z"/></svg>';
const GEAR = '<svg xmlns="http://www.w3.org/2000/svg" height="18" viewBox="0 -960 960 960" width="18"><path fill="currentColor" d="M480-336q60 0 102-42t42-102q0-60-42-102t-102-42q-60 0-102 42t-42 102q0 60 42 102t102 42ZM403-96l-22-114q-23-9-44.5-21T296-259l-110 37-77-133 87-76q-2-12-3-24t-1-25q0-13 1-25t3-24l-87-76 77-133 110 37q19-16 40.5-28t44.5-21l22-114h154l22 114q23 9 44.5 21t40.5 28l110-37 77 133-87 76q2 12 3 24t1 25q0 13-1 25t-3 24l87 76-77 133-110-37q-19 16-40.5 28T579-210L557-96H403Z"/></svg>';
