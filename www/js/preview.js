import { AtlasAPI } from './atlas-api.js';
import { state, getSelectedNames } from './state.js';

export const previewContainer = document.getElementById('preview-container');
export const previewImg       = document.getElementById('preview-img');

export function resetPreview() {
  state.viewState = { scale: 1, x: 0, y: 0, isDragging: false, startX: 0, startY: 0 };
  applyTransform();
}

export function applyTransform() {
  const { x, y, scale } = state.viewState;
  previewImg.style.transform = `translate(calc(-50% + ${x}px), calc(-50% + ${y}px)) scale(${scale})`;
  if (state.currentMode === 'modify') drawRegionOverlay();
}

export function drawRegionOverlay() {
  const canvas = document.getElementById('region-overlay');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const containerW = previewContainer.clientWidth;
  const containerH = previewContainer.clientHeight;
  canvas.width = containerW * dpr;
  canvas.height = containerH * dpr;
  canvas.style.width  = containerW + 'px';
  canvas.style.height = containerH + 'px';
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, containerW, containerH);
  if (state.currentMode !== 'modify' || state.selectedIndices.size === 0) return;

  const imgW = previewImg.naturalWidth, imgH = previewImg.naturalHeight;
  if (!imgW || !imgH) return;

  const { scale, x, y } = state.viewState;
  const centerX  = containerW / 2 + x;
  const centerY  = containerH / 2 + y;
  const topLeftX = centerX - imgW * scale / 2;
  const topLeftY = centerY - imgH * scale / 2;
  const lineWidth = 3;

  for (const name of getSelectedNames()) {
    const bounds = state.modifyRegionBounds[name];
    if (!bounds) continue;
    const [bx, by, bw, bh, rotate] = bounds;
    const isRotated = rotate === 90 || rotate === 270;
    const drawW = isRotated ? bh : bw;
    const drawH = isRotated ? bw : bh;
    const rx = topLeftX + bx * scale;
    const ry = topLeftY + by * scale;
    const rw = drawW * scale;
    const rh = drawH * scale;

    ctx.strokeStyle = 'rgba(255, 60, 60, 0.85)';
    ctx.lineWidth = lineWidth;
    ctx.strokeRect(rx - lineWidth / 2, ry - lineWidth / 2, rw + lineWidth, rh + lineWidth);

    const fontSize = 13;
    ctx.font = `bold ${fontSize}px "Segoe UI", sans-serif`;
    const textW = ctx.measureText(name).width;
    const labelX = rx;
    const labelY = ry - lineWidth - 2;
    ctx.fillStyle = 'rgba(255, 60, 60, 0.85)';
    ctx.fillRect(labelX - 1, labelY - fontSize, textW + 8, fontSize + 4);
    ctx.fillStyle = 'white';
    ctx.fillText(name, labelX + 3, labelY - 1);
  }
}

export function clearOverlay() {
  const canvas = document.getElementById('region-overlay');
  if (canvas) canvas.getContext('2d').clearRect(0, 0, canvas.width, canvas.height);
}

export async function updatePreview(names) {
  const status = document.getElementById('status-text');
  if (!names || names.length === 0) {
    previewImg.style.display = 'none';
    status.innerText = 'No selection';
    return;
  }
  const base64Img = AtlasAPI.get_preview ? await AtlasAPI.get_preview(names) : null;
  if (base64Img) {
    previewImg.src = base64Img;
    previewImg.style.display = 'block';
    status.innerText = names.length === 1 ? `Previewing: ${names[0]}` : `Previewing: ${names.length} regions`;
    previewImg.onload = function () {
      resetPreview();
      const containerW = previewContainer.clientWidth - 40;
      const containerH = previewContainer.clientHeight - 40;
      const imgW = previewImg.naturalWidth, imgH = previewImg.naturalHeight;
      status.innerText = names.length === 1
        ? `Previewing: ${names[0]} (${imgW}x${imgH})`
        : `Previewing: ${names.length} regions (${imgW}x${imgH})`;
      if (imgW > containerW || imgH > containerH) {
        state.viewState.scale = Math.min(containerW / imgW, containerH / imgH);
        applyTransform();
      }
      previewImg.onload = null;
    };
  } else {
    previewImg.style.display = 'none';
    status.innerText = 'Preview failed';
  }
}

