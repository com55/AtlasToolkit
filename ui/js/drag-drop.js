// Atlas Toolkit UI module
var dropOverlay = document.getElementById("drop-overlay");

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
    modifyOverlayRects = {};
    hasModImage = false;
    modifiedRegionNames = new Set();
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
