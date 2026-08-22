// Atlas Toolkit UI module
function previewSaveDefaultName(names) {
  if (!names || names.length === 0) return "image.png";
  const safe = names.map((n) => n.replace(/[<>:"/\\|?*]/g, "_"));
  if (safe.length === 1) return `${safe[0]}.png`;
  if (safe.length <= 5) return `${safe.join("+")}.png`;
  const more = safe.length - 5;
  return `${safe.slice(0, 5).join("+")}+ ${more} more.png`;
}

async function saveMergedImage() {
  const names = getSelectedNames();
  if (!names.length) {
    showToast("No regions selected.", "error");
    return;
  }

  try {
    const defaultName = previewSaveDefaultName(names);
    const result = await pywebview.api.save_preview(names, defaultName);
    if (result === "Cancelled") {
      showToast(result, "info");
    } else if (result.startsWith("Error")) {
      showToast(result, "error");
    } else {
      showToast(result, "success");
    }
  } catch (e) {
    console.error(e);
    showToast("Failed to save image.", "error");
  }
}

async function extractSelected() {
  if (selectedIndices.size === 0) return;
  const names = Array.from(selectedIndices).map((i) => regionsData[i]);
  setStatus("Extracting...");
  const result = await pywebview.api.extract_files(names);

  showToast(result, result.includes("Error") ? "error" : "success");
  setStatus("Ready");
}

async function extractAll() {
  if (document.getElementById("count").innerText === "0") return;

  const confirmed = await showConfirm(
    "Are you sure you want to extract all regions?",
    "Confirm Extraction",
  );
  if (!confirmed) return;

  setStatus("Extracting ALL...");
  const result = await pywebview.api.extract_files(null);

  showToast(result, result.includes("Error") ? "error" : "success");
  setStatus("Ready");
}
