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
    subtitle.innerText = 'Choose one image for each page below.';

    const list = document.createElement('div');
    list.className = 'missing-images-list';

    const rowByPage = new Map();
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
        if (!file) return;
        selectedByPage[pageName] = file;
        statusEl.innerText = formatSelectedImageLabel(file, pageName);
        statusEl.title = String(file.name || pageName);
        row.classList.add('selected');
        actionBtn.innerText = 'Change';
        actionBtn.classList.add('btn-save');
        updateConfirmState();
      });

      row.appendChild(pageEl);
      row.appendChild(statusEl);
      row.appendChild(actionBtn);
      list.appendChild(row);
      rowByPage.set(pageName, row);
    }

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

    window.addEventListener('keydown', onKeyDown);
    updateConfirmState();
    const firstBtn = rowByPage.get(pages[0])?.querySelector('button');
    if (firstBtn) firstBtn.focus();
  });
}
