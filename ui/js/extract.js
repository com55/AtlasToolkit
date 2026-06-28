// Atlas Toolkit UI module
function getPreviewPngBlob() {
  const img = previewImg;
  if (!img.naturalWidth || !img.naturalHeight) return null;

  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  canvas.getContext("2d").drawImage(img, 0, 0);

  return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
}

function previewSaveDefaultName(names) {
  if (!names || names.length === 0) return "image.png";
  const safe = names.map((n) => n.replace(/[<>:"/\\|?*]/g, "_"));
  if (safe.length === 1) return `${safe[0]}.png`;
  if (safe.length <= 5) return `${safe.join("+")}.png`;
  const more = safe.length - 5;
  return `${safe.slice(0, 5).join("+")}+ ${more} more.png`;
}

async function saveMergedImage() {
  try {
    const blob = await getPreviewPngBlob();
    if (!blob) {
      showToast("No image to save.", "error");
      return;
    }

    const reader = new FileReader();
    const dataUrl = await new Promise((resolve, reject) => {
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });

    const defaultName = previewSaveDefaultName(getSelectedNames());
    const result = await pywebview.api.save_preview_image(dataUrl, defaultName);
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

