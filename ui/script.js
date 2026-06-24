// --- Data State ---
let regionsData = [];
let selectedIndices = new Set();
let lastClickIndex = -1;
let isDragSelecting = false;
let dragStartIndex = -1;
let currentMode = "extract"; // 'extract' | 'modify'
let modifyRegionBounds = {}; // {name: [x, y, w, h], ...}
let hasModImage = false;
// Multi-page modify state
let modifyPages = []; // ordered page filenames
let modifyActivePageIndex = 0;
let modifyRegionPages = {}; // {name: pageFilename}
let viewState = {
  scale: 1,
  x: 0,
  y: 0,
  isDragging: false,
  startX: 0,
  startY: 0,
};

window.addEventListener("pywebviewready", async function () {
  // Restore saved preferences
  const repackPref = await pywebview.api.get_pref("repack", false);
  document.getElementById("chk-repack").checked = repackPref;

  const loaded = await pywebview.api.startup_check();
  if (loaded) await loadRegions();
});

// ==========================================
//  MODE SWITCHING
// ==========================================
function setMode(mode) {
  currentMode = mode;
  const normalHeader = document.getElementById("normal-header");
  const modifyHeader = document.getElementById("modify-header");
  const extractControls = document.getElementById("extract-controls");
  const modifyControls = document.getElementById("modify-controls");
  const repackOptions = document.getElementById("repack-options");
  const dropMsg = document.getElementById("drop-message-text");

  if (mode === "modify") {
    normalHeader.classList.add("hidden");
    modifyHeader.classList.remove("hidden");
    extractControls.classList.add("hidden");
    modifyControls.classList.remove("hidden");
    repackOptions.classList.remove("hidden");
    dropMsg.textContent = "Drop image to modify, or .atlas to load";
  } else {
    normalHeader.classList.remove("hidden");
    modifyHeader.classList.add("hidden");
    extractControls.classList.remove("hidden");
    modifyControls.classList.add("hidden");
    repackOptions.classList.add("hidden");
    dropMsg.textContent = "Drop .atlas file here to load";
    clearOverlay(); // Clear region overlay when exiting modify mode
  }
}

async function enterModifyMode() {
  try {
    const data = await pywebview.api.enter_modify_mode();
    if (data) {
      setMode("modify");
      modifyRegionBounds = data.regions || {};
      setupModifyPages(data);
      hasModImage = false;
      document.getElementById("modify-status-text").innerText =
        "Select regions and click Modify Selected";
      document.getElementById("btn-save-mod").disabled = true;
      // Show the original atlas PNG
      previewImg.src = data.image;
      previewImg.style.display = "block";
      previewImg.onload = function () {
        resetPreview();
        const containerW = previewContainer.clientWidth - 40;
        const containerH = previewContainer.clientHeight - 40;
        const imgW = previewImg.naturalWidth;
        const imgH = previewImg.naturalHeight;
        if (imgW > containerW || imgH > containerH) {
          viewState.scale = Math.min(containerW / imgW, containerH / imgH);
          applyTransform();
        }
        previewImg.onload = null;
      };
    } else {
      showToast("Load an atlas first.", "error");
    }
  } catch (e) {
    console.error(e);
    showToast("Failed to enter modify mode.", "error");
  }
}

async function exitModifyMode() {
  try {
    await pywebview.api.exit_modify_mode();
  } catch (e) {
    console.error(e);
  }
  setMode("extract");
  modifyRegionBounds = {};
  modifyPages = [];
  modifyRegionPages = {};
  modifyActivePageIndex = 0;
  document.getElementById("modify-page-switcher").classList.add("hidden");
  hasModImage = false;
  clearOverlay();
  // Restore preview from current selection
  previewImg.style.display = "none";
  resetPreview();
  document.getElementById("status-text").innerText = "Ready";
  updatePreview(getSelectedNames());
}

