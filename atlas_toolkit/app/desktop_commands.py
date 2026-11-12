"""Native desktop commands exposed through the local RPC server."""

from pathlib import Path
from typing import Any
import logging
import os
import subprocess
import sys
import threading
import webbrowser
from urllib.parse import urlparse

from atlas_toolkit.app import build_info
from atlas_toolkit.update.controller import get_update_dir
from atlas_toolkit.update.updater import get_current_version

log = logging.getLogger(__name__)
RPC_METHODS = frozenset("""
startup get_pref set_pref set_window_title pick_atlas pick_mod_image pick_skel
pick_page_image pick_save_folder pick_save_file existing_output_names write_files
open_url open_update_log get_update_download_progress download_update
restart_and_install_update install_update_from_file close_answer
""".split())


class DesktopCommands:
    def __init__(self, window, files, events, config, updates, argv, is_unsaved, file_dialog):
        self._window = window
        self._files = files
        self._events = events
        self._config = config
        self._updates = updates
        self._argv = list(argv)
        self._is_unsaved = is_unsaved
        self._file_dialog = file_dialog
        self._startup_result = None
        self._startup_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closing_confirmed = False
        self._close_check_in_progress = False
        self._close_event = None
        self._close_proceed = False

    def set_window(self, window):
        self._window = window

    def startup(self):
        with self._startup_lock:
            if self._startup_result is not None:
                return {**self._startup_result, "atlas": None, "atlasError": None}
            if self._startup_result is None:
                atlas, error = None, None
                if len(self._argv) > 1 and self._argv[1].lower().endswith(".atlas"):
                    try:
                        atlas = self._files.atlas_bundle(Path(self._argv[1]))
                    except (OSError, UnicodeDecodeError) as exc:
                        error = str(exc)
                self._startup_result = dict(version=get_current_version(), devBuild=build_info.DEV_BUILD,
                                            atlas=atlas, atlasError=error,
                                            updateFailure=self._updates.pending_failure)
                self._update_thread = threading.Thread(target=self._run_update_check, daemon=True)
                self._update_thread.start()
            return dict(self._startup_result)

    def _run_update_check(self):
        try:
            payload = self._updates.check_for_notification()
            if payload:
                self._events.publish("update-available", payload=payload)
        except Exception:
            log.exception("Update check failed")

    def get_pref(self, key, default=None):
        return self._config.get(key, default)

    def set_pref(self, key, value):
        self._config.set(key, value)

    def set_window_title(self, atlas_filename):
        if self._window is not None:
            title = f"Atlas Toolkit v{get_current_version()}"
            self._window.set_title(f"{title} - {atlas_filename}" if atlas_filename else title)

    def _dialog(self, kind, **kwargs):
        if self._window is None:
            return None
        result = self._window.create_file_dialog(kind, **kwargs)
        if not result:
            return None
        return str(result[0] if isinstance(result, (tuple, list)) else result)

    def _pick(self, filters, extension):
        path = self._dialog(self._file_dialog.OPEN, allow_multiple=False,
                            directory=str(self._files.atlas_dir or ""), file_types=filters)
        if path and path.lower().endswith(extension):
            return self._files.register(Path(path))

    def pick_atlas(self):
        path = self._dialog(self._file_dialog.OPEN, allow_multiple=False,
                            directory=str(self._files.atlas_dir or ""),
                            file_types=("Atlas Files (*.atlas)", "All files (*.*)"))
        return self._files.atlas_bundle(Path(path)) if path else None

    def pick_mod_image(self):
        return self._pick(("PNG Files (*.png)", "All files (*.*)"), ".png")

    def pick_skel(self):
        return self._pick(("Skeleton Files (*.skel)", "All files (*.*)"), ".skel")

    def pick_page_image(self, page_name):
        return self._pick(("PNG images (*.png)",), ".png")

    def pick_save_folder(self):
        path = self._dialog(self._file_dialog.FOLDER, directory=str(self._files.atlas_dir or ""))
        return self._files.grant_folder(Path(path)) if path else None

    def pick_save_file(self, default_filename):
        png = str(default_filename or "").lower().endswith(".png")
        kwargs = dict(directory=str(self._files.atlas_dir or ""), save_filename=default_filename)
        if png:
            kwargs["file_types"] = ("PNG image (*.png)", "All files (*.*)")
        path = self._dialog(self._file_dialog.SAVE, **kwargs)
        if not path:
            return None
        target = Path(path)
        adjusted = False
        dot = target.name.rfind(".")
        has_extension = target.name.lower() == ".png" or 0 < dot < len(target.name) - 1
        if png and not has_extension:
            stem = target.name[:-1] if target.name.endswith(".") else target.name
            target = target.with_name(stem + ".png")
            adjusted = True
        return {**self._files.grant_file(target), "adjusted": adjusted}

    def existing_output_names(self, grant, names):
        return self._files.existing_output_names(grant, names)

    def write_files(self, grant, files):
        return self._files.write_files(grant, files)

    def on_drop(self, event):
        try:
            files = event.get("dataTransfer", {}).get("files", [])
            if not files or not files[0].get("pywebviewFullPath"):
                return
            path = Path(files[0]["pywebviewFullPath"])
            x = event.get("clientX")
            x = event.get("x") if x is None else x
            y = event.get("clientY")
            y = event.get("y") if y is None else y
            data = dict(x=x, y=y)
            try:
                if str(path).lower().endswith(".atlas"):
                    data.update(type="atlas", bundle=self._files.atlas_bundle(path))
                else:
                    data.update(type="file", file=self._files.register(path))
            except (OSError, UnicodeDecodeError) as exc:
                data.update(type="error", message=str(exc))
            self._events.publish("drop", **data)
        except Exception:
            log.exception("Drop error")

    def on_closing(self):
        with self._close_lock:
            if self._closing_confirmed or not self._is_unsaved():
                return True
            if not self._close_check_in_progress:
                self._close_check_in_progress = True
                self._close_event = threading.Event()
                self._close_proceed = False
                self._close_thread = threading.Thread(target=self._check_and_close, daemon=True)
                self._close_thread.start()
            elif self._events.has_subscriber:
                self._events.publish("confirm-close")
        return False

    def close_answer(self, proceed):
        with self._close_lock:
            if self._close_check_in_progress and self._close_event is not None:
                self._close_proceed = bool(proceed)
                self._close_event.set()

    def _check_and_close(self):
        try:
            try:
                self._raise_window(pin=True)
                proceed = False
                use_native = not self._events.has_subscriber
                if not use_native:
                    self._events.publish("confirm-close")
                    while not self._close_event.wait(1.0):
                        if not self._events.has_subscriber:
                            use_native = not self._close_event.is_set()
                            break
                    proceed = self._close_proceed
                if use_native:
                    proceed = self._window is not None and self._window.create_confirmation_dialog(
                        "Discard modifications?", "You have unsaved atlas modifications.\nContinue and discard them?")
            finally:
                self._unpin_window()
            if proceed and self._window is not None:
                self._closing_confirmed = True
                self._window.destroy()
        finally:
            with self._close_lock:
                self._close_check_in_progress = False
                self._close_event = None

    def get_update_download_progress(self):
        return self._updates.get_progress()

    def download_update(self):
        return self._updates.download()

    def restart_and_install_update(self):
        return self._updates.restart_and_install()

    def install_update_from_file(self):
        if not build_info.DEV_BUILD:
            return {"ok": False, "error": "Not a development build."}
        path = self._dialog(self._file_dialog.OPEN, allow_multiple=False, file_types=("Installer (*.exe)",))
        if not path:
            return {"ok": False, "cancelled": True}
        return self._updates.install_from_file(Path(path))

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


    def open_update_log(self) -> dict[str, Any]:
        try:
            log_path = (self._updates.pending_failure or {}).get("logPath")
            p = Path(log_path).resolve() if log_path else None
            valid = (p is not None and p.is_file()
                     and p.parent == get_update_dir().resolve() and p.suffix.lower() == ".log")
        except (OSError, RuntimeError, ValueError, TypeError):
            valid = False
        if not valid:
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

