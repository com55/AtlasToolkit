// Atlas Toolkit UI module
function updateModeToggleUI() {
  const extractBtn = document.getElementById("mode-extract");
  const modifyBtn = document.getElementById("mode-modify");
  extractBtn.classList.toggle("active", currentMode === "extract");
  modifyBtn.classList.toggle("active", currentMode === "modify");
  const count = parseInt(document.getElementById("count").innerText, 10) || 0;
  modifyBtn.disabled = count === 0;
}

function clearRegionSelection() {
  selectedIndices.clear();
  lastClickIndex = -1;
  renderSelection();
  updateButtons();
}

function setMode(mode) {
  currentMode = mode;
  const extractControls = document.getElementById("extract-controls");
  const modifyControls = document.getElementById("modify-controls");
  const repackOptions = document.getElementById("repack-options");
  const saveBtn = document.getElementById("btn-save-mod");
  const dropMsg = document.getElementById("drop-message-text");

  if (mode === "modify") {
    extractControls.classList.add("hidden");
    modifyControls.classList.remove("hidden");
    repackOptions.classList.remove("hidden");
    saveBtn.classList.remove("hidden");
    dropMsg.textContent = "Drop image to modify, or .atlas to load";
  } else {
    extractControls.classList.remove("hidden");
    modifyControls.classList.add("hidden");
    repackOptions.classList.add("hidden");
    saveBtn.classList.add("hidden");
    dropMsg.textContent = "Drop .atlas file here to load";
    clearOverlay();
  }
  updateModeToggleUI();
  renderSelection();
  updateButtons();

  if (mode === "extract") {
    updatePreview(getSelectedNames());
  } else {
    updateModifyPreview(getSelectedNames());
  }
}

async function enterModifyMode() {
  try {
    const data = await pywebview.api.enter_modify_mode();
    if (data) {
      setMode("modify");
      hasModImage = false;
      applyModifyView(data, "Select regions and click Modify Selected");
    } else {
      showToast("Load an atlas first.", "error");
    }
  } catch (e) {
    console.error(e);
    showToast("Failed to enter modify mode.", "error");
  }
}
async function exitModifyMode() {
  const ok = await confirmDiscardModifications();
  if (!ok) return false;
  try {
    await pywebview.api.exit_modify_mode();
  } catch (e) {
    console.error(e);
    return false;
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
  await updatePreview(getSelectedNames());
  return true;
}