async function modifySelected() {
  const names = getSelectedNames();
  if (names.length === 0) {
    showToast("Select at least one region to modify.", "error");
    return;
  }
  try {
    document.getElementById("modify-status-text").innerText =
      "Selecting mod image...";
    const repack = document.getElementById("chk-repack").checked;
    const result = await pywebview.api.select_mod_image(names, repack);
    if (result) {
      onModPreviewReceived(result);
    } else {
      document.getElementById("modify-status-text").innerText =
        "Cancelled or no image selected.";
    }
  } catch (e) {
    console.error(e);
    showToast("Error selecting mod image.", "error");
  }
}

function onModPreviewReceived(data) {
  hasModImage = true;
  // Update region bounds from merged atlas
  if (data.regions) {
    modifyRegionBounds = data.regions;
  }
  // Refresh multi-page state (regions were redistributed across pages by repack)
  setupModifyPages(data);
  previewImg.src = data.image;
  previewImg.style.display = "block";
  document.getElementById("modify-status-text").innerText =
    "Mod image merged. Ready to save.";
  document.getElementById("btn-save-mod").disabled = false;

  previewImg.onload = function () {
    resetPreview();
    const containerW = previewContainer.clientWidth - 40;
    const containerH = previewContainer.clientHeight - 40;
    const imgW = previewImg.naturalWidth;
    const imgH = previewImg.naturalHeight;

    document.getElementById("modify-status-text").innerText =
      `Merged preview (${imgW}x${imgH}). Ready to save.`;

    if (imgW > containerW || imgH > containerH) {
      const scaleW = containerW / imgW;
      const scaleH = containerH / imgH;
      viewState.scale = Math.min(scaleW, scaleH);
    }
    applyTransform(); // This also redraws overlay
    previewImg.onload = null;
  };
}

async function saveModified() {
  try {
    document.getElementById("modify-status-text").innerText = "Saving...";
    const result = await pywebview.api.save_modified();
    if (result.startsWith("Error") || result === "Cancelled") {
      showToast(result, result === "Cancelled" ? "info" : "error");
    } else {
      showToast(result, "success");
    }
    document.getElementById("modify-status-text").innerText = result;
  } catch (e) {
    console.error(e);
    showToast("Save failed.", "error");
  }
}

// ==========================================
//  MULTI-PAGE SWITCHER
// ==========================================
function setupModifyPages(data) {
  modifyPages = Array.isArray(data.pages) ? data.pages : [];
  modifyRegionPages = data.regionPages || {};
  modifyActivePageIndex = 0;
  const switcher = document.getElementById("modify-page-switcher");
  const repackOptions = document.getElementById("repack-options");
  if (modifyPages.length > 1) {
    switcher.classList.remove("hidden");
    updatePageIndicator();
    // Multi-page always repacks all pages; the per-page repack toggle is
    // inert here (and toggling it post-merge errors), so hide it.
    repackOptions.classList.add("hidden");
  } else {
    switcher.classList.add("hidden");
    repackOptions.classList.remove("hidden");
  }
}

function updatePageIndicator() {
  const ind = document.getElementById("page-indicator");
  if (ind)
    ind.innerText = `Page ${modifyActivePageIndex + 1} / ${modifyPages.length}`;
  document.getElementById("page-prev").disabled = modifyActivePageIndex <= 0;
  document.getElementById("page-next").disabled =
    modifyActivePageIndex >= modifyPages.length - 1;
}

async function showModifyPage(index) {
  if (index < 0 || index >= modifyPages.length) return;
  modifyActivePageIndex = index;
  updatePageIndicator();
  try {
    const dataUri = await pywebview.api.get_modify_page_preview(index);
    if (!dataUri) return;
    previewImg.src = dataUri;
    previewImg.style.display = "block";
    previewImg.onload = function () {
      resetPreview();
      const containerW = previewContainer.clientWidth - 40;
      const containerH = previewContainer.clientHeight - 40;
      const imgW = previewImg.naturalWidth;
      const imgH = previewImg.naturalHeight;
      if (imgW > containerW || imgH > containerH) {
        viewState.scale = Math.min(containerW / imgW, containerH / imgH);
      }
      applyTransform(); // redraws overlay (filtered to this page)
      previewImg.onload = null;
    };
  } catch (e) {
    console.error(e);
  }
}

function modifyPagePrev() {
  showModifyPage(modifyActivePageIndex - 1);
}
function modifyPageNext() {
  showModifyPage(modifyActivePageIndex + 1);
}

