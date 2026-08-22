/**
 * updates.js
 * Native self-updater UI for the pywebview desktop shell — port of
 * `ui/js/updates.js` (Phase 5 of the unify-js-engine plan). Entirely inert
 * under a real browser/PWA session: these globals are only ever invoked by
 * `bridge.py`'s `evaluate_js()` calls (`_run_update_check`,
 * `on_update_install_failed`), which only fire when running under
 * pywebview. PWA/browser users keep using `dialogs.js`'s separate
 * `showUpdateToast` (service-worker refresh flow) untouched.
 */
import { showToast } from './dialogs.js';

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const digits = unitIndex === 0 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unitIndex]}`;
}

async function openExternalUrl(url, invalidMessage = 'Invalid URL.') {
  try {
    const parsed = new URL(String(url));
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      showToast(invalidMessage, 'error');
      return;
    }

    if (window.pywebview && window.pywebview.api && window.pywebview.api.open_url) {
      const result = await window.pywebview.api.open_url(parsed.toString());
      if (!result || !result.ok) {
        showToast((result && result.error) || 'Failed to open URL.', 'error');
      }
      return;
    }

    window.open(parsed.toString(), '_blank');
  } catch (err) {
    console.error(err);
    showToast(invalidMessage, 'error');
  }
}

window.showUpdateNotification = function (...args) {
  let payload = null;

  if (args.length === 1 && typeof args[0] === 'object' && args[0] !== null) {
    payload = args[0];
  } else {
    const legacyVersion = String(args[0] || '');
    payload = {
      latestVersion: legacyVersion,
      releaseName: String(args[1] || ''),
      releaseUrl: String(args[2] || ''),
      tagName: legacyVersion
        ? (legacyVersion.startsWith('v') ? legacyVersion : `v${legacyVersion}`)
        : '',
      sourceTreeUrl: String(args[2] || ''),
      action: 'open_source_tag',
    };
  }

  const latestVersion = String(payload.latestVersion || '');
  const releaseName = String(payload.releaseName || latestVersion || 'New release');
  const releaseUrl = String(payload.releaseUrl || '');
  const sourceTreeUrl = String(payload.sourceTreeUrl || releaseUrl);
  const tagName = String(payload.tagName || latestVersion || '');
  const action = payload.action === 'download' ? 'download' : 'open_source_tag';

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
  title.textContent = `Update available - ${latestVersion}`;

  const sub = document.createElement('span');
  sub.className = 'toast-update-sub';
  sub.textContent = releaseName;

  const actionBtn = document.createElement('button');
  actionBtn.className = 'toast-update-btn-go';

  let phase = action === 'download' ? 'download' : 'external';
  let pollTimer = null;

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function pollDownloadProgress() {
    try {
      const progress = await window.pywebview.api.get_update_download_progress();
      if (!progress) return;

      const status = String(progress.status || 'idle');
      if (status === 'downloading') {
        const total = Number(progress.total_bytes || 0);
        const downloaded = Number(progress.downloaded_bytes || 0);
        if (total > 0) {
          const percent = Number(progress.percent || 0);
          sub.textContent = `Downloading... ${percent}% (${formatBytes(downloaded)} / ${formatBytes(total)})`;
          actionBtn.textContent = `Downloading ${percent}%`;
        } else {
          sub.textContent = `Downloading... ${formatBytes(downloaded)}`;
          actionBtn.textContent = 'Downloading...';
        }
      } else if (status === 'error') {
        stopPolling();
      }
    } catch (err) {
      console.error(err);
    }
  }

  async function handleDownload() {
    actionBtn.disabled = true;
    actionBtn.textContent = 'Downloading...';
    sub.textContent = 'Downloading update package...';

    stopPolling();
    pollTimer = setInterval(pollDownloadProgress, 350);

    try {
      const result = await window.pywebview.api.download_update();
      stopPolling();

      if (result && result.ok) {
        phase = 'restart';
        actionBtn.disabled = false;
        actionBtn.textContent = 'Restart to Update';
        sub.textContent = `Downloaded v${result.version}. Restart to install.`;
      } else {
        const message = (result && result.error) || 'Update download failed.';
        showToast(message, 'error');
        phase = 'download';
        actionBtn.disabled = false;
        actionBtn.textContent = 'Download Update';
        sub.textContent = releaseName;
      }
    } catch (err) {
      stopPolling();
      console.error(err);
      showToast('Update download failed.', 'error');
      phase = 'download';
      actionBtn.disabled = false;
      actionBtn.textContent = 'Download Update';
      sub.textContent = releaseName;
    }
  }

  async function handleRestartToUpdate() {
    actionBtn.disabled = true;
    actionBtn.textContent = 'Restarting...';
    sub.textContent = 'Restarting app to install update...';

    try {
      const result = await window.pywebview.api.restart_and_install_update();
      if (!result || !result.ok) {
        const message = (result && result.error) || 'Failed to restart for update.';
        showToast(message, 'error');
        actionBtn.disabled = false;
        actionBtn.textContent = 'Restart to Update';
        sub.textContent = releaseName;
      }
    } catch (err) {
      console.error(err);
      showToast('Failed to restart for update.', 'error');
      actionBtn.disabled = false;
      actionBtn.textContent = 'Restart to Update';
      sub.textContent = releaseName;
    }
  }

  actionBtn.addEventListener('click', () => {
    if (phase === 'external') {
      openExternalUrl(sourceTreeUrl, 'Invalid source URL.');
      return;
    }
    if (phase === 'download') {
      handleDownload();
      return;
    }
    if (phase === 'restart') {
      handleRestartToUpdate();
    }
  });

  if (phase === 'external') {
    actionBtn.textContent = tagName ? `View Source (${tagName})` : 'View Source';
  } else {
    actionBtn.textContent = 'Download Update';
  }

  const closeBtn = document.createElement('button');
  closeBtn.className = 'toast-update-btn-close';
  closeBtn.textContent = 'x';
  closeBtn.addEventListener('click', () => {
    stopPolling();
    toast.remove();
  });

  toast.appendChild(icon);
  toast.appendChild(title);
  toast.appendChild(sub);
  toast.appendChild(actionBtn);
  toast.appendChild(closeBtn);

  document.getElementById('right-panel').appendChild(toast);
};

window.showUpdateInstallFailed = function (payload) {
  const info = payload && typeof payload === 'object' ? payload : {};
  const message = String(info.message || 'Update installation failed. The app was relaunched.');
  const logPath = String(info.logPath || '');
  const releaseUrl = String(info.releaseUrl || '');

  const existing = document.getElementById('update-failed-toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.id = 'update-failed-toast';
  toast.className = 'toast-update toast-update-error';

  const title = document.createElement('span');
  title.className = 'toast-update-title';
  title.textContent = 'Update failed';

  const sub = document.createElement('span');
  sub.className = 'toast-update-sub';
  sub.textContent = message;

  const logBtn = document.createElement('button');
  logBtn.className = 'toast-update-btn-go';
  logBtn.textContent = 'Open Log';
  logBtn.disabled = !logPath;
  logBtn.addEventListener('click', async () => {
    if (!logPath) return;
    try {
      const result = await window.pywebview.api.open_update_log(logPath);
      if (!result || !result.ok) {
        showToast((result && result.error) || 'Failed to open log.', 'error');
      }
    } catch (err) {
      console.error(err);
      showToast('Failed to open log.', 'error');
    }
  });

  const releaseBtn = document.createElement('button');
  releaseBtn.className = 'toast-update-btn-go';
  releaseBtn.textContent = 'Download Manually';
  releaseBtn.disabled = !releaseUrl;
  releaseBtn.addEventListener('click', () => {
    if (!releaseUrl) return;
    openExternalUrl(releaseUrl, 'Invalid release URL.');
  });

  const closeBtn = document.createElement('button');
  closeBtn.className = 'toast-update-btn-close';
  closeBtn.textContent = 'x';
  closeBtn.addEventListener('click', () => toast.remove());

  toast.appendChild(title);
  toast.appendChild(sub);
  toast.appendChild(logBtn);
  toast.appendChild(releaseBtn);
  toast.appendChild(closeBtn);

  document.getElementById('right-panel').appendChild(toast);
};
