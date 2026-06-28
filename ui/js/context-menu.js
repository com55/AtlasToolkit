// Atlas Toolkit UI module
var contextMenu = document.getElementById("context-menu");

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
    const blob = await getPreviewPngBlob();
    if (!blob) {
      showToast("No image to copy.", "error");
      return;
    }

    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
    showToast("Image copied to clipboard.", "success");
  } catch (e) {
    console.error(e);
    showToast("Failed to copy image.", "error");
  }
}