document.getElementById("chk-repack").addEventListener("change", async (e) => {
  pywebview.api.set_pref("repack", e.target.checked);
  if (!hasModImage) return;
  const statusEl = document.getElementById("modify-status-text");
  statusEl.innerText = e.target.checked
    ? "Applying repack..."
    : "Reverting repack...";
  try {
    const result = await pywebview.api.toggle_repack(e.target.checked);
    if (result) {
      onModPreviewReceived(result);
    } else {
      showToast("No merged data to repack.", "error");
    }
  } catch (err) {
    console.error(err);
    showToast("Repack toggle failed.", "error");
  }
});

// ==========================================
//  CORE LOGIC
// ==========================================
async function openFile() {
  try {
    const success = await pywebview.api.choose_file();
    if (success) {
      selectedIndices.clear();
      lastClickIndex = -1;
      document.getElementById("preview-img").style.display = "none";
      resetPreview();
      updateButtons();
      await loadRegions();
    }
  } catch (e) {
    console.error(e);
  }
}

async function loadRegions() {
  regionsData = await pywebview.api.get_region_names();
  if (!regionsData) return;
  const listEl = document.getElementById("region-list");
  listEl.innerHTML = "";
  document.getElementById("count").innerText = regionsData.length;
  regionsData.forEach((name, index) => {
    const li = document.createElement("li");
    li.className = "region-item";
    li.innerText = name;
    li.dataset.index = index;
    li.addEventListener("mousedown", (e) => onRegionMouseDown(e, index, name));
    li.addEventListener("mouseenter", (e) => onRegionMouseEnter(e, index));
    listEl.appendChild(li);
  });
  if (regionsData.length > 0) {
    document.getElementById("status-text").innerText = "Atlas loaded.";
    document.getElementById("btn-enter-modify").disabled = false;
    document.getElementById("btn-extract-all").disabled = false;
  } else {
    document.getElementById("btn-enter-modify").disabled = true;
    document.getElementById("btn-extract-all").disabled = true;
  }
}

function getSelectedNames() {
  return Array.from(selectedIndices)
    .sort((a, b) => a - b)
    .map((i) => regionsData[i]);
}

// --- Auto-Scroll State ---
let autoScrollSpeed = 0;
let autoScrollInterval = null;
const SCROLL_ZONE_SIZE = 50;
const MAX_SCROLL_SPEED = 15;
let lastMouseX = 0;
let lastMouseY = 0;

function onRegionMouseDown(e, index, name) {
  if (e.button !== 0) return;
  if (e.shiftKey) e.preventDefault();

  isDragSelecting = true;
  dragStartIndex = index;

  if (e.ctrlKey || e.metaKey) {
    toggleIndex(index);
    lastClickIndex = index;
  } else if (e.shiftKey && lastClickIndex !== -1) {
    selectRange(
      Math.min(lastClickIndex, index),
      Math.max(lastClickIndex, index),
      false,
    );
  } else {
    selectedIndices.clear();
    selectedIndices.add(index);
    lastClickIndex = index;
  }

  renderSelection();
  triggerPreviewUpdate();

  window.addEventListener("mousemove", onWindowMouseMove);
  startAutoScroll();
}

function onWindowMouseMove(e) {
  if (!isDragSelecting) return;
  e.preventDefault();

  lastMouseX = e.clientX;
  lastMouseY = e.clientY;

  const container = document.getElementById("region-list-container");
  const rect = container.getBoundingClientRect();

  if (e.clientY < rect.top + SCROLL_ZONE_SIZE) {
    const dist = Math.max(0, rect.top + SCROLL_ZONE_SIZE - e.clientY);
    autoScrollSpeed = -(dist / SCROLL_ZONE_SIZE) * MAX_SCROLL_SPEED;
  } else if (e.clientY > rect.bottom - SCROLL_ZONE_SIZE) {
    const dist = Math.max(0, e.clientY - (rect.bottom - SCROLL_ZONE_SIZE));
    autoScrollSpeed = (dist / SCROLL_ZONE_SIZE) * MAX_SCROLL_SPEED;
  } else {
    autoScrollSpeed = 0;
  }

  updateSelectionFromMouse(e.clientX, e.clientY);
}

