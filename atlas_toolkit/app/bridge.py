"""pywebview bridge — dialogs, JS callbacks, and OS integration."""

from __future__ import annotations

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

    def set_window(self, window: webview.Window) -> None:
        self._window = window

    def get_pref(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def set_pref(self, key: str, value: Any) -> None:
        self._config.set(key, value)

    def has_pending_modifications(self) -> bool:
        return self._session.has_pending_modifications()

    def _confirm_discard_modifications(self) -> bool:
        if not self._session.has_pending_modifications():
            return True
        if not self._window:
            return False
        return self._window.create_confirmation_dialog(
            "Discard modifications?",
            "You have unsaved atlas modifications. Continue and discard them?",
        )

    def startup_check(self) -> bool:
        time.sleep(0.5)
        threading.Thread(target=self._run_update_check, daemon=True).start()

        if self._updates.pending_failure and self._window:
            payload_json = json.dumps(self._updates.pending_failure)
            self._window.evaluate_js(
                f"window.showUpdateInstallFailed({payload_json});"
            )

        if len(sys.argv) > 1 and sys.argv[1].endswith(".atlas"):
            return self.load_atlas(sys.argv[1])
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
        try:
            files = e["dataTransfer"]["files"]
            if len(files) == 0:
                return

            path = files[0].get("pywebviewFullPath")
            log.debug("Dropped file path: %s", path)
            if not path:
                return

            path_lower = path.lower()

            if self._window:
                missing_open = self._window.evaluate_js(
                    "typeof isMissingDialogOpen === 'function' && isMissingDialogOpen()"
                )
                if missing_open:
                    if not path_lower.endswith(".png"):
                        return
                    path_json = json.dumps(path)
                    client_x = e.get("clientX")
                    client_y = e.get("clientY")
                    if client_x is not None and client_y is not None:
                        self._window.evaluate_js(
                            f"applyMissingImageDrop({path_json}, {client_x}, {client_y})"
                        )
                    return

            if path_lower.endswith(".atlas"):
                if self._window:
                    path_json = json.dumps(path)
                    self._window.evaluate_js(f"requestLoadAtlas({path_json})")
            elif any(path_lower.endswith(ext) for ext in IMAGE_EXTENSIONS):
                if self._session.modifier:
                    self._handle_image_drop(path)
                elif self._window:
                    self._window.evaluate_js(
                        "showToast('Enter Modify Mode first to drop images.', 'error')"
                    )
            elif self._window:
                self._window.evaluate_js("showToast('Unsupported file type.', 'error')")
        except Exception as ex:
            log.error("Drop error: %s", ex)

    def _handle_image_drop(self, path: str) -> None:
        if not self._window:
            return

        selected_json = self._window.evaluate_js("JSON.stringify(getSelectedNames())")
        if not selected_json:
            self._window.evaluate_js(
                "showToast('Select at least one region first.', 'error')"
            )
            return

        names: list[str] = json.loads(selected_json)
        if not names:
            self._window.evaluate_js(
                "showToast('Select at least one region first.', 'error')"
            )
            return

        repack_val = self._window.evaluate_js(
            "document.getElementById('chk-repack').checked"
        )
        result = self.process_mod_image(path, names, bool(repack_val))
        if result:
            result_json = json.dumps(result)
            self._window.evaluate_js(f"window.onModImageProcessed({result_json})")
        else:
            self._window.evaluate_js("showToast('Failed to process mod image.', 'error')")

    def get_update_download_progress(self) -> dict[str, Any]:
        return self._updates.get_progress()

    def download_update(self) -> dict[str, Any]:
        return self._updates.download()

    def restart_and_install_update(self) -> dict[str, Any]:
        def _close_window() -> None:
            if self._window:
                self._window.destroy()

        return self._updates.restart_and_install(on_success=_close_window)

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
