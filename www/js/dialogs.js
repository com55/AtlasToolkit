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

    const close = (result) => {
      window.removeEventListener('keydown', onKeyDown);
      delete document.body.dataset.missingDialogOpen;
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

    window.addEventListener('keydown', onKeyDown);
    updateConfirmState();
    const firstBtn = rowByPage.get(pages[0])?.querySelector('button');
    if (firstBtn) firstBtn.focus();
  });
}

/**
 * Show a "new version available" toast for the service-worker update flow.
 * Reuses the existing toast-update visual pattern (see style.css). Clicking
 * the action button calls onUpdate(), which should tell the waiting worker
 * to activate; the caller reloads the page on the resulting controllerchange.
 */
export function showUpdateToast(onUpdate) {
  const existing = document.getElementById('update-toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.id = 'update-toast';
  toast.className = 'toast-update';

  const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  icon.setAttribute('class', 'toast-update-icon');
  icon.setAttribute('viewBox', '0 -960 960 960');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', 'M440-320v-326L336-542l-56-58 200-200 200 200-56 58-104-104v326h-80ZM240-160q-33 0-56.5-23.5T160-240v-120h80v120h480v-120h80v120q0 33-23.5 56.5T720-160H240Z');
  icon.appendChild(path);

  const title = document.createElement('span');
  title.className = 'toast-update-title';
  title.textContent = 'Update available';

  const sub = document.createElement('span');
  sub.className = 'toast-update-sub';
  sub.textContent = 'A new version is ready. Refresh to update.';

  const actionBtn = document.createElement('button');
  actionBtn.className = 'toast-update-btn-go';
  actionBtn.textContent = 'Refresh';
  actionBtn.addEventListener('click', () => {
    actionBtn.disabled = true;
    actionBtn.textContent = 'Refreshing...';
    sub.textContent = 'Applying update...';
    onUpdate();
  });

  const closeBtn = document.createElement('button');
  closeBtn.className = 'toast-update-btn-close';
  closeBtn.textContent = 'x';
  closeBtn.addEventListener('click', () => toast.remove());

  toast.appendChild(icon);
  toast.appendChild(title);
  toast.appendChild(sub);
  toast.appendChild(actionBtn);
  toast.appendChild(closeBtn);

  document.getElementById('right-panel').appendChild(toast);
}