function updateSelectionFromMouse(clientX, clientY) {
  const container = document.getElementById("region-list-container");
  const rect = container.getBoundingClientRect();

  let checkY = Math.max(rect.top + 1, Math.min(clientY, rect.bottom - 1));
  let checkX = rect.left + rect.width / 2;

  const el = document.elementFromPoint(checkX, checkY);
  const item = el?.closest(".region-item");

  if (item) {
    const index = parseInt(item.dataset.index);
    if (!isNaN(index)) {
      const start = Math.min(dragStartIndex, index);
      const end = Math.max(dragStartIndex, index);

      selectedIndices.clear();
      for (let i = start; i <= end; i++) selectedIndices.add(i);

      lastClickIndex = index;
      renderSelection();
      triggerPreviewUpdate();
    }
  }
}

// --- Preview Debounce State ---
let previewTimeout = null;
let lastSelectedJSON = "[]";

function triggerPreviewUpdate() {
  const currentNames = getSelectedNames();
  const currentJSON = JSON.stringify(currentNames);

  if (currentJSON !== lastSelectedJSON) {
    lastSelectedJSON = currentJSON;

    if (previewTimeout) clearTimeout(previewTimeout);

    previewTimeout = setTimeout(() => {
      if (currentMode === "modify") {
        updateModifyPreview(currentNames);
      } else {
        updatePreview(currentNames);
      }
    }, 50);

    updateButtons();
  }
}

function updateModifyPreview(names) {
  // Pure client-side: just redraw overlay canvas
  drawRegionOverlay();
  if (!names || names.length === 0) {
    document.getElementById("modify-status-text").innerText = hasModImage
      ? "Mod image merged. Ready to save."
      : "Select regions and click Modify Selected";
  } else {
    document.getElementById("modify-status-text").innerText = hasModImage
      ? `Merged preview. ${names.length} region(s) selected.`
      : `${names.length} region(s) selected`;
  }
}

function startAutoScroll() {
  if (autoScrollInterval) return;

  function scrollLoop() {
    if (!isDragSelecting) {
      stopAutoScroll();
      return;
    }

    if (autoScrollSpeed !== 0) {
      const container = document.getElementById("region-list-container");
      container.scrollTop += autoScrollSpeed;

      updateSelectionFromMouse(lastMouseX, lastMouseY);
    }

    triggerPreviewUpdate();

    autoScrollInterval = requestAnimationFrame(scrollLoop);
  }
  autoScrollInterval = requestAnimationFrame(scrollLoop);
}

function stopAutoScroll() {
  if (autoScrollInterval) {
    cancelAnimationFrame(autoScrollInterval);
    autoScrollInterval = null;
  }
  autoScrollSpeed = 0;
}

function onRegionMouseEnter(e, index) {
  // Deprecated in favor of global handler
}

window.addEventListener("mouseup", () => {
  if (isDragSelecting) {
    isDragSelecting = false;
    stopAutoScroll();
    window.removeEventListener("mousemove", onWindowMouseMove);
    if (currentMode === "extract") {
      updatePreview(getSelectedNames());
    } else {
      updateModifyPreview(getSelectedNames());
    }
    updateButtons();
  }
  viewState.isDragging = false;
});

function toggleIndex(index) {
  if (selectedIndices.has(index)) selectedIndices.delete(index);
  else selectedIndices.add(index);
}

function selectRange(start, end, keepExisting) {
  if (!keepExisting) selectedIndices.clear();
  for (let i = start; i <= end; i++) selectedIndices.add(i);
}

function renderSelection() {
  const items = document.querySelectorAll(".region-item");
  items.forEach((el, idx) => {
    if (selectedIndices.has(idx)) el.classList.add("selected");
    else el.classList.remove("selected");
  });
}

