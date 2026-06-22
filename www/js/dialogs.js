import { platform } from './platform.js';

export function showConfirm(message, title = 'Confirm') {
  return new Promise((resolve) => {
    const overlay = document.getElementById('modal-overlay');
    document.getElementById('modal-title').innerText = title;
    document.getElementById('modal-message').innerText = message;
    overlay.classList.remove('hidden');
    const btnConfirm = document.getElementById('btn-modal-confirm');
    const btnCancel  = document.getElementById('btn-modal-cancel');
    if (document.activeElement) document.activeElement.blur();
    btnConfirm.focus();
    function cleanup() {
      overlay.classList.add('hidden');
      btnConfirm.removeEventListener('click', onConfirm);
      btnCancel.removeEventListener('click', onCancel);
      window.removeEventListener('keydown', onKey);
    }
    function onConfirm() { cleanup(); resolve(true); }
    function onCancel()  { cleanup(); resolve(false); }
    function onKey(e)    { if (e.key === 'Escape') onCancel(); }
    btnConfirm.addEventListener('click', onConfirm);
    btnCancel.addEventListener('click', onCancel);
    window.addEventListener('keydown', onKey);
  });
}

export function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerText = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = 'none';
    toast.offsetHeight;
    toast.style.animation = 'fadeOut 0.5s ease-out forwards';
    toast.addEventListener('animationend', () => toast.remove());
  }, 3000);
}

export function pickSingleImageFile() {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/png,.png';
    input.multiple = false;

    let done = false;
    const finish = (file) => {
      if (done) return;
      done = true;
      input.removeEventListener('change', onChange);
      resolve(file || null);
    };
    const onChange = (e) => {
      finish(e.target.files && e.target.files[0] ? e.target.files[0] : null);
    };
    input.addEventListener('change', onChange);
    input.click();
    setTimeout(() => finish(null), 60000);
  });
}

export function formatSelectedImageLabel(file, pageName) {
  const fallback = `Selected for ${pageName}`;
  if (!file) return fallback;
  const rawName = String(file.name || '').trim();
  if (!rawName) return fallback;
  const isLikelyTempName = /^\d+(\.[a-z0-9]+)?$/i.test(rawName);
  if (isLikelyTempName) return 'Selected';
  return rawName;
}

