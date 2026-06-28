// Atlas Toolkit UI module
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
  document.getElementById("count").innerText = regionsData.length;
  renderRegionList();
  if (regionsData.length > 0) {
    setStatus("Atlas loaded.");
    document.getElementById("btn-extract-all").disabled = false;
  } else {
    document.getElementById("btn-extract-all").disabled = true;
  }
  updateModeToggleUI();
}
