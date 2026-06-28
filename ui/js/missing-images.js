// Missing atlas page image picker (modal) — mirrors js-project dialogs.js

function isMissingDialogOpen() {
  return document.body.dataset.missingDialogOpen === "true";
}
window.isMissingDialogOpen = isMissingDialogOpen;

function formatSelectedImageLabel(path, pageName) {
  if (!path) return "No image selected";
  const parts = String(path).split(/[/\\]/);
  const name = parts[parts.length - 1];
  if (!name) return `Selected for ${pageName}`;
  if (/^\d+(\.[a-z0-9]+)?$/i.test(name)) return "Selected";
  return name;
}

let _missingDialogState = null;

function isFileDragEvent(e) {
  const types = e.dataTransfer?.types;
  if (!types) return false;
  return [...types].includes("Files");
}

function fileItemDragHint(item) {
  if (item.kind !== "file") return "skip";
  if (item.type === "image/png") return "png";
  if (item.type && item.type !== "image/png") return "blocked";
  try {
    const entry = item.webkitGetAsEntry?.() ?? item.getAsEntry?.();
    if (entry?.isFile && entry.name) {
      return /\.png$/i.test(entry.name) ? "png" : "blocked";
    }
  } catch (_) {
    /* entry name unavailable during drag */
  }
  return "unknown";
}

/** During drag: png | blocked | none — default blocked when type cannot be verified. */
function missingDragFileHint(e) {
  if (!isFileDragEvent(e)) return "none";
  const types = [...e.dataTransfer.types];
  if (types.includes("image/png")) return "png";

  let sawPng = false;
  let sawUnknown = false;
  try {
    for (const item of e.dataTransfer.items) {
      const hint = fileItemDragHint(item);
      if (hint === "skip") continue;
      if (hint === "blocked") return "blocked";
      if (hint === "png") {
        sawPng = true;
      } else if (hint === "unknown") {
        sawUnknown = true;
      }
    }
  } catch (_) {
    /* items unavailable during drag on some hosts */
  }

  if (sawPng && !sawUnknown) return "png";
  return "blocked";
}

function missingImageRowAt(clientX, clientY) {
  if (typeof clientX !== "number" || typeof clientY !== "number") return null;
  const el = document.elementFromPoint(clientX, clientY);
  return el ? el.closest(".missing-images-row") : null;
}

function clearMissingDragUi() {
  if (!_missingDialogState) return;
  const { overlay, rowByPage } = _missingDialogState;
  overlay.classList.remove(
    "missing-images-file-drag",
    "missing-images-type-blocked",
    "missing-images-drop-ok"
  );
  for (const [, row] of rowByPage) {
    row.classList.remove("missing-images-drop-target", "drag-over");
  }
  _missingDialogState.dragDepth = 0;
  _missingDialogState.dropAllowed = false;
}

function updateMissingDragUi(e) {
  if (!_missingDialogState) return;
  const { overlay, rowByPage } = _missingDialogState;
  const hint = missingDragFileHint(e);
  if (hint === "none") {
    clearMissingDragUi();
    return;
  }

  overlay.classList.add("missing-images-file-drag");
  overlay.classList.toggle("missing-images-type-blocked", hint !== "png");

  const row = missingImageRowAt(e.clientX, e.clientY);
  const rowAllowed = !!row && hint === "png";
  _missingDialogState.dropAllowed = rowAllowed;

  for (const [, r] of rowByPage) {
    const onTarget = r === row && rowAllowed;
    r.classList.toggle("missing-images-drop-target", onTarget);
    r.classList.toggle("drag-over", onTarget);
  }

  overlay.classList.toggle("missing-images-drop-ok", rowAllowed);

  if (!rowAllowed) {
    e.dataTransfer.dropEffect = "none";
  } else {
    e.dataTransfer.dropEffect = "copy";
  }
}

