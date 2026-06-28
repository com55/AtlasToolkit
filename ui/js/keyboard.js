// Atlas Toolkit UI module
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
