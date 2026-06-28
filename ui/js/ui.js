// Atlas Toolkit UI module
function setStatus(text) {
  document.getElementById("status-text").innerText = text;
}
function showConfirm(message, title = "Confirm") {
  return new Promise((resolve) => {
    const overlay = document.getElementById("modal-overlay");
    const titleEl = document.getElementById("modal-title");
    const msgEl = document.getElementById("modal-message");
    const btnConfirm = document.getElementById("btn-modal-confirm");
    const btnCancel = document.getElementById("btn-modal-cancel");

    titleEl.innerText = title;
    msgEl.innerText = message;
    overlay.classList.remove("hidden");

    if (document.activeElement) document.activeElement.blur();

    btnConfirm.focus();

    function cleanup() {
      overlay.classList.add("hidden");
      btnConfirm.removeEventListener("click", onConfirm);
      btnCancel.removeEventListener("click", onCancel);
      window.removeEventListener("keydown", onKey);
    }

    function onConfirm() {
      cleanup();
      resolve(true);
    }

    function onCancel() {
      cleanup();
      resolve(false);
    }

    function onKey(e) {
      if (e.key === "Escape") onCancel();
    }

    btnConfirm.addEventListener("click", onConfirm);
    btnCancel.addEventListener("click", onCancel);
    window.addEventListener("keydown", onKey);
  });
}

// --- Toast Logic ---
function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.innerText = message;

  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "1";
    toast.style.transform = "translateY(0)";

    toast.style.animation = "none";
    toast.offsetHeight; /* trigger reflow */

    toast.style.animation = "fadeOut 0.5s ease-out forwards";

    toast.addEventListener("animationend", () => {
      toast.remove();
    });
  }, 3000);
}
