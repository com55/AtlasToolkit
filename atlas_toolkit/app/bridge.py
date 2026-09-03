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
from typing import Any, Optional
from urllib.parse import urlparse

from atlas_toolkit.app.config import AppConfig
from atlas_toolkit.update.controller import UpdateController
from atlas_toolkit.update.updater import get_current_version

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png"}


def _dialog_first_path(result: Any) -> Optional[str]:
    """Normalize create_file_dialog's return (tuple/list/str/None) to one path."""
    if not result:
        return None
    if isinstance(result, (list, tuple)):
        return str(result[0]) if result else None
    return str(result)


def _atlas_page_filenames(atlas_text: str) -> list[str]:
    """Page PNG lines: no colon, ends with `.png` (case-sensitive)."""
    names: list[str] = []
    for raw in atlas_text.splitlines():
        line = raw.strip()
        if ":" in line or not line.endswith(".png"):
            continue
        names.append(line)
    return names


class Api:
    """pywebview bridge — dialogs, JS callbacks, and OS integration."""

    def __init__(self, pending_update_failure: Optional[dict[str, str]] = None) -> None:
        self._window: Optional[webview.Window] = None
        self._config = AppConfig()
        self._updates = UpdateController(pending_failure=pending_update_failure)
        self._closing_confirmed = False
        self._close_check_in_progress = False

    def set_window(self, window: webview.Window) -> None:
        self._window = window

    def _native_hwnd(self) -> int | None:
        window = self._window
        if not window or sys.platform != "win32":
            return None
        try:
            import ctypes

            native = getattr(window, "native", None)
            handle = getattr(native, "Handle", None) if native is not None else None
            if handle is None:
                return None
            hwnd = int(handle)
            root = ctypes.windll.user32.GetAncestor(hwnd, 2)  # GA_ROOT
            return int(root) if root else hwnd
        except Exception:
            return None

    def _raise_window(self, *, pin: bool = False) -> None:
        """Bring the app in front of other windows so an in-app confirm is
        visible after the user clicks Close / Alt+F4 while another app is
        focused (otherwise the vetoed close looks like a hang).

        `pin=True` leaves the window topmost until `_unpin_window()`.
        Explorer keeps the foreground lock, so dropping topmost (or any
        later focus change, e.g. the confirm button) lets it cover us
        again — stay pinned for the life of that modal."""
        window = self._window
        if not window:
            return
        try:
            window.restore()
        except Exception:
            pass
        try:
            window.show()
        except Exception:
            pass
        try:
            window.on_top = True
            if not pin:
                window.on_top = False
        except Exception:
            pass
        if sys.platform != "win32":
            return
        try:
            import ctypes

            hwnd = self._native_hwnd()
            if not hwnd:
                return
            user32 = ctypes.windll.user32
            flags = 0x0001 | 0x0002 | 0x0040  # NOSIZE | NOMOVE | SHOWWINDOW
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)  # HWND_TOPMOST
            if not pin:
                user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)  # HWND_NOTOPMOST
            user32.SetForegroundWindow(hwnd)
        except Exception as e:
            log.debug("bring-to-front failed: %s", e)

    def _unpin_window(self) -> None:
        """Drop the temporary always-on-top from `_raise_window(pin=True)`.

        Call only after the user has clicked the in-app confirm — that
        click gives us the foreground lock, so Explorer stays behind."""
        window = self._window
        if not window:
            return
        try:
            window.on_top = False
        except Exception:
            pass
        hwnd = self._native_hwnd()
        if not hwnd:
            return
        try:
            import ctypes

            flags = 0x0001 | 0x0002 | 0x0040  # NOSIZE | NOMOVE | SHOWWINDOW
            ctypes.windll.user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception as e:
            log.debug("unpin window failed: %s", e)

    def on_closed(self) -> None:
        """`window.events.closed` — drop the handle so later evaluate_js /
        destroy during teardown cannot hit a None native browser (FormClosed
        NoneType in the release app, 2026-08-23)."""
        self._window = None

    def _hide_js_drop_overlay(self) -> None:
        if not self._window:
            return
        try:
            self._window.evaluate_js(
                "var o=document.getElementById('drop-overlay');"
                "if(o){o.classList.add('hidden');o.style.pointerEvents='none';}"
            )
        except Exception:
            pass

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

    def resolve_sibling_page_images(self, atlas_path: str) -> list[dict[str, str]]:
        """Map each atlas page line to an on-disk sibling, if the file exists.

        Returns a list of `{name, path}` (not a dict) so the js_api
        serializer cannot drop keys. Used by the Open button — same rule as
        the historical Python `AtlasSession.resolve_page_images` (page line
        exists on disk next to the .atlas).
        """
        p = Path(atlas_path)
        try:
            content = p.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("resolve_sibling_page_images read failed: %s", e)
            return []
        resolved = _atlas_page_filenames(content)
        out: list[dict[str, str]] = []
        for name in resolved:
            candidate = p.parent / name
            if candidate.is_file():
                out.append({"name": name, "path": str(candidate.resolve())})
        return out

    def resolve_sibling_skel(self, atlas_path: str) -> Optional[str]:
        """Path of `{stem}.skel` next to the atlas, or None.

        Historical Python copied this at save via `Path.with_suffix(".skel")`
        (pinned SHA 9655e3c). Exists-check here so JS does not fetch(file://)
        a missing sibling.
        """
        p = Path(atlas_path).with_suffix(".skel")
        try:
            if p.is_file():
                return str(p.resolve())
        except OSError as e:
            log.warning("resolve_sibling_skel failed: %s", e)
        return None

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
        return _dialog_first_path(result)

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

    def _confirm_discard_modifications(self) -> bool:
        # Atlas engine/session lives in www/js/atlas-api.js — read its state
        # via evaluate_js (synchronous return in pywebview 6.1 docs).
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
            self._unpin_window()
            return True
        self._raise_window(pin=True)
        # In-app modal (www/js/dialogs.js showConfirm), not the OS native
        # MessageBox — user request 2026-08-23: confirmation dialogs should
        # match the rest of the www/ UI. Safe here because every caller
        # already runs off the GUI thread (on_closing's background thread,
        # on_drop / startup_check via js_api worker thread).
        try:
            result = self._evaluate_js_promise(
                "window.showConfirm("
                "'You have unsaved atlas modifications.\\nContinue and discard them?', "
                "'Discard modifications?')",
                timeout=None,
            )
        finally:
            self._unpin_window()
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
        self._raise_window(pin=True)
        if not self._close_check_in_progress:
            self._close_check_in_progress = True
            threading.Thread(target=self._check_and_close, daemon=True).start()
        return False

    def _check_and_close(self) -> None:
        try:
            proceed = self._confirm_discard_modifications()
        finally:
            self._close_check_in_progress = False
        window = self._window
        if proceed and window is not None:
            self._closing_confirmed = True
            try:
                window.destroy()
            except Exception as e:
                log.warning("window.destroy failed: %s", e)

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
        window = self._window
        if not window:
            return None
        holder: dict[str, object] = {}
        done = threading.Event()

        def on_result(value: object) -> None:
            holder["value"] = value
            done.set()

        try:
            window.evaluate_js(script, on_result)
        except Exception as e:
            log.warning("evaluate_js failed: %s", e)
            return None
        if not done.wait(timeout=timeout):
            log.warning("evaluate_js promise timed out: %s", script[:120])
            return None
        return holder.get("value")

    def _open_atlas_path_native(self, path_str: str, via_drop: bool = False) -> bool:
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
            # timeout=None: missing-images dialog can sit open for minutes
            # (30s default was logging a truncated-base64 warning and
            # toasting Failed to load while the dialog was still up).
            result = self._evaluate_js_promise(
                f"window.loadAtlasFromNative({json.dumps(atlas_file['base64'])}, "
                f"{json.dumps(atlas_file['name'])}, {json.dumps(image_paths)}, "
                f"{json.dumps(atlas_dir)})",
                timeout=None,
            )
            if result == "cancelled":
                self._window.evaluate_js("showToast('Cancelled', 'info')")
                return False
            ok = bool(result)
            if not ok:
                self._window.evaluate_js(
                    "showToast('Failed to load atlas file.', 'error')"
                )
            elif via_drop:
                self._window.evaluate_js(
                    "showToast('Atlas loaded via drag & drop.', 'success')"
                )
            # Window title update happens centrally in script.js's
            # _resetUiAfterFreshLoad() (via set_window_title below), so it
            # covers every fresh-load path uniformly (Open button's native
            # branch included), not just this one.
            return ok
        except Exception as e:
            log.error("Native atlas open error: %s", e)
            msg = json.dumps(f"Error: {e}")
            if self._window:
                try:
                    self._window.evaluate_js(f"showToast({msg}, 'error')")
                except Exception:
                    pass
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
        """Unused by the JS Open button (that path is pick_atlas_file +
        AtlasAPI.choose_file). Fallback that still drives the JS engine."""
        if not self._window:
            return False
        file_types = ("Atlas Files (*.atlas)", "All files (*.*)")
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=False, file_types=file_types
        )
        path = _dialog_first_path(result)
        if path:
            return self._open_atlas_path_native(path)
        return False

    def pick_page_image(self, page_name: str, default_dir: str = "") -> Optional[str]:
        if not self._window:
            return None
        file_types = ("PNG images (*.png)",)
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=file_types,
            directory=default_dir or "",
        )
        if not result:
            return None
        path = result[0]
        if not path.lower().endswith(".png"):
            return None
        return path

    def on_drop(self, e: Any) -> None:
        # Native OS drop: JS File has no bytes; Python has pywebviewFullPath.
        # Hand paths to www/script.js — see _open_atlas_path_native /
        # _handle_image_drop.
        try:
            files = e["dataTransfer"]["files"]
            if len(files) == 0:
                return

            path = files[0].get("pywebviewFullPath")
            log.debug("Dropped file path: %s", path)
            if not path or not self._window:
                return

            self._hide_js_drop_overlay()
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
                # 2026-08-23). clientX/clientY may be missing on some
                # WebView2 drops; JS then matches the PNG filename to a row.
                if path_lower.endswith(".png"):
                    client_x = e.get("clientX")
                    client_y = e.get("clientY")
                    if client_x is None:
                        client_x = e.get("x")
                    if client_y is None:
                        client_y = e.get("y")
                    # Fire-and-forget: applyMissingImageDrop is async and
                    # on_drop may run on the GUI thread (DOMEventHandler).
                    # Awaiting the Promise here deadlocks the same way
                    # on_closing did (pywebview#1699). Starting the call is
                    # enough — the dialog updates when the File loads.
                    self._window.evaluate_js(
                        f"void window.applyMissingImageDrop({json.dumps(path)}, "
                        f"{json.dumps(client_x)}, {json.dumps(client_y)})"
                    )
                return

            add_open = self._window.evaluate_js(
                "document.body.dataset.addRegionDialogOpen === 'true'"
            )
            if add_open:
                if path_lower.endswith(".png"):
                    # Fire-and-forget, same reasoning as applyMissingImageDrop
                    # above: awaiting here can deadlock on the GUI thread.
                    self._window.evaluate_js(
                        f"void window.applyAddRegionImageDrop({json.dumps(path)})"
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
                self._open_atlas_path_native(path, via_drop=True)
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
