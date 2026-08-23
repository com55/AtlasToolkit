"""pywebview bridge — dialogs, JS callbacks, and OS integration."""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
import webview
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import urlparse

from atlas_toolkit.app.config import AppConfig
from atlas_toolkit.app.preview_cache import PreviewCache
from atlas_toolkit.app.session import AtlasSession, ModifyResult, ModifyViewData
from atlas_toolkit.update.controller import UpdateController
from atlas_toolkit.update.updater import get_current_version

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png"}


def _modify_view_to_payload(
    view: ModifyViewData, cache: PreviewCache
) -> dict[str, object]:
    payload: dict[str, object] = {
        "image": cache.store_image(view.image, "modify_page_0"),
        "regions": view.regions,
        "overlayRects": view.overlay_rects,
        "pages": view.pages,
        "regionPages": view.region_pages,
        "activePage": view.active_page,
        "modifiedRegions": view.modified_regions,
    }
    if view.extra:
        payload.update(view.extra)
    return payload


def _modify_result_to_payload(
    result: ModifyResult, cache: PreviewCache
) -> dict[str, object]:
    page_key = "modify_page_0"
    extra = result.extra or {}
    preview_page = extra.get("previewPage")
    pages = extra.get("pages")
    if isinstance(pages, list) and pages and isinstance(preview_page, str):
        try:
            page_key = f"modify_page_{pages.index(preview_page)}"
        except ValueError:
            pass

    payload: dict[str, object] = {
        "image": cache.store_image(result.image, page_key),
        "regions": result.regions,
        "overlayRects": result.overlay_rects,
        "modifiedRegions": result.modified_regions,
    }
    if result.extra:
        payload.update(result.extra)
    return payload


