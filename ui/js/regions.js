// Atlas Toolkit UI module
function regionDisplayName(name) {
  return modifiedRegionNames.has(name) ? `${name}*` : name;
}

function renderRegionList() {
  const listEl = document.getElementById("region-list");
  listEl.innerHTML = "";
  regionsData.forEach((name, index) => {
    const li = document.createElement("li");
    li.className = "region-item";
    if (modifiedRegionNames.has(name)) {
      li.classList.add("modified");
    }
    li.innerText = regionDisplayName(name);
    li.dataset.index = index;
    li.addEventListener("mousedown", (e) => onRegionMouseDown(e, index, name));
    li.addEventListener("mouseenter", (e) => onRegionMouseEnter(e, index));
    listEl.appendChild(li);
  });
  renderSelection();
}

function getSelectedNames() {
  return Array.from(selectedIndices)
    .sort((a, b) => a - b)
    .map((i) => regionsData[i]);
}

// --- Auto-Scroll State ---
var autoScrollSpeed = 0;
var autoScrollInterval = null;
var SCROLL_ZONE_SIZE = 50;
var MAX_SCROLL_SPEED = 15;
var lastMouseX = 0;
var lastMouseY = 0;

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
var previewTimeout = null;
var lastSelectedJSON = "[]";

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