const previewContainer = document.getElementById("preview-container");
const previewImg = document.getElementById("preview-img");

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
    const bounds = modifyRegionBounds[name];
    if (!bounds) continue;
    const [bx, by, bw, bh, rotate] = bounds;

    // Bounds store ORIGINAL dimensions (before rotation)
    // Overlay needs to show STORED dimensions (after rotation)
    // So swap w/h when rotated
    const isRotated = rotate === 90 || rotate === 270;
    const drawW = isRotated ? bh : bw;
    const drawH = isRotated ? bw : bh;

    // Convert to screen coords
    const rx = topLeftX + bx * scale;
    const ry = topLeftY + by * scale;
    const rw = drawW * scale;
    const rh = drawH * scale;

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
      previewImg.onload = null;
    };
  } else {
    previewImg.style.display = "none";
    status.innerText = "Preview failed";
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

  // Update modify button state too
  const btnModSel = document.getElementById("btn-modify-sel");
  if (btnModSel) {
    btnModSel.disabled = selectedIndices.size === 0;
    btnModSel.innerText = `Modify Selected (${selectedIndices.size})`;
  }
}

async function extractSelected() {
  if (selectedIndices.size === 0) return;
  const names = Array.from(selectedIndices).map((i) => regionsData[i]);
  document.getElementById("status-text").innerText = "Extracting...";
  const result = await pywebview.api.extract_files(names);

  showToast(result, result.includes("Error") ? "error" : "success");
  document.getElementById("status-text").innerText = "Ready";
}

async function extractAll() {
  if (document.getElementById("count").innerText === "0") return;

  const confirmed = await showConfirm(
    "Are you sure you want to extract all regions?",
    "Confirm Extraction",
  );
  if (!confirmed) return;

  document.getElementById("status-text").innerText = "Extracting ALL...";
  const result = await pywebview.api.extract_files(null);

  showToast(result, result.includes("Error") ? "error" : "success");
  document.getElementById("status-text").innerText = "Ready";
}

// --- Modal Logic ---
function showConfirm(message, title = "Confirm") {
  return new Promise((resolve) => {
    const overlay = document.getElementById("modal-overlay");
    const titleEl = document.getElementById("modal-title");
    const msgEl = document.getElementById("modal-message");
    const btnConfirm = document.getElementById("btn-modal-confirm");
    const btnCancel = document.getElementById("btn-modal-cancel");

    titleEl.innerText = title;
    msgEl.innerText = message;
    overlay.classList.remove("hidden");

    if (document.activeElement) document.activeElement.blur();

    btnConfirm.focus();

    function cleanup() {
      overlay.classList.add("hidden");
      btnConfirm.removeEventListener("click", onConfirm);
      btnCancel.removeEventListener("click", onCancel);
      window.removeEventListener("keydown", onKey);
    }

    function onConfirm() {
      cleanup();
      resolve(true);
    }

    function onCancel() {
      cleanup();
      resolve(false);
    }

    function onKey(e) {
      if (e.key === "Escape") onCancel();
    }

    btnConfirm.addEventListener("click", onConfirm);
    btnCancel.addEventListener("click", onCancel);
    window.addEventListener("keydown", onKey);
  });
}

// --- Toast Logic ---
function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.innerText = message;

  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "1";
    toast.style.transform = "translateY(0)";

    toast.style.animation = "none";
    toast.offsetHeight; /* trigger reflow */

    toast.style.animation = "fadeOut 0.5s ease-out forwards";

    toast.addEventListener("animationend", () => {
      toast.remove();
    });
  }, 3000);
}
// ==========================================
//  KEYBOARD NAVIGATION (Arrow Keys)
// ==========================================
window.addEventListener("keydown", (e) => {
  if (regionsData.length === 0) return;
  if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;

  e.preventDefault();

  let newIndex = lastClickIndex;

  if (e.key === "ArrowDown") {
    newIndex++;
    if (newIndex >= regionsData.length) newIndex = regionsData.length - 1;
  } else if (e.key === "ArrowUp") {
    newIndex--;
    if (newIndex < 0) newIndex = 0;
  }

  if (newIndex === lastClickIndex && selectedIndices.size > 0) return;

  if (e.shiftKey) {
    if (dragStartIndex === -1) dragStartIndex = lastClickIndex;

    selectedIndices.clear();
    const start = Math.min(dragStartIndex, newIndex);
    const end = Math.max(dragStartIndex, newIndex);
    for (let i = start; i <= end; i++) selectedIndices.add(i);
  } else {
    selectedIndices.clear();
    selectedIndices.add(newIndex);
    dragStartIndex = newIndex;
  }

  lastClickIndex = newIndex;

  renderSelection();
  if (currentMode === "extract") {
    updatePreview(getSelectedNames());
  } else {
    updateModifyPreview(getSelectedNames());
  }
  updateButtons();

  const item = document.querySelector(`.region-item[data-index="${newIndex}"]`);
  if (item) item.scrollIntoView({ block: "nearest" });
});

