/**
 * Busy overlay + re-entrant lock. Paint (withBusy) is separate from the
 * mutex (withLock): confirm stays at z-index 4500 during a silent lock.
 * Painted busy sits above rename/add/gap so leftover dialogs cannot cover it.
 */

export const BUSY_WAIT_MESSAGE = 'Please wait for the current operation to finish.';
export const BUSY_LABEL = 'Processing...';
export const BUSY_LOADING_LABEL = 'Loading...';

let _lockCount = 0;
let _paintCount = 0;
let _lastMessage = '';
let _stickyErrors = [];
let _statusHideTimer = null;

function el(id) {
  return typeof document !== 'undefined' ? document.getElementById(id) : null;
}

function setHidden(node, hidden) {
  if (!node || !node.classList) return;
  if (hidden) node.classList.add('hidden');
  else node.classList.remove('hidden');
}

export function hideBusyStatus() {
  if (_statusHideTimer) {
    clearTimeout(_statusHideTimer);
    _statusHideTimer = null;
  }
  const status = el('busy-status-line');
  if (status) {
    status.textContent = '';
    setHidden(status, true);
  }
}

/** Hide gap/rename/add/help/confirm so they are gone before busy paints.
 *  Confirm is already resolved by the time withBusy runs. */
export function dismissWorkOverlays() {
  for (const id of [
    'rename-modal',
    'add-region-modal',
    'nest-gap-overlay',
    'opt-help-popover',
    'modal-overlay',
  ]) {
    const node = el(id);
    if (node && node.classList) node.classList.add('hidden');
  }
  const gap = el('nest-gap-overlay');
  if (gap && gap.classList) gap.classList.remove('is-touch-modal');
  const gear = el('btn-nest-settings');
  if (gear && gear.setAttribute) gear.setAttribute('aria-expanded', 'false');
  if (typeof document !== 'undefined' && document.body && document.body.dataset) {
    document.body.dataset.addRegionDialogOpen = 'false';
  }
}

function scheduleStatusHide() {
  if (_statusHideTimer) clearTimeout(_statusHideTimer);
  _statusHideTimer = setTimeout(() => {
    _statusHideTimer = null;
    hideBusyStatus();
  }, 4000);
}

function paintOverlay(show, label) {
  const overlay = el('busy-overlay');
  const labelEl = el('busy-label');
  if (labelEl && label != null) labelEl.textContent = label;
  if (show) {
    dismissWorkOverlays();
    const reject = el('busy-reject');
    if (reject) reject.textContent = '';
    setHidden(overlay, false);
  } else if (_paintCount <= 0) {
    setHidden(overlay, true);
    const reject = el('busy-reject');
    if (reject) reject.textContent = '';
  }
}

function flushStickyErrors() {
  const queued = _stickyErrors;
  _stickyErrors = [];
  if (queued.length === 0) return;
  const last = queued[queued.length - 1];
  queueMicrotask(() => {
    if (typeof window !== 'undefined' && typeof window.showAlert === 'function') {
      window.showAlert(last.message, last.title);
    }
  });
}

async function runLocked(fn) {
  _lockCount++;
  try {
    return await fn();
  } finally {
    _lockCount--;
    if (_lockCount <= 0) {
      _lockCount = 0;
      _paintCount = 0;
      paintOverlay(false);
      hideBusyStatus();
      flushStickyErrors();
    }
  }
}

/** Refcount only — does not paint #busy-overlay. */
export async function withLock(fn) {
  return runLocked(fn);
}

function forceBusyReflow() {
  const overlay = el('busy-overlay');
  if (overlay) void overlay.offsetHeight;
}

function yieldForPaint() {
  return new Promise((resolve) => {
    if (typeof requestAnimationFrame === 'function') {
      // One rAF fires *before* the browser paints. The second waits until
      // that paint has landed, so getImageData/_canvasHash cannot steal
      // the frame that should show #busy-overlay.
      requestAnimationFrame(() => requestAnimationFrame(resolve));
    } else {
      setTimeout(resolve, 0);
    }
  });
}

/** withLock plus a painted overlay for the duration of fn. */
export async function withBusy(label, fn) {
  if (typeof fn !== 'function') {
    fn = label;
    label = BUSY_LABEL;
  }
  const isOuterPaint = _paintCount === 0;
  _paintCount++;
  paintOverlay(true, label);
  if (isOuterPaint) forceBusyReflow();
  try {
    return await runLocked(async () => {
      if (isOuterPaint) await yieldForPaint();
      return await fn();
    });
  } finally {
    _paintCount = Math.max(0, _paintCount - 1);
    if (_paintCount <= 0 && _lockCount > 0) {
      // Outer withLock is still held: hide paint, keep the mutex.
      paintOverlay(false);
    }
  }
}

export function isBusy() {
  return _lockCount > 0;
}

export function isOverlayPainted() {
  return _paintCount > 0;
}

/** Reject a conflicting action onto the painted overlay (not sticky). */
export function rejectBusy(message) {
  _lastMessage = String(message ?? '');
  const reject = el('busy-reject');
  if (reject) reject.textContent = _lastMessage;
}

/**
 * Error/reject text while a silent lock is held (e.g. Remove confirm).
 * Never unhides #modal-overlay — that node is shared with showConfirm.
 */
export function reportBusyError(message) {
  _lastMessage = String(message ?? '');
  if (_paintCount > 0) {
    rejectBusy(_lastMessage);
    return;
  }
  const status = el('busy-status-line');
  if (status) {
    status.textContent = _lastMessage;
    setHidden(status, false);
    if (_lockCount <= 0) scheduleStatusHide();
  }
}

/** Operation error during busy/confirm: keep it, flush to showAlert after unlock. */
export function queueBusyError(message, title = 'Error') {
  _lastMessage = String(message ?? '');
  if (_lockCount > 0) {
    _stickyErrors.push({ message: _lastMessage, title });
    if (_paintCount > 0) rejectBusy(_lastMessage);
    else reportBusyError(_lastMessage);
    return;
  }
  reportBusyError(_lastMessage);
}

export function lastBusyMessage() {
  return _lastMessage;
}

/** Drop/toggle conflict: overlay text if painted, otherwise a status line. */
export function noteBusyConflict(message = BUSY_WAIT_MESSAGE) {
  if (_paintCount > 0) rejectBusy(message);
  else reportBusyError(message);
}

export function resetBusyForTests() {
  _lockCount = 0;
  _paintCount = 0;
  _lastMessage = '';
  _stickyErrors = [];
  hideBusyStatus();
  paintOverlay(false);
  const reject = el('busy-reject');
  if (reject) reject.textContent = '';
}

export function isModalOverlayOpen() {
  const modal = el('modal-overlay');
  return !!(modal && modal.classList && !modal.classList.contains('hidden'));
}