class Api:
    """pywebview bridge — dialogs, JS callbacks, and OS integration."""

    def __init__(self, pending_update_failure: Optional[dict[str, str]] = None) -> None:
        self._window: Optional[webview.Window] = None
        self._session = AtlasSession()
        self._config = AppConfig()
        self._updates = UpdateController(pending_failure=pending_update_failure)
        self._preview_cache = PreviewCache()
        self._closing_confirmed = False
        self._close_check_in_progress = False

    def set_window(self, window: webview.Window) -> None:
        self._window = window

    def get_pref(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def set_pref(self, key: str, value: Any) -> None:
        self._config.set(key, value)

    # ── Native-only OS glue (D1's revised split — see plan Phase 2) ──────────
    # These have no in-webview equivalent: pywebview's native drag-drop/CLI-arg
    # opens hand Python a filesystem path, not a browser File, and native
    # drag-drop File objects carry no readable bytes client-side (only
    # `pywebviewFullPath`). Read the bytes here and hand them to the JS engine
    # (`window.AtlasAPI` / `window.loadAtlasFromNative`, see www/script.js) as
    # base64 via `evaluate_js`.

    def read_file_as_base64(self, path: str) -> dict[str, str]:
        p = Path(path)
        data = p.read_bytes()
        return {"name": p.name, "base64": base64.b64encode(data).decode("ascii")}

    def write_file_bytes(self, path: str, base64_data: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(base64.b64decode(base64_data))

    def list_sibling_page_images(self, atlas_path: str) -> dict[str, str]:
        """Glob the `.atlas` file's parent directory for `*.png` siblings —
        pure I/O, no atlas-text parsing (the JS side already knows which page
        names it needs; this just hands over every candidate by filename).

        Returns `{filename: full_path}`, NOT base64 bytes (changed 2026-08-23,
        perf fix): the JS side reads the bytes itself via
        `fetch(file://...)` (see platform.js's `loadFileAsFile`) instead of
        round-tripping through the js_api bridge, which base64-encodes AND
        regex-escapes the whole payload twice before embedding it as a
        literal in an `eval()`-wrapped script — measured over 1 second for a
        single ~5MB PNG, vs. under 10ms via fetch(file://) for the same file.
        """
        parent = Path(atlas_path).parent
        images: dict[str, str] = {}
        try:
            for png in parent.glob("*.png"):
                images[png.name] = str(png.resolve())
        except OSError as e:
            log.warning("Failed listing sibling images in %s: %s", parent, e)
        return images

    def set_window_title(self, atlas_filename: str) -> None:
        """Update the native OS window title to reflect the loaded atlas —
        the old Python engine's `load_atlas` did this on every open; the new
        JS engine has no equivalent hook, so `_resetUiAfterFreshLoad()`
        (script.js) calls this once, centrally, after every fresh load
        across all three open paths (Open button, drag-drop, CLI/file
        association)."""
        if not self._window:
            return
        title = f"Atlas Toolkit v{get_current_version()}"
        if atlas_filename:
            title = f"{title} - {atlas_filename}"
        self._window.set_title(title)

    def pick_atlas_file(self) -> Optional[str]:
        """Native single-file Open dialog for `.atlas` files, used by the
        "Open" button (`AtlasAPI.choose_file()`, see platform.js/atlas-api.js
        Phase 3 fix) — returns a bare path so the caller can auto-resolve
        sibling page images via list_sibling_page_images(), matching the old
        Python-engine desktop UX instead of requiring the user to multi-select
        the atlas + every PNG together in a browser file picker."""
        if not self._window:
            return None
        file_types = ("Atlas Files (*.atlas)", "All files (*.*)")
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=False, file_types=file_types
        )
        return result[0] if result else None

    def pick_mod_image(self, default_dir: str = "") -> Optional[str]:
        """Native single-file Open dialog for a mod PNG ("Modify Selected"),
        opened at the atlas's own folder by default — old Python engine's
        select_mod_image() did the same (`directory=atlas_path.parent`); the
        JS port initially always used a plain `<input type=file>` even on
        desktop (found via parity audit, 2026-08-23)."""
        if not self._window:
            return None
        file_types = ("PNG Files (*.png)", "All files (*.*)")
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=file_types,
            directory=default_dir or "",
        )
        return result[0] if result else None

    def pick_save_folder(self, default_dir: str = "") -> Optional[str]:
        """Native folder-picker for output (extract-to-folder / save_modified)
        — see platform.js's pickSaveFolder(), Phase 3."""
        if not self._window:
            return None
        result = self._window.create_file_dialog(
            webview.FileDialog.FOLDER, directory=default_dir or ""
        )
        return result[0] if result else None

    def pick_save_file(self, default_filename: str = "", default_dir: str = "") -> Optional[str]:
        """Native Save-As dialog for a single output file (preview export /
        "Save Preview As") — see platform.js's saveFileWithDialog(), Phase 3."""
        if not self._window:
            return None
        result = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=default_dir or "",
            save_filename=default_filename,
        )
        return result[0] if result else None

    def has_pending_modifications(self) -> bool:
        return self._session.has_pending_modifications()

    def _confirm_discard_modifications(self) -> bool:
        # Redesigned (fable review): the atlas engine/session now lives
        # client-side in www/js/atlas-api.js — read its state synchronously
        # via evaluate_js (confirmed synchronous in pywebview 6.1 docs)
        # instead of the now-inert Python AtlasSession.
        #
        # IMPORTANT: this must only ever be called from a background thread
        # (see on_closing below) — evaluate_js needs the main GUI thread's
        # message loop to be free to pump, and calling it directly from a
        # blocking window event (like `closing`) that itself runs ON the
        # main thread deadlocks pywebview outright (known, still-open
        # upstream issue: r0x0r/pywebview#1699).
        if not self._window:
            return True
        try:
            pending = self._window.evaluate_js(
                "window.AtlasAPI && window.AtlasAPI.has_pending_modifications"
                " && window.AtlasAPI.has_pending_modifications()"
            )
        except Exception as e:
            log.warning("has_pending_modifications check failed: %s", e)
            pending = False
        if not pending:
            return True
        # In-app modal (www/js/dialogs.js showConfirm), not the OS native
        # MessageBox — user request 2026-08-23: confirmation dialogs should
        # match the rest of the www/ UI. Safe here because every caller
        # already runs off the GUI thread (on_closing's background thread,
        # on_drop / startup_check via js_api worker thread).
        result = self._evaluate_js_promise(
            "window.showConfirm("
            "'You have unsaved atlas modifications.\\nContinue and discard them?', "
            "'Discard modifications?')",
            timeout=None,
        )
        return bool(result)

    def on_closing(self) -> bool:
        """`window.events.closing` handler. Always vetoes the *first* close
        attempt (native X button / Alt+F4) and kicks the actual
        has-pending-modifications check off onto a background thread —
        calling `evaluate_js` synchronously right here, on the closing
        event's own thread, would deadlock the whole app (main GUI thread
        blocks on evaluate_js's result; evaluate_js needs that same thread's
        message loop free to produce one). Once the background check
        confirms it's OK to close, `window.destroy()` closes it for real."""
        if self._closing_confirmed:
            return True
        if not self._close_check_in_progress:
            self._close_check_in_progress = True
            threading.Thread(target=self._check_and_close, daemon=True).start()
        return False

    def _check_and_close(self) -> None:
        try:
            proceed = self._confirm_discard_modifications()
        finally:
            self._close_check_in_progress = False
        if proceed and self._window:
            self._closing_confirmed = True
            self._window.destroy()

    def _evaluate_js_promise(
        self, script: str, timeout: float | None = 30.0
    ) -> Any:
        """`evaluate_js(script)` **without** a callback does NOT await a
        returned JS Promise (pywebview docs: "If the JavaScript code returns
        a promise, you can resolve it by providing a callback function") —
        calling it bare against an `async function` silently returns the
        (empty, falsy) Promise wrapper instead of the real resolved value.
        This bit `_open_atlas_path_native` for real: `window.loadAtlasFromNative`
        is async, so the old bare call always looked like a failure even
        though the atlas had, in fact, loaded — a false "Failed to load
        atlas file" toast on every native open/drag-drop (found via user
        testing, 2026-08-23). Use the documented callback form + a
        threading.Event instead — same pattern already proven by
        `_prompt_missing_page_images`.

        `timeout=None` waits indefinitely — required for in-app confirm
        modals where the user may sit on the dialog longer than 30s.
        """
        if not self._window:
            return None
        holder: dict[str, object] = {}
        done = threading.Event()

        def on_result(value: object) -> None:
            holder["value"] = value
            done.set()

        self._window.evaluate_js(script, on_result)
        if not done.wait(timeout=timeout):
            log.warning("evaluate_js promise timed out: %s", script[:120])
            return None
        return holder.get("value")

    def _open_atlas_path_native(self, path_str: str) -> bool:
        """Load an `.atlas` file opened via a native path (CLI arg, file
        association, or native drag-drop) into the JS engine."""
        if not self._window:
            return False
        try:
            # Atlas text is small and still crosses as base64; sibling image
            # PATHS (not bytes, see list_sibling_page_images's doc comment --
            # perf fix, 2026-08-23) are read client-side via fetch(file://...).
            atlas_file = self.read_file_as_base64(path_str)
            image_paths = self.list_sibling_page_images(path_str)
            atlas_dir = str(Path(path_str).resolve().parent)
            result = self._evaluate_js_promise(
                f"window.loadAtlasFromNative({json.dumps(atlas_file['base64'])}, "
                f"{json.dumps(atlas_file['name'])}, {json.dumps(image_paths)}, "
                f"{json.dumps(atlas_dir)})"
            )
            ok = bool(result)
            if not ok:
                self._window.evaluate_js(
                    "showToast('Failed to load atlas file.', 'error')"
                )
            # Window title update happens centrally in script.js's
            # _resetUiAfterFreshLoad() (via set_window_title below), so it
            # covers every fresh-load path uniformly (Open button's native
            # branch included), not just this one.
            return ok
        except Exception as e:
            log.error("Native atlas open error: %s", e)
            msg = json.dumps(f"Error: {e}")
            self._window.evaluate_js(f"showToast({msg}, 'error')")
            return False

    def startup_check(self) -> bool:
        time.sleep(0.5)
        threading.Thread(target=self._run_update_check, daemon=True).start()

        if self._updates.pending_failure and self._window:
            payload_json = json.dumps(self._updates.pending_failure)
            self._window.evaluate_js(
                f"window.showUpdateInstallFailed({payload_json});"
            )

        if len(sys.argv) > 1 and sys.argv[1].endswith(".atlas"):
            if not self._confirm_discard_modifications():
                return False
            return self._open_atlas_path_native(sys.argv[1])
        return False

    def open_update_log(self, log_path: str) -> dict[str, Any]:
        p = Path(log_path)
        if not p.exists():
            return {"ok": False, "error": "Log file not found."}
        try:
            if sys.platform == "win32":
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)], close_fds=True)
            else:
                subprocess.Popen(["xdg-open", str(p)], close_fds=True)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"Failed to open log file: {e}"}

    def open_url(self, url: str) -> dict[str, Any]:
        try:
            parsed = urlparse(str(url))
            if parsed.scheme not in ("http", "https"):
                return {"ok": False, "error": "Invalid URL scheme."}
            webbrowser.open(parsed.geturl())
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"Failed to open URL: {e}"}

    def choose_file(self) -> bool:
        if not self._window:
            return False
        file_types = ("Atlas Files (*.atlas)", "All files (*.*)")
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=False, file_types=file_types
        )
        if result:
            return self.load_atlas(result[0])
        return False

    def pick_page_image(self, page_name: str, default_dir: str = "") -> Optional[str]:
        if not self._window:
            return None
        file_types = ("PNG images (*.png)",)
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=file_types,
            directory=default_dir or None,
        )
        if not result:
            return None
        path = result[0]
        if not path.lower().endswith(".png"):
            return None
        return path

    def _prompt_missing_page_images(
        self, missing_pages: list[str], atlas_dir: str
    ) -> Optional[dict[str, str]]:
        if not self._window or not missing_pages:
            return {}
        pages_json = json.dumps(missing_pages)
        atlas_dir_json = json.dumps(atlas_dir)
        holder: dict[str, object] = {}
        done = threading.Event()

        def on_result(value: object) -> None:
            holder["value"] = value
            done.set()

        self._window.evaluate_js(
            f"showMissingAtlasImages({pages_json}, {atlas_dir_json})",
            on_result,
        )
        if not done.wait(timeout=600.0):
            return None
        value = holder.get("value")
        if value is None:
            return None
        if not isinstance(value, dict):
            return None
        return {str(k): str(v) for k, v in value.items()}

    def load_atlas(self, path_str: str) -> bool:
        log.debug("load_atlas received path: %r", path_str)
        if not self._window:
            return False

        try:
            atlas_path = Path(path_str)
            content = atlas_path.read_text(encoding="utf-8")
            page_images: dict[str, Path] = {}
            resolved_pages = self._session.resolve_page_images(atlas_path, content)

            for page_name, resolved in resolved_pages.items():
                if resolved is not None:
                    page_images[page_name] = resolved

            missing_pages = [
                name for name, resolved in resolved_pages.items() if resolved is None
            ]
            if missing_pages:
                selected = self._prompt_missing_page_images(
                    missing_pages, str(atlas_path.parent)
                )
                if selected is None:
                    return False
                for page_name, image_path in selected.items():
                    page_images[page_name] = Path(image_path)

                still_missing = [
                    name
                    for name in self._session.required_page_names(content)
                    if name not in page_images
                ]
                if still_missing:
                    return False

            self._session.load(atlas_path, page_images)
            self._window.set_title(
                f"Atlas Toolkit v{get_current_version()} - {atlas_path.name}"
            )
            return True
        except Exception as e:
            msg = json.dumps(f"Error: {e}")
            self._window.evaluate_js(f"showToast({msg}, 'error')")
            return False

    def get_region_names(self) -> List[str]:
        return self._session.get_region_names()

    def get_preview(self, names: List[str]) -> Optional[str]:
        img = self._session.get_preview_image(names)
        if img is None:
            return None
        return self._preview_cache.store_image(img, "view_preview")

    def save_preview(
        self, region_names: List[str], default_filename: str = "merged.png"
    ) -> str:
        """Save the composited extract preview for *region_names* via a save dialog."""
        if not self._window:
            return "Error: Window not ready."

        img = self._session.get_preview_image(region_names)
        if img is None:
            return "Error: No image to save."

        default_dir = (
            str(self._session.atlas_path.parent) if self._session.atlas_path else ""
        )
        result = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            directory=default_dir,
            save_filename=default_filename,
        )
        if not result:
            return "Cancelled"
        try:
            Path(result[0]).parent.mkdir(parents=True, exist_ok=True)
            img.save(result[0], format="PNG")
            return f"Saved to {result[0]}"
        except Exception as e:
            log.error("Save preview error: %s", e)
            return f"Error: {e}"

    def extract_files(self, region_names: Optional[List[str]]) -> str:
        if not self._session.is_loaded or not self._window:
            return "No atlas loaded or window not ready."

        extracted = self._session.extract_regions(region_names)
        if not extracted:
            return "No regions to extract."

        is_single = len(extracted) == 1
        default_dir = str(self._session.atlas_path.parent) if self._session.atlas_path else ""
        save_path: Any = None

        if is_single:
            result = self._window.create_file_dialog(
                webview.FileDialog.SAVE,
                directory=default_dir,
                save_filename=f"{extracted[0][0]}.png",
            )
            if result:
                save_path = result[0]
        else:
            result = self._window.create_file_dialog(
                webview.FileDialog.FOLDER, directory=default_dir
            )
            if result:
                save_path = result[0]

        if not save_path:
            return "Cancelled"

        try:
            for name, img in extracted:
                if is_single:
                    dest = Path(save_path)
                else:
                    safe_name = "".join(
                        x for x in name if x.isalnum() or x in "._- "
                    )
                    dest = Path(save_path) / f"{safe_name}.png"
                img.save(dest)
            return f"Successfully extracted {len(extracted)} images."
        except Exception as e:
            return f"Error: {str(e)}"

    def enter_modify_mode(self) -> Optional[dict[str, object]]:
        view = self._session.enter_modify_mode()
        if view is not None:
            log.debug("Entered modify mode")
            return _modify_view_to_payload(view, self._preview_cache)
        return None

    def reset_modify_mode(self) -> Optional[dict[str, object]]:
        view = self._session.reset_modify_mode()
        if view is not None:
            log.debug("Reset modify mode to original atlas")
            return _modify_view_to_payload(view, self._preview_cache)
        return None

    def exit_modify_mode(self) -> None:
        self._session.exit_modify_mode()
        self._preview_cache.clear()
        log.debug("Exited modify mode")

    def select_mod_image(
        self, selected_names: List[str], repack: bool = False
    ) -> Optional[dict[str, object]]:
        if not self._window or not self._session.modifier:
            return None

        file_types = ("PNG Files (*.png)", "All files (*.*)")
        default_dir = (
            str(self._session.atlas_path.parent) if self._session.atlas_path else ""
        )
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=file_types,
            directory=default_dir,
        )
        if not result:
            return None
        return self.process_mod_image(result[0], selected_names, repack)

    def process_mod_image(
        self, path_str: str, selected_names: List[str], repack: bool = False
    ) -> Optional[dict[str, object]]:
        result = self._session.process_mod_image(path_str, selected_names, repack)
        if result is None:
            if self._window:
                self._window.evaluate_js("showToast('Error processing mod image.', 'error')")
            return None
        return _modify_result_to_payload(result, self._preview_cache)

    def get_modify_page_preview(self, index: int) -> Optional[str]:
        img = self._session.get_modify_page_image(index)
        if img is None:
            return None
        return self._preview_cache.store_image(img, f"modify_page_{index}")

    def save_modified(self) -> str:
        if not self._session.has_merged_output() or not self._window:
            return "Error: No merged data to save."

        default_dir = (
            str(self._session.atlas_path.parent) if self._session.atlas_path else ""
        )
        result = self._window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=default_dir,
        )
        if not result:
            return "Cancelled"

        try:
            self._session.save_merged_to(Path(result[0]))
            return f"Saved to: {result[0]}"
        except Exception as e:
            return f"Error: {e}"

    def toggle_repack(self, repack: bool) -> Optional[dict[str, object]]:
        result = self._session.toggle_repack(repack)
        return _modify_result_to_payload(result, self._preview_cache) if result else None

    def debug_log(self, msg: str) -> None:
        log.debug("JS: %s", msg)

    def on_drop(self, e: Any) -> None:
        # Redesigned (fable review): point the follow-up calls at the JS
        # engine's AtlasAPI/www/script.js glue instead of the now-inert
        # Python AtlasSession — see _open_atlas_path_native/_handle_image_drop.
        try:
            files = e["dataTransfer"]["files"]
            if len(files) == 0:
                return

            path = files[0].get("pywebviewFullPath")
            log.debug("Dropped file path: %s", path)
            if not path or not self._window:
                return

            path_lower = path.lower()

            missing_open = self._window.evaluate_js(
                "document.body.dataset.missingDialogOpen === 'true'"
            )
            if missing_open:
                # Native OS drops never carry readable file bytes client-side
                # (only pywebviewFullPath here on the Python side), so the
                # missing-page dialog's own per-row `drop` handler no-ops
                # under pywebview and defers to this instead — mirrors the
                # old Python engine's applyMissingImageDrop() (parity fix,
                # 2026-08-23). clientX/clientY come along on every DOM event
                # pywebview forwards (standard MouseEvent properties), used
                # here to figure out which row the file was dropped onto.
                if path_lower.endswith(".png"):
                    client_x = e.get("clientX")
                    client_y = e.get("clientY")
                    self._window.evaluate_js(
                        f"window.applyMissingImageDrop({json.dumps(path)}, "
                        f"{json.dumps(client_x)}, {json.dumps(client_y)})"
                    )
                return

            if path_lower.endswith(".atlas"):
                # Native drops bypass script.js's openFile() entirely (that's
                # the JS-only "Open" button's own guard), so loading a new
                # atlas here would silently discard unsaved modifications
                # with no confirmation — regression found via user testing,
                # 2026-08-23. Mirror openFile()'s check: this call is safe to
                # make synchronously here (unlike on_closing) because DOM drop
                # events already run off the main GUI thread, same as any
                # other js_api-bound bridge method.
                if not self._confirm_discard_modifications():
                    return
                self._open_atlas_path_native(path)
            elif any(path_lower.endswith(ext) for ext in IMAGE_EXTENSIONS):
                self._handle_image_drop(path)
            else:
                self._window.evaluate_js("showToast('Unsupported file type.', 'error')")
        except Exception as ex:
            log.error("Drop error: %s", ex)

    def _handle_image_drop(self, path: str) -> None:
        """Apply a natively-dropped PNG as a mod image via the JS engine's
        AtlasAPI (client-side selection/repack state) — `applyNativeModImageDrop`
        (www/script.js) itself no-ops with a toast if Edit mode/a selection
        isn't active, mirroring the old Python-session guard here.

        Hands over the plain PATH, not base64 bytes (perf fix, 2026-08-23) --
        the JS side reads it via fetch(file://...) instead; see
        list_sibling_page_images's doc comment for why."""
        if not self._window:
            return
        try:
            # Await the async JS handler — a bare evaluate_js returns the
            # Promise wrapper immediately and the caller would think the
            # drop finished before merge/preview even started.
            self._evaluate_js_promise(
                f"window.applyNativeModImageDrop({json.dumps(path)})"
            )
        except Exception as e:
            log.error("Native image drop error: %s", e)
            msg = json.dumps(f"Error: {e}")
            self._window.evaluate_js(f"showToast({msg}, 'error')")

    def get_update_download_progress(self) -> dict[str, Any]:
        return self._updates.get_progress()

    def download_update(self) -> dict[str, Any]:
        return self._updates.download()

    def restart_and_install_update(self) -> dict[str, Any]:
        return self._updates.restart_and_install()

    def _run_update_check(self) -> None:
        try:
            payload = self._updates.check_for_notification()
            if payload and self._window:
                args_json = json.dumps(payload)
                self._window.evaluate_js(
                    f"window.showUpdateNotification({args_json});"
                )
        except Exception as e:
            log.warning("Update check failed: %s", e)


def setup_drop(window: webview.Window, api: Api) -> None:
    try:
        from webview.dom import DOMEventHandler

        def _drag_over_no_op(_e: Any) -> None:
            pass

        log.debug("Binding drop events...")
        doc = window.dom.document
        doc.events.dragover += DOMEventHandler(
            _drag_over_no_op, True, True, debounce=500
        )  # type: ignore[operator]
        doc.events.drop += DOMEventHandler(api.on_drop, True, True)  # type: ignore[operator]
        log.debug("Drop events bound.")
    except Exception as e:
        log.error("Failed to setup drop events: %s", e)
