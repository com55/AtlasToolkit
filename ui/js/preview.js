// Atlas Toolkit UI module
function updateModifyPreview(names) {
  // Pure client-side: just redraw overlay canvas
  drawRegionOverlay();
  if (!names || names.length === 0) {
    setStatus(
      hasModImage
        ? "Mod image merged. Ready to save."
        : "Select regions and click Modify Selected",
    );
  } else {
    setStatus(
      hasModImage
        ? `Merged preview. ${names.length} region(s) selected.`
        : `${names.length} region(s) selected`,
    );
  }
}

var previewContainer = document.getElementById("preview-container");
var previewImg = document.getElementById("preview-img");

function resetPreview() {
  viewState = {
    scale: 1,
    x: 0,
    y: 0,
    isDragging: false,
    startX: 0,
    startY: 0,
  };
  applyTransform();
}

function applyTransform() {
  previewImg.style.transform = `translate(calc(-50% + ${viewState.x}px), calc(-50% + ${viewState.y}px)) scale(${viewState.scale})`;
  // Redraw overlay if in modify mode
  if (currentMode === "modify") {
    drawRegionOverlay();
  }
}

// ==========================================
//  REGION OVERLAY (Canvas)
// ==========================================
function drawRegionOverlay() {
  const canvas = document.getElementById("region-overlay");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  const dpr = window.devicePixelRatio || 1;
  const containerW = previewContainer.clientWidth;
  const containerH = previewContainer.clientHeight;
  canvas.width = containerW * dpr;
  canvas.height = containerH * dpr;
  canvas.style.width = containerW + "px";
  canvas.style.height = containerH + "px";
  ctx.scale(dpr, dpr);

  ctx.clearRect(0, 0, containerW, containerH);

  if (currentMode !== "modify" || selectedIndices.size === 0) return;

  const imgW = previewImg.naturalWidth;
  const imgH = previewImg.naturalHeight;
  if (!imgW || !imgH) return;

  // Compute image position on screen
  const scale = viewState.scale;
  const centerX = containerW / 2 + viewState.x;
  const centerY = containerH / 2 + viewState.y;
  const displayW = imgW * scale;
  const displayH = imgH * scale;
  const topLeftX = centerX - displayW / 2;
  const topLeftY = centerY - displayH / 2;

  const lineWidth = 3;
  const names = getSelectedNames();

  // On multi-page atlases only draw regions that live on the visible page.
  const activePage =
    modifyPages.length > 1 ? modifyPages[modifyActivePageIndex] : null;

  for (const name of names) {
    if (activePage && modifyRegionPages[name] && modifyRegionPages[name] !== activePage)
      continue;
    const bounds = modifyOverlayRects[name] || modifyRegionBounds[name];
    if (!bounds) continue;
    const [bx, by, bw, bh] = bounds.length >= 5
      ? (() => {
          const [x, y, w, h, rotate] = bounds;
          const isRotated = rotate === 90 || rotate === 270;
          return [x, y, isRotated ? h : w, isRotated ? w : h];
        })()
      : bounds;

    const rx = topLeftX + bx * scale;
    const ry = topLeftY + by * scale;
    const rw = bw * scale;
    const rh = bh * scale;

    // Draw rect — expand outward by lineWidth
    ctx.strokeStyle = "rgba(255, 60, 60, 0.85)";
    ctx.lineWidth = lineWidth;
    ctx.strokeRect(
      rx - lineWidth / 2,
      ry - lineWidth / 2,
      rw + lineWidth,
      rh + lineWidth,
    );

    // Draw label above the box
    const fontSize = 13;
    ctx.font = `bold ${fontSize}px "Segoe UI", sans-serif`;
    const textMetrics = ctx.measureText(name);
    const textW = textMetrics.width;
    const labelX = rx;
    const labelY = ry - lineWidth - 2;

    // Label background
    ctx.fillStyle = "rgba(255, 60, 60, 0.85)";
    ctx.fillRect(labelX - 1, labelY - fontSize, textW + 8, fontSize + 4);

    // Label text
    ctx.fillStyle = "white";
    ctx.fillText(name, labelX + 3, labelY - 1);
  }
}

function clearOverlay() {
  const canvas = document.getElementById("region-overlay");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
}

async function updatePreview(names) {
  const status = document.getElementById("status-text");
  if (!names || names.length === 0) {
    previewImg.style.display = "none";
    status.innerText = "No selection";
    updateButtons();
    return;
  }

  const base64Img = await pywebview.api.get_preview(names);
  if (base64Img) {
    previewImg.src = base64Img;
    previewImg.style.display = "block";
    if (names.length === 1) {
      status.innerText = `Previewing: ${names[0]}`;
    } else {
      status.innerText = `Previewing: ${names.length} regions`;
    }

    previewImg.onload = function () {
      resetPreview();
      const containerW = previewContainer.clientWidth - 40;
      const containerH = previewContainer.clientHeight - 40;
      const imgW = previewImg.naturalWidth;
      const imgH = previewImg.naturalHeight;

      if (names.length === 1) {
        status.innerText = `Previewing: ${names[0]} (${imgW}x${imgH})`;
      } else {
        status.innerText = `Previewing: ${names.length} regions (${imgW}x${imgH})`;
      }

      if (imgW > containerW || imgH > containerH) {
        const scaleW = containerW / imgW;
        const scaleH = containerH / imgH;
        viewState.scale = Math.min(scaleW, scaleH);
        applyTransform();
      }
      updateButtons();
      previewImg.onload = null;
    };
  } else {
    previewImg.style.display = "none";
    status.innerText = "Preview failed";
    updateButtons();
  }
}

previewContainer.addEventListener("wheel", (e) => {
  e.preventDefault();
  const zoomIntensity = 0.1;
  const direction = -Math.sign(e.deltaY);
  const newScale =
    viewState.scale + direction * zoomIntensity * viewState.scale;

  if (newScale > 0.1 && newScale < 50) {
    const rect = previewContainer.getBoundingClientRect();
    const cx = rect.width / 2;
    const cy = rect.height / 2;
    const mx = e.clientX - rect.left - cx;
    const my = e.clientY - rect.top - cy;

    viewState.x = mx - (mx - viewState.x) * (newScale / viewState.scale);
    viewState.y = my - (my - viewState.y) * (newScale / viewState.scale);

    viewState.scale = newScale;
    applyTransform();
  }
});

previewContainer.addEventListener("mousedown", (e) => {
  if (e.button !== 0 && e.button !== 1) return;
  e.preventDefault();
  viewState.isDragging = true;
  viewState.startX = e.clientX - viewState.x;
  viewState.startY = e.clientY - viewState.y;
});

window.addEventListener("mousemove", (e) => {
  if (viewState.isDragging) {
    viewState.x = e.clientX - viewState.startX;
    viewState.y = e.clientY - viewState.startY;
    applyTransform();
  }
});

function updateButtons() {
  const btnSel = document.getElementById("btn-extract-sel");
  btnSel.disabled = selectedIndices.size === 0;
  btnSel.innerText = `Extract Selected (${selectedIndices.size})`;

  const btnModSel = document.getElementById("btn-modify-sel");
  if (btnModSel) {
    btnModSel.disabled = selectedIndices.size === 0;
    btnModSel.innerText = `Modify Selected (${selectedIndices.size})`;
  }

  const btnSaveMerged = document.getElementById("btn-save-merged");
  if (btnSaveMerged) {
    const hasPreview =
      previewImg.style.display !== "none" &&
      previewImg.naturalWidth > 0 &&
      previewImg.naturalHeight > 0;
    btnSaveMerged.disabled = !hasPreview;
  }
}
