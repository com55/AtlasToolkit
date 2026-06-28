// Application bootstrap (pywebview)
window.addEventListener("pywebviewready", async function () {
  const repackPref = await pywebview.api.get_pref("repack", false);
  document.getElementById("chk-repack").checked = repackPref;

  document.getElementById("mode-extract").addEventListener("click", async () => {
    if (currentMode === "extract") return;
    await exitModifyMode();
  });
  document.getElementById("mode-modify").addEventListener("click", async () => {
    if (currentMode === "modify") return;
    await enterModifyMode();
  });

  const loaded = await pywebview.api.startup_check();
  if (loaded) await loadRegions();
});