function bindMissingImagesDragLayer(overlay, rowByPage) {
  let dragDepth = 0;

  const onDragEnter = (e) => {
    if (!isFileDragEvent(e)) return;
    e.preventDefault();
    e.stopPropagation();
    dragDepth += 1;
    _missingDialogState.dragDepth = dragDepth;
    updateMissingDragUi(e);
  };

  const onDragOver = (e) => {
    if (!isFileDragEvent(e)) return;
    e.preventDefault();
    updateMissingDragUi(e);
  };

  const onDragLeave = (e) => {
    if (!isFileDragEvent(e)) return;
    e.stopPropagation();
    if (e.relatedTarget && overlay.contains(e.relatedTarget)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    _missingDialogState.dragDepth = dragDepth;
    if (dragDepth === 0) {
      clearMissingDragUi();
    }
  };

  const onDrop = (e) => {
    if (!isFileDragEvent(e)) return;
    e.preventDefault();
    dragDepth = 0;
    clearMissingDragUi();
    // Bubble to document so pywebview can deliver the file path to Python.
  };

  overlay.addEventListener("dragenter", onDragEnter, true);
  overlay.addEventListener("dragover", onDragOver, true);
  overlay.addEventListener("dragleave", onDragLeave, true);
  overlay.addEventListener("drop", onDrop, true);

  return () => {
    overlay.removeEventListener("dragenter", onDragEnter, true);
    overlay.removeEventListener("dragover", onDragOver, true);
    overlay.removeEventListener("dragleave", onDragLeave, true);
    overlay.removeEventListener("drop", onDrop, true);
  };
}

function showMissingAtlasImagesDialog(missingPages, atlasDir) {
  return new Promise((resolve) => {
    const pages = Array.from(new Set((missingPages || []).filter(Boolean)));
    if (pages.length === 0) {
      resolve({});
      return;
    }

    const selectedByPage = {};
    const rowByPage = new Map();
    const statusByPage = new Map();
    const btnByPage = new Map();
    let confirmBtn = null;
    let unbindDrag = null;

    const overlay = document.createElement("div");
    overlay.className = "missing-images-overlay";

    const dialog = document.createElement("div");
    dialog.className = "missing-images-dialog";

    const title = document.createElement("h3");
    title.className = "missing-images-title";
    title.innerText = "Missing image files for atlas pages";

    const subtitle = document.createElement("p");
    subtitle.className = "missing-images-subtitle";
    subtitle.innerText =
      "Choose one image for each page below, or drag & drop a PNG onto a row.";

    const list = document.createElement("div");
    list.className = "missing-images-list";

    function updateConfirmState() {
      if (confirmBtn) {
        confirmBtn.disabled = !pages.every((p) => !!selectedByPage[p]);
      }
    }

    function applySelection(pageName, path) {
      if (!path || !/\.png$/i.test(path)) return;
      const row = rowByPage.get(pageName);
      const statusEl = statusByPage.get(pageName);
      const actionBtn = btnByPage.get(pageName);
      if (!row || !statusEl || !actionBtn) return;
      selectedByPage[pageName] = path;
      statusEl.innerText = formatSelectedImageLabel(path, pageName);
      statusEl.title = path;
      row.classList.add("selected");
      actionBtn.innerText = "Change";
      actionBtn.classList.add("btn-save");
      updateConfirmState();
    }

    _missingDialogState = {
      applySelection,
      rowByPage,
      overlay,
      dragDepth: 0,
      dropAllowed: false,
    };

    for (const pageName of pages) {
      const row = document.createElement("div");
      row.className = "missing-images-row";
      row.dataset.pageName = pageName;

      const pageEl = document.createElement("div");
      pageEl.className = "missing-images-page";
      pageEl.innerText = pageName;

      const statusEl = document.createElement("div");
      statusEl.className = "missing-images-status";
      statusEl.innerText = "No image selected";

      const actionBtn = document.createElement("button");
      actionBtn.className = "action-btn missing-images-btn";
      actionBtn.type = "button";
      actionBtn.innerText = "Add image";
      actionBtn.addEventListener("click", async () => {
        try {
          const path = await pywebview.api.pick_page_image(pageName, atlasDir || "");
          if (path) applySelection(pageName, path);
        } catch (e) {
          console.error(e);
        }
      });

      row.appendChild(pageEl);
      row.appendChild(statusEl);
      row.appendChild(actionBtn);
      list.appendChild(row);
      rowByPage.set(pageName, row);
      statusByPage.set(pageName, statusEl);
      btnByPage.set(pageName, actionBtn);
    }

    unbindDrag = bindMissingImagesDragLayer(overlay, rowByPage);

    const buttons = document.createElement("div");
    buttons.className = "missing-images-buttons";

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "btn-secondary";
    cancelBtn.type = "button";
    cancelBtn.innerText = "Cancel";

    confirmBtn = document.createElement("button");
    confirmBtn.className = "btn-primary";
    confirmBtn.type = "button";
    confirmBtn.innerText = "Load";
    confirmBtn.disabled = true;

    const close = (result) => {
      window.removeEventListener("keydown", onKeyDown);
      if (unbindDrag) unbindDrag();
      clearMissingDragUi();
      delete document.body.dataset.missingDialogOpen;
      _missingDialogState = null;
      overlay.remove();
      resolve(result);
    };

    function onKeyDown(e) {
      if (e.key === "Escape") close(null);
    }

    cancelBtn.addEventListener("click", () => close(null));
    confirmBtn.addEventListener("click", () => {
      if (confirmBtn.disabled) return;
      close({ ...selectedByPage });
    });

    buttons.appendChild(cancelBtn);
    buttons.appendChild(confirmBtn);
    dialog.appendChild(title);
    dialog.appendChild(subtitle);
    dialog.appendChild(list);
    dialog.appendChild(buttons);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);
    document.body.dataset.missingDialogOpen = "true";
    const dropOverlay = document.getElementById("drop-overlay");
    if (dropOverlay) {
      dropOverlay.classList.add("hidden");
      dropOverlay.style.pointerEvents = "none";
    }

    window.addEventListener("keydown", onKeyDown);
    updateConfirmState();
    const firstBtn = rowByPage.get(pages[0])?.querySelector("button");
    if (firstBtn) firstBtn.focus();
  });
}

/** Called from Python when a PNG is dropped while the missing-images dialog is open. */
window.applyMissingImageDrop = function (path, clientX, clientY) {
  if (!_missingDialogState || !path || !/\.png$/i.test(path)) return false;

  const row = missingImageRowAt(clientX, clientY);
  if (!row) return false;

  const targetPage = row.dataset.pageName;
  if (!targetPage) return false;

  clearMissingDragUi();
  _missingDialogState.applySelection(targetPage, path);
  return true;
};

window.showMissingAtlasImages = async function (missingPages, atlasDir) {
  return showMissingAtlasImagesDialog(missingPages, atlasDir || "");
};