export function showMissingAtlasImagesDialog(missingPages) {
  return new Promise((resolve) => {
    const pages = Array.from(new Set((missingPages || []).filter(Boolean)));
    if (pages.length === 0) { resolve({}); return; }

    const selectedByPage = {};

    const overlay = document.createElement('div');
    overlay.className = 'missing-images-overlay';

    const dialog = document.createElement('div');
    dialog.className = 'missing-images-dialog';

    const title = document.createElement('h3');
    title.className = 'missing-images-title';
    title.innerText = 'Missing image files for atlas pages';

    const subtitle = document.createElement('p');
    subtitle.className = 'missing-images-subtitle';
    subtitle.innerText = 'Choose one image for each page below, or drag & drop a PNG onto a row.';

    const list = document.createElement('div');
    list.className = 'missing-images-list';

    const rowByPage = new Map();
    const statusByPage = new Map();
    const btnByPage = new Map();

    function applySelection(pageName, file) {
      if (!file) return;
      const row = rowByPage.get(pageName);
      const statusEl = statusByPage.get(pageName);
      const actionBtn = btnByPage.get(pageName);
      if (!row || !statusEl || !actionBtn) return;
      selectedByPage[pageName] = file;
      statusEl.innerText = formatSelectedImageLabel(file, pageName);
      statusEl.title = String(file.name || pageName);
      row.classList.add('selected');
      actionBtn.innerText = 'Change';
      actionBtn.classList.add('btn-save');
      updateConfirmState();
    }

    function isPngFile(file) {
      return !!file && (file.type === 'image/png' || /\.png$/i.test(file.name || ''));
    }

    for (const pageName of pages) {
      const row = document.createElement('div');
      row.className = 'missing-images-row';

      const pageEl = document.createElement('div');
      pageEl.className = 'missing-images-page';
      pageEl.innerText = pageName;

      const statusEl = document.createElement('div');
      statusEl.className = 'missing-images-status';
      statusEl.innerText = 'No image selected';

      const actionBtn = document.createElement('button');
      actionBtn.className = 'action-btn missing-images-btn';
      actionBtn.type = 'button';
      actionBtn.innerText = 'Add image';
      actionBtn.addEventListener('click', async () => {
        const file = await pickSingleImageFile();
        if (file) applySelection(pageName, file);
      });

      // Browser/PWA drag & drop directly onto the row. Stop propagation so the
      // global window-level drop overlay (drop.js) never sees this drag.
      row.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.stopPropagation();
        row.classList.add('drag-over');
      });
      row.addEventListener('dragleave', (e) => {
        e.preventDefault();
        e.stopPropagation();
        row.classList.remove('drag-over');
      });
      row.addEventListener('drop', (e) => {
        e.preventDefault();
        e.stopPropagation();
        row.classList.remove('drag-over');
        const file = Array.from(e.dataTransfer?.files || []).find(isPngFile);
        if (file) applySelection(pageName, file);
      });

      row.appendChild(pageEl);
      row.appendChild(statusEl);
      row.appendChild(actionBtn);
      list.appendChild(row);
      rowByPage.set(pageName, row);
      statusByPage.set(pageName, statusEl);
      btnByPage.set(pageName, actionBtn);
    }

    // Backdrop: swallow stray drag events so they never reach drop.js's
    // global window-level listeners while this dialog is open.
    overlay.addEventListener('dragover', (e) => { e.preventDefault(); e.stopPropagation(); });
    overlay.addEventListener('drop', (e) => { e.preventDefault(); e.stopPropagation(); });

    const buttons = document.createElement('div');
    buttons.className = 'missing-images-buttons';

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn-secondary';
    cancelBtn.type = 'button';
    cancelBtn.innerText = 'Cancel';

    const confirmBtn = document.createElement('button');
    confirmBtn.className = 'btn-primary';
    confirmBtn.type = 'button';
    confirmBtn.innerText = 'Load atlas';
    confirmBtn.disabled = true;

    let unsubscribeTauriDrag = null;
    const close = (result) => {
      window.removeEventListener('keydown', onKeyDown);
      delete document.body.dataset.missingDialogOpen;
      if (unsubscribeTauriDrag) unsubscribeTauriDrag();
      overlay.remove();
      resolve(result);
    };

    function updateConfirmState() {
      confirmBtn.disabled = !pages.every(p => !!selectedByPage[p]);
    }

    function onKeyDown(e) { if (e.key === 'Escape') close(null); }

    cancelBtn.addEventListener('click', () => close(null));
    confirmBtn.addEventListener('click', () => {
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
    document.body.dataset.missingDialogOpen = 'true';

    // Tauri's webview intercepts drag-drop at the native layer, so the DOM
    // dragover/drop listeners on the rows above never fire there. Subscribe
    // to Tauri's own drag-drop event instead and resolve the target row from
    // the (physical-pixel) cursor position.
    if (platform.isTauri) {
      const rowAtPosition = (position) => {
        if (!position) return null;
        const dpr = window.devicePixelRatio || 1;
        const el = document.elementFromPoint(position.x / dpr, position.y / dpr);
        return el ? el.closest('.missing-images-row') : null;
      };
      const clearDragOver = () => { for (const r of rowByPage.values()) r.classList.remove('drag-over'); };

      platform.subscribeDragDrop({
        onOver: (p) => {
          const row = rowAtPosition(p?.position);
          for (const [, r] of rowByPage) r.classList.toggle('drag-over', r === row);
        },
        onLeave: clearDragOver,
        onDrop: async (paths, p) => {
          clearDragOver();
          const row = rowAtPosition(p?.position);
          const pageName = row && [...rowByPage.entries()].find(([, r]) => r === row)?.[0];
          if (!pageName) return;
          const pngPath = (paths || []).find(path => /\.png$/i.test(path));
          if (!pngPath) return;
          const { droppedImageFiles } = await platform.readDroppedPaths([pngPath]);
          const file = droppedImageFiles.values().next().value;
          if (file) applySelection(pageName, file);
        },
      }).then((unlisten) => { unsubscribeTauriDrag = unlisten; });
    }

    window.addEventListener('keydown', onKeyDown);
    updateConfirmState();
    const firstBtn = rowByPage.get(pages[0])?.querySelector('button');
    if (firstBtn) firstBtn.focus();
  });
}
