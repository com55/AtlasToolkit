// --- Data State ---
let regionsData = [];
let selectedIndices = new Set();
let lastClickIndex = -1;
let isDragSelecting = false;
let dragStartIndex = -1;
let viewState = {
  scale: 1,
  x: 0,
  y: 0,
  isDragging: false,
  startX: 0,
  startY: 0,
};

window.addEventListener("pywebviewready", async function () {
  const loaded = await pywebview.api.startup_check();
  if (loaded) await loadRegions();
});

// --- Logic ---
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
  if (regionsData.length > 0)
    document.getElementById("status-text").innerText = "Atlas loaded.";
}

function getSelectedNames() {
  return Array.from(selectedIndices)
    .sort((a, b) => a - b)
    .map((i) => regionsData[i]);
}

// --- Auto-Scroll State ---
let autoScrollSpeed = 0;
let autoScrollInterval = null;
const SCROLL_ZONE_SIZE = 50; // px from edge to trigger scroll
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
    // Ctrl-drag not typically supported for list selection in standard OS, keeping simple
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
  triggerPreviewUpdate(); // Uses debounce
  // updateButtons called inside triggerPreviewUpdate if changed, but safe to call here too

  // Start global tracking
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

  // 1. Calculate Auto-Scroll Speed
  if (e.clientY < rect.top + SCROLL_ZONE_SIZE) {
    // Scrolling Up
    const dist = Math.max(0, rect.top + SCROLL_ZONE_SIZE - e.clientY);
    autoScrollSpeed = -(dist / SCROLL_ZONE_SIZE) * MAX_SCROLL_SPEED;
  } else if (e.clientY > rect.bottom - SCROLL_ZONE_SIZE) {
    // Scrolling Down
    const dist = Math.max(0, e.clientY - (rect.bottom - SCROLL_ZONE_SIZE));
    autoScrollSpeed = (dist / SCROLL_ZONE_SIZE) * MAX_SCROLL_SPEED;
  } else {
    autoScrollSpeed = 0;
  }

  // 2. Resolve target index from mouse position
  updateSelectionFromMouse(e.clientX, e.clientY);
}

