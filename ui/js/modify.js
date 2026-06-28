// Atlas Toolkit UI module
function updateModifyActionButtons() {
  document.getElementById("btn-save-mod").disabled = !hasModImage;
  document.getElementById("btn-reset-mod").disabled = !hasModImage;
}
function applyModifyView(data, statusText) {
  modifyRegionBounds = data.regions || {};
  modifyOverlayRects = data.overlayRects || {};
  setupModifyPages(data);
  modifiedRegionNames = new Set(data.modifiedRegions || []);
  renderRegionList();
  setStatus(statusText);
  updateModifyActionButtons();
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
    } else {
      applyTransform();
    }
    previewImg.onload = null;
  };
}

async function resetModify() {
  if (!hasModImage) return;
  try {
    const data = await pywebview.api.reset_modify_mode();
    if (data) {
      hasModImage = false;
      applyModifyView(data, "Select regions and click Modify Selected");
      showToast("Modifications reset.", "success");
    } else {
      showToast("Failed to reset modifications.", "error");
    }
  } catch (e) {
    console.error(e);
    showToast("Failed to reset modifications.", "error");
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
  modifyOverlayRects = {};
  modifyPages = [];
  modifyRegionPages = {};
  modifyActivePageIndex = 0;
  document.getElementById("modify-page-switcher").classList.add("hidden");
  hasModImage = false;
  modifiedRegionNames = new Set();
  renderRegionList();
  clearOverlay();
  // Restore preview from current selection
  previewImg.style.display = "none";
  resetPreview();
  setStatus("Ready");
}

async function modifySelected() {
  const names = getSelectedNames();
  if (names.length === 0) {
    showToast("Select at least one region to modify.", "error");
    return;
  }
  try {
    setStatus("Selecting mod image...");
    const repack = document.getElementById("chk-repack").checked;
    const result = await pywebview.api.select_mod_image(names, repack);
    if (result) {
      onModPreviewReceived(result);
    } else {
      setStatus("Cancelled or no image selected.");
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
  if (data.overlayRects) {
    modifyOverlayRects = data.overlayRects;
  }
  if (data.modifiedRegions) {
    modifiedRegionNames = new Set(data.modifiedRegions);
    renderRegionList();
  }
  // Refresh multi-page state (regions were redistributed across pages by repack)
  setupModifyPages(data);
  previewImg.src = data.image;
  previewImg.style.display = "block";
  setStatus("Mod image merged. Ready to save.");
  updateModifyActionButtons();

  previewImg.onload = function () {
    resetPreview();
    const containerW = previewContainer.clientWidth - 40;
    const containerH = previewContainer.clientHeight - 40;
    const imgW = previewImg.naturalWidth;
    const imgH = previewImg.naturalHeight;

    setStatus(`Merged preview (${imgW}x${imgH}). Ready to save.`);

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
    setStatus("Saving...");
    const result = await pywebview.api.save_modified();
    if (result.startsWith("Error") || result === "Cancelled") {
      showToast(result, result === "Cancelled" ? "info" : "error");
    } else {
      showToast(result, "success");
    }
    setStatus(result);
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
  setStatus(
    e.target.checked ? "Applying repack..." : "Reverting repack...",
  );
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