export function updateModifyPreview(names) {
  drawRegionOverlay();
  if (!names || names.length === 0) {
    document.getElementById('modify-status-text').innerText = state.hasModImage
      ? 'Mod image merged. Ready to save.'
      : 'Select regions want to edit.';
  } else {
    document.getElementById('modify-status-text').innerText = state.hasModImage
      ? `Merged preview. ${names.length} region(s) selected.`
      : `${names.length} region(s) selected`;
  }
}

// ─── Pan & Zoom ───────────────────────────────────────────────────────────────
previewContainer.addEventListener('wheel', (e) => {
  e.preventDefault();
  const direction = -Math.sign(e.deltaY);
  const newScale = state.viewState.scale + direction * 0.1 * state.viewState.scale;
  if (newScale > 0.1 && newScale < 50) {
    const rect = previewContainer.getBoundingClientRect();
    const cx = rect.width / 2, cy = rect.height / 2;
    const mx = e.clientX - rect.left - cx, my = e.clientY - rect.top - cy;
    state.viewState.x     = mx - (mx - state.viewState.x) * (newScale / state.viewState.scale);
    state.viewState.y     = my - (my - state.viewState.y) * (newScale / state.viewState.scale);
    state.viewState.scale = newScale;
    applyTransform();
  }
});

previewContainer.addEventListener('mousedown', (e) => {
  if (e.button !== 0 && e.button !== 1) return;
  e.preventDefault();
  state.viewState.isDragging = true;
  state.viewState.startX = e.clientX - state.viewState.x;
  state.viewState.startY = e.clientY - state.viewState.y;
});

window.addEventListener('mousemove', (e) => {
  if (state.viewState.isDragging) {
    state.viewState.x = e.clientX - state.viewState.startX;
    state.viewState.y = e.clientY - state.viewState.startY;
    applyTransform();
  }
});

// ─── Touch pan & pinch-to-zoom ────────────────────────────────────────────────
let _lastTouchDist = null;
let _lastTouchMid  = null;

previewContainer.addEventListener('touchstart', (e) => {
  if (e.touches.length === 2) {
    _lastTouchDist = Math.hypot(
      e.touches[1].clientX - e.touches[0].clientX,
      e.touches[1].clientY - e.touches[0].clientY,
    );
    _lastTouchMid = {
      x: (e.touches[0].clientX + e.touches[1].clientX) / 2,
      y: (e.touches[0].clientY + e.touches[1].clientY) / 2,
    };
  } else if (e.touches.length === 1) {
    state.viewState.isDragging = true;
    state.viewState.startX = e.touches[0].clientX - state.viewState.x;
    state.viewState.startY = e.touches[0].clientY - state.viewState.y;
  }
}, { passive: true });

previewContainer.addEventListener('touchmove', (e) => {
  if (e.touches.length === 2) {
    const dist = Math.hypot(
      e.touches[1].clientX - e.touches[0].clientX,
      e.touches[1].clientY - e.touches[0].clientY,
    );
    if (_lastTouchDist) {
      const factor   = dist / _lastTouchDist;
      const newScale = Math.min(50, Math.max(0.1, state.viewState.scale * factor));
      const rect = previewContainer.getBoundingClientRect();
      const mid  = {
        x: (e.touches[0].clientX + e.touches[1].clientX) / 2,
        y: (e.touches[0].clientY + e.touches[1].clientY) / 2,
      };
      const cx = rect.width / 2, cy = rect.height / 2;
      const mx = mid.x - rect.left - cx, my = mid.y - rect.top - cy;
      state.viewState.x     = mx - (mx - state.viewState.x) * (newScale / state.viewState.scale);
      state.viewState.y     = my - (my - state.viewState.y) * (newScale / state.viewState.scale);
      state.viewState.scale = newScale;
      applyTransform();
    }
    _lastTouchDist = dist;
  } else if (e.touches.length === 1 && state.viewState.isDragging) {
    state.viewState.x = e.touches[0].clientX - state.viewState.startX;
    state.viewState.y = e.touches[0].clientY - state.viewState.startY;
    applyTransform();
  }
}, { passive: true });

previewContainer.addEventListener('touchend', () => {
  _lastTouchDist = null;
  _lastTouchMid  = null;
  state.viewState.isDragging = false;
}, { passive: true });