function updateSelectionFromMouse(clientX, clientY) {
  const container = document.getElementById("region-list-container");
  const rect = container.getBoundingClientRect();

  // Clamp Y to container bounds for element detection
  // checking slightly inside to ensure we hit an element
  let checkY = Math.max(rect.top + 1, Math.min(clientY, rect.bottom - 1));
  let checkX = rect.left + rect.width / 2; // Check center of list

  const el = document.elementFromPoint(checkX, checkY);
  const item = el?.closest(".region-item");

  if (item) {
    const index = parseInt(item.dataset.index);
    if (!isNaN(index)) {
      // Update Selection Range
      const start = Math.min(dragStartIndex, index);
      const end = Math.max(dragStartIndex, index);

      // If not holding Ctrl, clear previous
      // (Assuming standard drag behavior is "Set Selection", not "Add to Selection")
      selectedIndices.clear();
      for (let i = start; i <= end; i++) selectedIndices.add(i);

      lastClickIndex = index;
      renderSelection();
      // Use shared trigger for debounce
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

    // Clear any pending update
    if (previewTimeout) clearTimeout(previewTimeout);

    // Set new debounce timer (e.g., 50ms)
    previewTimeout = setTimeout(() => {
      updatePreview(currentNames);
    }, 50);

    updateButtons(); // Buttons can update immediately
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

      // Update selection based on new scroll position (list moving under stationary mouse)
      updateSelectionFromMouse(lastMouseX, lastMouseY);
    }

    // Check for selection change and update preview (debounced)
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

// Remove old onRegionMouseEnter as it conflicts/is redundant with global router
function onRegionMouseEnter(e, index) {
  // Deprecated in favor of global handler
}

window.addEventListener("mouseup", () => {
  if (isDragSelecting) {
    isDragSelecting = false;
    stopAutoScroll();
    window.removeEventListener("mousemove", onWindowMouseMove);
    updatePreview(getSelectedNames()); // Final update
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
}

async function updatePreview(names) {
  const status = document.getElementById("status-text");
  // names is now a list
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

    // รอให้รูปโหลดก่อนเพื่อหาขนาดจริง
    previewImg.onload = function () {
      resetPreview();
      const containerW = previewContainer.clientWidth - 40; // เผื่อ padding
      const containerH = previewContainer.clientHeight - 40;
      const imgW = previewImg.naturalWidth;
      const imgH = previewImg.naturalHeight;

      // Update status with size
      if (names.length === 1) {
        status.innerText = `Previewing: ${names[0]} (${imgW}x${imgH})`;
      } else {
        status.innerText = `Previewing: ${names.length} regions (${imgW}x${imgH})`;
      }

      // ถ้าขนาดรูปใหญ่กว่าหน้าต่าง ให้ปรับ Fit
      if (imgW > containerW || imgH > containerH) {
        const scaleW = containerW / imgW;
        const scaleH = containerH / imgH;
        viewState.scale = Math.min(scaleW, scaleH);
        applyTransform();
      }
      previewImg.onload = null; // ป้องกัน Loop ถ้าเปลี่ยน src เดิม
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
    viewState.scale + direction * zoomIntensity * viewState.scale; // Zoom proportional to current scale feels better

  if (newScale > 0.1 && newScale < 50) {
    // Get mouse position relative to container center
    const rect = previewContainer.getBoundingClientRect();
    const cx = rect.width / 2;
    const cy = rect.height / 2;
    const mx = e.clientX - rect.left - cx;
    const my = e.clientY - rect.top - cy;

    // Calculate new position to keep mouse over the same image point
    // Formula: newPos = mousePos - (mousePos - oldPos) * (newScale / oldScale)
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

    // Defocus any existing element to prevent accidental double triggering
    if (document.activeElement) document.activeElement.blur();

    // Focus confirm button for keyboard accessibility
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

  // Remove after 3 seconds
  setTimeout(() => {
    // 1. Lock the visual state from the end of slideIn
    // (Prevent snapping back to opacity:0 / translateY(20px))
    toast.style.opacity = "1";
    toast.style.transform = "translateY(0)";

    // 2. Clear old animation
    toast.style.animation = "none";
    toast.offsetHeight; /* trigger reflow */

    // 3. Start fadeOut
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
  // ถ้าไม่มีข้อมูล หรือไม่ได้กดปุ่มลูกศร ให้ข้ามไป
  if (regionsData.length === 0) return;
  if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;

  e.preventDefault(); // ป้องกันหน้าจอเลื่อนเอง

  let newIndex = lastClickIndex;

  if (e.key === "ArrowDown") {
    newIndex++;
    if (newIndex >= regionsData.length) newIndex = regionsData.length - 1;
  } else if (e.key === "ArrowUp") {
    newIndex--;
    if (newIndex < 0) newIndex = 0;
  }

  // ถ้าค่า Index ไม่เปลี่ยน (เช่น สุดขอบแล้ว) ไม่ต้องทำอะไร
  if (newIndex === lastClickIndex && selectedIndices.size > 0) return;

  // Logic การเลือก
  if (e.shiftKey) {
    // Shift: เลือกช่วง (Excel Style)
    // ต้องหาจุด Anchor เดิม (dragStartIndex) ถ้าไม่มีให้ใช้ตัวปัจจุบัน
    if (dragStartIndex === -1) dragStartIndex = lastClickIndex;

    selectedIndices.clear();
    const start = Math.min(dragStartIndex, newIndex);
    const end = Math.max(dragStartIndex, newIndex);
    for (let i = start; i <= end; i++) selectedIndices.add(i);
  } else {
    // Normal: เลือกตัวเดียว
    selectedIndices.clear();
    selectedIndices.add(newIndex);
    dragStartIndex = newIndex; // Reset anchor
  }

  lastClickIndex = newIndex;

  // Update UI
  renderSelection();
  updatePreview(getSelectedNames());
  updateButtons();

  // Scroll ไปหาตัวที่เลือก
  const item = document.querySelector(`.region-item[data-index="${newIndex}"]`);
  if (item) item.scrollIntoView({ block: "nearest" });
});

// ==========================================
//  DRAG AND DROP SUPPORT (.atlas files)
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
  // Only hide if we actually leave the overlay area
  if (e.relatedTarget === null || !dropOverlay.contains(e.relatedTarget)) {
    dropOverlay.classList.add("hidden");
    dropOverlay.style.pointerEvents = "none";
  }
});

dropOverlay.addEventListener("drop", (e) => {
  e.preventDefault(); // CRITICAL: Stop browser from opening file
  dropOverlay.classList.add("hidden");
  dropOverlay.style.pointerEvents = "none";
  // Python handler (DOMEventHandler) will continue to process the file
});

// Callback called from Python after successful drop loading
window.onAtlasLoadedFromPython = async () => {
  selectedIndices.clear();
  lastClickIndex = -1;
  document.getElementById("preview-img").style.display = "none";
  resetPreview();
  updateButtons();
  await loadRegions();
  showToast("Atlas loaded via drag & drop.", "success");
};