// ==========================================
//  DRAG AND DROP SUPPORT
// ==========================================
const dropOverlay = document.getElementById("drop-overlay");

// 1. Global Prevention to stop browser from opening files
["dragover", "drop"].forEach((eventName) => {
  window.addEventListener(eventName, (e) => e.preventDefault(), false);
});

// 2. Window logic to show/hide overlay
window.addEventListener("dragenter", (e) => {
  e.preventDefault();
  if (e.dataTransfer.types.includes("Files")) {
    dropOverlay.classList.remove("hidden");
    dropOverlay.style.pointerEvents = "auto";
  }
});

// 3. Overlay specific logic
dropOverlay.addEventListener("dragover", (e) => {
  e.preventDefault();
});

dropOverlay.addEventListener("dragleave", (e) => {
  e.preventDefault();
  if (e.relatedTarget === null || !dropOverlay.contains(e.relatedTarget)) {
    dropOverlay.classList.add("hidden");
    dropOverlay.style.pointerEvents = "none";
  }
});

dropOverlay.addEventListener("drop", (e) => {
  e.preventDefault();
  dropOverlay.classList.add("hidden");
  dropOverlay.style.pointerEvents = "none";
  // Python handler (DOMEventHandler) will continue to process the file
});

// Callback called from Python after successful drop loading
window.onAtlasLoadedFromPython = async () => {
  // If we were in modify mode, switch back
  if (currentMode === "modify") {
    setMode("extract");
    modifyRegionBounds = {}; // Clear modify state
    hasModImage = false;
  }
  selectedIndices.clear();
  lastClickIndex = -1;
  document.getElementById("preview-img").style.display = "none";
  resetPreview();
  clearOverlay(); // Ensure overlay is cleared
  updateButtons();
  await loadRegions();
  showToast("Atlas loaded via drag & drop.", "success");
};

// Callback called from Python after mod image processed via drag-drop
window.onModImageProcessed = (data) => {
  if (data) {
    onModPreviewReceived(data);
    showToast("Mod image loaded via drag & drop.", "success");
  }
};

// ==========================================
//  CONTEXT MENU (Right-click Copy)
// ==========================================
const contextMenu = document.getElementById("context-menu");

previewContainer.addEventListener("contextmenu", (e) => {
  e.preventDefault();

  // Only show in extract mode and when there's a visible preview
  if (currentMode !== "extract") return;
  if (previewImg.style.display === "none" || !previewImg.src) return;

  contextMenu.style.left = e.clientX + "px";
  contextMenu.style.top = e.clientY + "px";
  contextMenu.classList.remove("hidden");
});

window.addEventListener("click", () => {
  contextMenu.classList.add("hidden");
});

window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") contextMenu.classList.add("hidden");
});

async function copyPreviewImage() {
  contextMenu.classList.add("hidden");
  try {
    // Draw the preview img onto an offscreen canvas and copy as PNG
    const img = previewImg;
    if (!img.naturalWidth || !img.naturalHeight) {
      showToast("No image to copy.", "error");
      return;
    }

    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(img, 0, 0);

    const blob = await new Promise((resolve) =>
      canvas.toBlob(resolve, "image/png"),
    );
    if (!blob) {
      showToast("Failed to copy image.", "error");
      return;
    }

    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
    showToast("Image copied to clipboard.", "success");
  } catch (e) {
    console.error(e);
    showToast("Failed to copy image.", "error");
  }
}

