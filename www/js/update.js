import { showToast } from './dialogs.js';

export async function checkForTauriUpdate() {
  const invoke = window.__TAURI_INTERNALS__?.invoke;
  if (!invoke) return;
  try {
    const update = await invoke('fetch_update');
    if (update) showUpdateNotification(update.version, update.body, invoke);
  } catch (e) {
    console.warn('Update check failed:', e);
  }
}

function showUpdateNotification(version, releaseNotes, invoke) {
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
  title.textContent = `Update available - ${version}`;

  const sub = document.createElement('span');
  sub.className = 'toast-update-sub';
  sub.textContent = releaseNotes || `Version ${version} is ready to install.`;

  const actionBtn = document.createElement('button');
  actionBtn.className = 'toast-update-btn-go';
  actionBtn.textContent = 'Install & Restart';
  actionBtn.addEventListener('click', async () => {
    actionBtn.disabled = true;
    actionBtn.textContent = 'Installing...';
    sub.textContent = 'Downloading and installing update...';
    try {
      await invoke('apply_update');
    } catch (e) {
      console.error(e);
      showToast('Update failed: ' + e, 'error');
      actionBtn.disabled = false;
      actionBtn.textContent = 'Install & Restart';
      sub.textContent = releaseNotes || `Version ${version} is ready to install.`;
    }
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