// ==========================================
//  UPDATE NOTIFICATION (Persistent Toast)
// ==========================================

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const digits = unitIndex === 0 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unitIndex]}`;
}

async function openExternalUrl(url, invalidMessage = "Invalid URL.") {
  try {
    const parsed = new URL(String(url));
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      showToast(invalidMessage, "error");
      return;
    }

    if (window.pywebview && pywebview.api && pywebview.api.open_url) {
      const result = await pywebview.api.open_url(parsed.toString());
      if (!result || !result.ok) {
        showToast((result && result.error) || "Failed to open URL.", "error");
      }
      return;
    }

    window.open(parsed.toString(), "_blank");
  } catch (err) {
    console.error(err);
    showToast(invalidMessage, "error");
  }
}

window.showUpdateNotification = function (...args) {
  let payload = null;

  if (args.length === 1 && typeof args[0] === "object" && args[0] !== null) {
    payload = args[0];
  } else {
    const legacyVersion = String(args[0] || "");
    // Backward compatibility with old signature
    payload = {
      latestVersion: legacyVersion,
      releaseName: String(args[1] || ""),
      releaseUrl: String(args[2] || ""),
      tagName: legacyVersion
        ? legacyVersion.startsWith("v")
          ? legacyVersion
          : `v${legacyVersion}`
        : "",
      sourceTreeUrl: String(args[2] || ""),
      action: "open_source_tag",
    };
  }

  const latestVersion = String(payload.latestVersion || "");
  const releaseName = String(payload.releaseName || latestVersion || "New release");
  const releaseUrl = String(payload.releaseUrl || "");
  const sourceTreeUrl = String(payload.sourceTreeUrl || releaseUrl);
  const tagName = String(payload.tagName || latestVersion || "");
  const action = payload.action === "download" ? "download" : "open_source_tag";

  // Remove any existing update toast first
  const existing = document.getElementById("update-toast");
  if (existing) existing.remove();

  const toast = document.createElement("div");
  toast.id = "update-toast";
  toast.className = "toast-update";
  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  icon.setAttribute("class", "toast-update-icon");
  icon.setAttribute("viewBox", "0 -960 960 960");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute(
    "d",
    "M440-320v-326L336-542l-56-58 200-200 200 200-56 58-104-104v326h-80ZM240-160q-33 0-56.5-23.5T160-240v-120h80v120h480v-120h80v120q0 33-23.5 56.5T720-160H240Z",
  );
  icon.appendChild(path);

  const title = document.createElement("span");
  title.className = "toast-update-title";
  title.textContent = `Update available - ${latestVersion}`;

  const sub = document.createElement("span");
  sub.className = "toast-update-sub";
  sub.textContent = releaseName;

  const actionBtn = document.createElement("button");
  actionBtn.className = "toast-update-btn-go";

  let phase = action === "download" ? "download" : "external";
  let pollTimer = null;

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function pollDownloadProgress() {
    try {
      const progress = await pywebview.api.get_update_download_progress();
      if (!progress) return;

      const status = String(progress.status || "idle");
      if (status === "downloading") {
        const total = Number(progress.total_bytes || 0);
        const downloaded = Number(progress.downloaded_bytes || 0);
        if (total > 0) {
          const percent = Number(progress.percent || 0);
          sub.textContent = `Downloading... ${percent}% (${formatBytes(downloaded)} / ${formatBytes(total)})`;
          actionBtn.textContent = `Downloading ${percent}%`;
        } else {
          sub.textContent = `Downloading... ${formatBytes(downloaded)}`;
          actionBtn.textContent = "Downloading...";
        }
      } else if (status === "error") {
        stopPolling();
      }
    } catch (err) {
      console.error(err);
    }
  }

  async function handleDownload() {
    actionBtn.disabled = true;
    actionBtn.textContent = "Downloading...";
    sub.textContent = "Downloading update package...";

    stopPolling();
    pollTimer = setInterval(pollDownloadProgress, 350);

    try {
      const result = await pywebview.api.download_update();
      stopPolling();

      if (result && result.ok) {
        phase = "restart";
        actionBtn.disabled = false;
        actionBtn.textContent = "Restart to Update";
        sub.textContent = `Downloaded v${result.version}. Restart to install.`;
      } else {
        const message = (result && result.error) || "Update download failed.";
        showToast(message, "error");
        phase = "download";
        actionBtn.disabled = false;
        actionBtn.textContent = "Download Update";
        sub.textContent = releaseName;
      }
    } catch (err) {
      stopPolling();
      console.error(err);
      showToast("Update download failed.", "error");
      phase = "download";
      actionBtn.disabled = false;
      actionBtn.textContent = "Download Update";
      sub.textContent = releaseName;
    }
  }

  async function handleRestartToUpdate() {
    actionBtn.disabled = true;
    actionBtn.textContent = "Restarting...";
    sub.textContent = "Restarting app to install update...";

    try {
      const result = await pywebview.api.restart_and_install_update();
      if (!result || !result.ok) {
        const message = (result && result.error) || "Failed to restart for update.";
        showToast(message, "error");
        actionBtn.disabled = false;
        actionBtn.textContent = "Restart to Update";
        sub.textContent = releaseName;
      }
    } catch (err) {
      console.error(err);
      showToast("Failed to restart for update.", "error");
      actionBtn.disabled = false;
      actionBtn.textContent = "Restart to Update";
      sub.textContent = releaseName;
    }
  }

  actionBtn.addEventListener("click", () => {
    if (phase === "external") {
      openExternalUrl(sourceTreeUrl, "Invalid source URL.");
      return;
    }

    if (phase === "download") {
      handleDownload();
      return;
    }

    if (phase === "restart") {
      handleRestartToUpdate();
    }
  });

  if (phase === "external") {
    actionBtn.textContent = tagName ? `View Source (${tagName})` : "View Source";
  } else {
    actionBtn.textContent = "Download Update";
  }

  const closeBtn = document.createElement("button");
  closeBtn.className = "toast-update-btn-close";
  closeBtn.textContent = "x";
  closeBtn.addEventListener("click", () => {
    stopPolling();
    toast.remove();
  });

  toast.appendChild(icon);
  toast.appendChild(title);
  toast.appendChild(sub);
  toast.appendChild(actionBtn);
  toast.appendChild(closeBtn);

  document.getElementById("right-panel").appendChild(toast);
};

window.showUpdateInstallFailed = function (payload) {
  const info = payload && typeof payload === "object" ? payload : {};
  const message = String(
    info.message || "Update installation failed. The app was relaunched.",
  );
  const logPath = String(info.logPath || "");
  const releaseUrl = String(info.releaseUrl || "");

  const existing = document.getElementById("update-failed-toast");
  if (existing) existing.remove();

  const toast = document.createElement("div");
  toast.id = "update-failed-toast";
  toast.className = "toast-update toast-update-error";

  const title = document.createElement("span");
  title.className = "toast-update-title";
  title.textContent = "Update failed";

  const sub = document.createElement("span");
  sub.className = "toast-update-sub";
  sub.textContent = message;

  const logBtn = document.createElement("button");
  logBtn.className = "toast-update-btn-go";
  logBtn.textContent = "Open Log";
  logBtn.disabled = !logPath;
  logBtn.addEventListener("click", async () => {
    if (!logPath) return;
    try {
      const result = await pywebview.api.open_update_log(logPath);
      if (!result || !result.ok) {
        showToast((result && result.error) || "Failed to open log.", "error");
      }
    } catch (err) {
      console.error(err);
      showToast("Failed to open log.", "error");
    }
  });

  const releaseBtn = document.createElement("button");
  releaseBtn.className = "toast-update-btn-go";
  releaseBtn.textContent = "Download Manually";
  releaseBtn.disabled = !releaseUrl;
  releaseBtn.addEventListener("click", () => {
    if (!releaseUrl) return;
    openExternalUrl(releaseUrl, "Invalid release URL.");
  });

  const closeBtn = document.createElement("button");
  closeBtn.className = "toast-update-btn-close";
  closeBtn.textContent = "x";
  closeBtn.addEventListener("click", () => toast.remove());

  toast.appendChild(title);
  toast.appendChild(sub);
  toast.appendChild(logBtn);
  toast.appendChild(releaseBtn);
  toast.appendChild(closeBtn);

  document.getElementById("right-panel").appendChild(toast);
};
