from __future__ import annotations
import sys
import os
import base64
import json
import shutil
import subprocess
import webbrowser
import webview
import time
import threading
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional
from urllib.parse import urlparse
from atlas_converter import auto_convert_atlas
from atlas_extracter import AtlasProcessor
from atlas_modifier import AtlasModifier
from updater import (
    check_for_updates,
    download_update_asset,
    find_windows_installer_asset,
    get_current_version,
    get_latest_release_info,
    is_running_as_exe,
)


def get_resource_path(path: str) -> Path:
    """Get path to a resource file embedded in the executable.

    In Nuitka standalone mode, ``__file__`` resolves to the install directory
    where embedded resources sit next to the executable.
    """
    return Path(__file__).parent / path


def _consume_launch_flags(argv: list[str]) -> tuple[list[str], Optional[dict[str, str]]]:
    """Consume internal launch flags and return user args + optional failure payload."""
    clean_args: list[str] = []
    failed = False
    failed_log = ""
    failed_release_url = ""
    failed_message = ""

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--update-install-failed":
            failed = True
            i += 1
            continue
        if arg == "--update-failed-log" and i + 1 < len(argv):
            failed_log = argv[i + 1]
            i += 2
            continue
        if arg == "--update-release-url" and i + 1 < len(argv):
            failed_release_url = argv[i + 1]
            i += 2
            continue
        if arg == "--update-failed-message" and i + 1 < len(argv):
            failed_message = argv[i + 1]
            i += 2
            continue

        clean_args.append(arg)
        i += 1

    payload: Optional[dict[str, str]] = None
    if failed:
        payload = {
            "message": failed_message or "Update installation failed. The app was relaunched.",
            "logPath": failed_log,
            "releaseUrl": failed_release_url,
        }

    return clean_args, payload


_clean_argv, _pending_update_failure = _consume_launch_flags(sys.argv[1:])
sys.argv = [sys.argv[0], *_clean_argv]

if TYPE_CHECKING:
    from PIL.Image import Image



# Reconfigure stdout/stderr for immediate output (Nuitka console mode compatibility)
if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8')  # type: ignore
if sys.stderr:
    sys.stderr.reconfigure(encoding='utf-8')  # type: ignore

# Logging setup
import logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(levelname)s: %(message)s',
    stream=sys.stdout,  # Ensure logs go to stdout where we can see them
)
log = logging.getLogger(__name__)


IMAGE_EXTENSIONS = {'.png'}
PAGE_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'}
PAGE_HEADER_KEYS = {'size', 'format', 'filter', 'repeat', 'pma'}


def _extract_required_pages(atlas_text: str) -> list[str]:
    """Extract atlas page image names from page headers only."""
    lines = atlas_text.splitlines()
    pages: list[str] = []

    for i, raw in enumerate(lines):
        candidate = raw.strip()
        if not candidate or ':' in candidate:
            continue

        suffix = Path(candidate).suffix.lower()
        if suffix not in PAGE_IMAGE_EXTENSIONS:
            continue

        next_key: Optional[str] = None
        for j in range(i + 1, len(lines)):
            nxt = lines[j].strip()
            if not nxt:
                continue
            if ':' in nxt:
                next_key = nxt.split(':', 1)[0].strip().lower()
            break

        if next_key in PAGE_HEADER_KEYS and candidate not in pages:
            pages.append(candidate)

    return pages

def _get_config_dir() -> Path:
    """Return a persistent config directory for the app."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "AtlasToolkit"
    d.mkdir(parents=True, exist_ok=True)
    return d

CONFIG_PATH = _get_config_dir() / "config.json"


def _get_update_dir() -> Path:
    """Return persistent directory for downloaded update artifacts."""
    d = _get_config_dir() / "update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_nuitka_onefile_parent_exe_path() -> Optional[Path]:
    """Return outer onefile launcher path when running in Nuitka child process."""
    if os.name != "nt":
        return None

    raw_pid = os.environ.get("NUITKA_ONEFILE_PARENT", "").strip()
    if not raw_pid:
        return None

    try:
        pid = int(raw_pid)
    except Exception:
        return None

    if pid <= 0:
        return None

    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32

        process_handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not process_handle:
            return None

        try:
            size = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            ok = kernel32.QueryFullProcessImageNameW(
                process_handle,
                0,
                buf,
                ctypes.byref(size),
            )
            if not ok:
                return None

            p = Path(buf.value)
            if p.exists() and p.is_file():
                return p
            return None
        finally:
            kernel32.CloseHandle(process_handle)
    except Exception:
        return None


def _get_running_executable_path() -> Path:
    """Return the best on-disk executable path for relaunch/update flow."""
    # Nuitka onefile should expose the outer launcher as sys.argv[0].
    if sys.argv and sys.argv[0]:
        try:
            argv0_path = Path(os.path.abspath(sys.argv[0]))
            if argv0_path.exists() and argv0_path.is_file():
                return argv0_path.resolve()
        except Exception:
            pass

    candidates: list[Path] = []

    onefile_parent = _get_nuitka_onefile_parent_exe_path()
    if onefile_parent is not None:
        candidates.append(onefile_parent)

    for raw in ([sys.argv[0]] if sys.argv else []) + [sys.executable]:
        if not raw:
            continue
        try:
            p = Path(raw).expanduser()
        except Exception:
            continue
        if not p.is_absolute():
            p = Path.cwd() / p
        candidates.append(p)

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if resolved.exists() and resolved.is_file():
            return resolved

    if candidates:
        try:
            return candidates[0].resolve()
        except Exception:
            return candidates[0]

    return Path(sys.executable).resolve()


def _install_dir() -> Path:
    """The per-user install directory the Inno Setup installer targets
    (%LOCALAPPDATA%\\AtlasToolkit). The installer upgrades cleanly by running the
    previous version's uninstaller first, which removes only installer-tracked
    files and leaves the app's config / update cache (in the same dir) intact."""
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return (base / "AtlasToolkit").resolve()


def _is_installed_build() -> bool:
    """True when running from the installed location (vs a portable copy).

    Only installed builds get the silent installer self-update; portable copies
    are pointed at the releases page instead (a silent install would replace a
    different folder and orphan the portable exe).
    """
    if not is_running_as_exe():
        return False
    try:
        exe = _get_running_executable_path().resolve()
        install_dir = _install_dir()
        return exe.parent == install_dir or install_dir in exe.parents
    except Exception:
        return False


# Silent installer flags: no UI, no msgboxes, don't reboot, close the running
# app via Restart Manager, and don't let Inno restart it (the script relaunches).
_INNO_SILENT_FLAGS = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /NORESTARTAPPLICATIONS"


def _build_update_script(
    installer_path: Path,
    target_exe: Path,
    pid: int,
    relaunch_args: list[str],
    release_url: str,
    inno_log_path: Path,
) -> str:
    """Build the detached .cmd that waits for the app to exit, runs the Inno
    installer silently, then relaunches the freshly-installed app (or relaunches
    with a failure notice). Returned as text so it can be unit-tested off-Windows.
    """
    inst = str(installer_path)
    exe = str(target_exe)
    log = str(inno_log_path)
    relaunch_suffix = " ".join(f'"{a}"' for a in relaunch_args)
    success_launch = f'start "" "{exe}" {relaunch_suffix}'.rstrip()

    fail_message = "Update failed: the installer reported an error."
    fail_args = [
        "--update-install-failed",
        "--update-failed-message", f'"{fail_message}"',
        "--update-failed-log", f'"{log}"',
    ]
    if release_url:
        fail_args += ["--update-release-url", f'"{release_url}"']
    fail_launch = f'start "" "{exe}" ' + " ".join(fail_args)

    lines = [
        "@echo off",
        "setlocal",
        ":waitloop",
        f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul',
        "if not errorlevel 1 (",
        "    ping -n 2 127.0.0.1 >nul",
        "    goto waitloop",
        ")",
        f'"{inst}" {_INNO_SILENT_FLAGS} /LOG="{log}"',
        "if errorlevel 1 goto failed",
        success_launch,
        "goto cleanup",
        ":failed",
        fail_launch,
        ":cleanup",
        f'del /f /q "{inst}" >nul 2>nul',
        'del /f /q "%~f0" >nul 2>nul',
        "endlocal",
    ]
    return "\r\n".join(lines) + "\r\n"


class Api:
    def __init__(self) -> None:
        self._atlas_path: Optional[Path] = None
        self._processor: Optional[AtlasProcessor] = None
        self._window: Optional[webview.Window] = None
        # Modify mode state
        self._modifier: Optional[AtlasModifier] = None
        self._merged_image: Optional[Image] = None
        self._merged_atlas_text: Optional[str] = None
        # Multi-page merge output (set instead of _merged_image for >1 page atlases)
        self._merged_pages: Optional[List[Image]] = None
        # Pre-repack state (merge output before repack was applied)
        self._pre_repack_image: Optional[Image] = None
        self._pre_repack_text: Optional[str] = None
        self._modified_regions: set[str] = set()
        # Persistent config
        self._config: dict[str, Any] = self._load_config()
        # Update state
        self._update_installer_path: Optional[Path] = None
        self._update_version: Optional[str] = None
        self._update_release_url: Optional[str] = None
        self._update_ready: bool = False
        self._update_progress_lock = threading.Lock()
        self._update_progress: dict[str, Any] = {
            "status": "idle",
            "downloaded_bytes": 0,
            "total_bytes": None,
            "percent": 0,
            "error": None,
        }
        self._pending_update_failure: Optional[dict[str, str]] = _pending_update_failure

    def set_window(self, window: webview.Window) -> None:
        self._window = window

    # --- Config persistence ---
    @staticmethod
    def _load_config() -> dict[str, Any]:
        try:
            if CONFIG_PATH.exists():
                return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
        except Exception:
            pass
        return {}

    def _save_config(self) -> None:
        try:
            CONFIG_PATH.write_text(json.dumps(self._config), encoding="utf-8")
        except Exception as e:
            log.warning("Failed to save config: %s", e)

    def get_pref(self, key: str, default: Any = None) -> Any:
        """Get a persistent preference value."""
        return self._config.get(key, default)

    def set_pref(self, key: str, value: Any) -> None:
        """Set and persist a preference value."""
        self._config[key] = value
        self._save_config()

    def startup_check(self) -> bool:
        """Called by JS when pywebview is ready"""
        time.sleep(0.5)

        threading.Thread(target=self._run_update_check, daemon=True).start()

        if self._pending_update_failure and self._window:
            payload_json = json.dumps(self._pending_update_failure)
            self._window.evaluate_js(
                f"window.showUpdateInstallFailed({payload_json});"
            )

        if len(sys.argv) > 1 and sys.argv[1].endswith('.atlas'):
            return self.load_atlas(sys.argv[1])
        else:
            return False

    def open_update_log(self, log_path: str) -> dict[str, Any]:
        """Open update failure log file in OS default app."""
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
        """Open URL using system default browser."""
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
        file_types = ('Atlas Files (*.atlas)', 'All files (*.*)')
        result = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False, file_types=file_types)
        if result:
            return self.load_atlas(result[0])
        return False

    def load_atlas(self, path_str: str) -> bool:
        log.debug("load_atlas received path: %r", path_str)
        try:
            self._atlas_path = Path(path_str)
            atlas_dir = self._atlas_path.parent
            
            with open(self._atlas_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            required_pages = _extract_required_pages(content)
            image_loader = {}
            
            if not self._window:
                return False

            for page_name in required_pages:
                expected_path = atlas_dir / page_name
                if expected_path.exists():
                    image_loader[page_name] = expected_path
                else:
                    self._window.evaluate_js(f"alert('Image \\\"{page_name}\\\" not found. Please locate it.')")
                    file_types = (f'{Path(page_name).stem} (*{Path(page_name).suffix})', 'All files (*.*)')
                    result = self._window.create_file_dialog(
                        webview.FileDialog.OPEN, 
                        allow_multiple=False, 
                        file_types=file_types,
                        directory=str(atlas_dir)
                    )
                    if result:
                        image_loader[page_name] = Path(result[0])
                    else:
                        self._window.evaluate_js("alert('Load cancelled.')")
                        return False

            self._processor = AtlasProcessor(auto_convert_atlas(content), image_loader)
            self._window.set_title(f"Atlas Toolkit v{get_current_version()} - {self._atlas_path.name}")
            
            # Clear modify state when loading a new atlas
            self._clear_modify_state()
            
            return True
            
        except Exception as e:
            if self._window:
                self._window.evaluate_js(f"alert('Error: {str(e)}')")
            return False

    def _clear_modify_state(self) -> None:
        """Reset all modify mode state."""
        self._modifier = None
        self._merged_image = None
        self._merged_atlas_text = None
        self._merged_pages = None
        self._pre_repack_image = None
        self._pre_repack_text = None
        self._modified_regions = set()

    def _mark_regions_modified(self, names: List[str]) -> None:
        self._modified_regions.update(names)

    def _build_modify_response(
        self,
        image: Image,
        atlas_text: str,
        extra: Optional[dict[str, object]] = None,
    ) -> dict[str, object]:
        from atlas_modifier import parse_atlas

        _, _, merged_regions = parse_atlas(atlas_text)
        region_bounds: dict[str, list[int]] = {}
        for name, info in merged_regions.items():
            region_bounds[name] = [*info.bounds, info.rotate]

        payload: dict[str, object] = {
            "image": self._image_to_base64(image),
            "regions": region_bounds,
            "modifiedRegions": sorted(self._modified_regions),
        }
        if extra:
            payload.update(extra)
        return payload

    @staticmethod
    def _parse_atlas_page_names(atlas_text: str) -> List[str]:
        names: List[str] = []
        for line in atlas_text.splitlines():
            stripped = line.strip()
            if (
                stripped
                and ":" not in stripped
                and stripped.lower().endswith(".png")
            ):
                names.append(stripped)
        return names

    def _extract_sprites_from_merged_pages(self) -> dict[str, Image]:
        from atlas_modifier import AtlasModifier, parse_atlas

        if not self._merged_pages or not self._merged_atlas_text:
            return {}

        page_names = self._parse_atlas_page_names(self._merged_atlas_text)
        page_images = {
            name: self._merged_pages[i]
            for i, name in enumerate(page_names)
            if i < len(self._merged_pages)
        }

        _, _, regions = parse_atlas(self._merged_atlas_text)
        sprites: dict[str, Image] = {}
        for name, info in regions.items():
            page_img = page_images.get(info.page)
            if page_img is not None:
                sprites[name] = AtlasModifier._extract_raw_sprite(page_img, info)
        return sprites

    def get_region_names(self) -> List[str]:
        if not self._processor: return []
        return list(self._processor.regions.keys())

    def get_preview(self, names: List[str]) -> Optional[str]:
        if not self._processor: return None
        if not names: return None
        
        try:
            images: List[Image] = []
            max_w, max_h = 0, 0
            
            valid_names = [n for n in names if n in self._processor.regions]
            
            for name in valid_names:
                img = self._processor.extract_region(name)
                if img:
                    images.append(img)
                    max_w = max(max_w, img.width)
                    max_h = max(max_h, img.height)
            
            if not images:
                return None

            if len(images) == 1:
                return self._image_to_base64(images[0])

            from PIL import Image
            monitor = Image.new('RGBA', (max_w, max_h), (0, 0, 0, 0))

            for img in reversed(images):
                # Pad to monitor size so alpha_composite works (requires same size)
                layer = Image.new('RGBA', monitor.size, (0, 0, 0, 0))
                layer.paste(img, (0, 0))
                monitor = Image.alpha_composite(monitor, layer)

            return self._image_to_base64(monitor)
            
        except Exception as e:
            log.error("Preview error: %s", e)
        return None

    def _image_to_base64(self, img: Image) -> str:
        """Convert a PIL Image to a base64 data URI string."""
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode('utf-8')}"

    def extract_files(self, region_names: Optional[List[str]]) -> str:
        if not self._processor or not self._atlas_path or not self._window: 
            return "No atlas loaded or window not ready."
        
        target_regions = region_names if region_names else list(self._processor.regions.keys())
        is_single = len(target_regions) == 1
        default_dir = str(self._atlas_path.parent)
        save_path: Any = None
        
        if is_single:
            result = self._window.create_file_dialog(
                webview.FileDialog.SAVE, 
                directory=default_dir, 
                save_filename=f"{target_regions[0]}.png"
            )
            if result: save_path = result[0]
        else:
            result = self._window.create_file_dialog(
                webview.FileDialog.FOLDER, 
                directory=default_dir
            )
            if result: save_path = result[0]

        if not save_path: return "Cancelled"

        success_count = 0
        try:
            for name in target_regions:
                img = self._processor.extract_region(name)
                if img:
                    if is_single:
                        dest = Path(save_path)
                    else:
                        safe_name = "".join(x for x in name if x.isalnum() or x in "._- ")
                        dest = Path(save_path) / f"{safe_name}.png"
                    img.save(dest)
                    success_count += 1
            return f"Successfully extracted {success_count} images."
        except Exception as e:
            return f"Error: {str(e)}"

    # ==========================================
    #  MODIFY MODE API
    # ==========================================

    def _build_modify_view(self, *, clear_modified: bool = False) -> Optional[dict[str, object]]:
        """Rebuild modify mode from the originally-loaded atlas."""
        if not self._processor or not self._atlas_path:
            return None

        try:
            atlas_text = self._atlas_path.read_text(encoding='utf-8')
            base_image = self._processor.get_page_image()
            if not base_image:
                log.error("No loaded images in processor")
                return None

            if clear_modified:
                self._modified_regions = set()

            self._merged_image = None
            self._merged_atlas_text = None
            self._merged_pages = None
            self._pre_repack_image = None
            self._pre_repack_text = None

            self._modifier = AtlasModifier(
                auto_convert_atlas(atlas_text), self._atlas_path, base_image
            )

            region_bounds: dict[str, list[int]] = {}
            for name, info in self._modifier.regions.items():
                region_bounds[name] = [*info.bounds, info.rotate]

            pages = [p.filename for p in self._processor.pages]
            region_pages = {
                name: r.page_filename
                for name, r in self._processor.regions.items()
            }

            return {
                "image": self._image_to_base64(base_image),
                "regions": region_bounds,
                "pages": pages,
                "regionPages": region_pages,
                "activePage": pages[0] if pages else None,
                "modifiedRegions": sorted(self._modified_regions),
            }
        except Exception as e:
            log.error("Building modify view: %s", e)
            return None

    def enter_modify_mode(self) -> Optional[dict[str, object]]:
        """Prepare the AtlasModifier from the current loaded atlas.
        
        Returns:
            Dict with 'image' (base64) and 'regions' ({name: [x,y,w,h]}), or None.
        """
        data = self._build_modify_view(clear_modified=False)
        if data is not None:
            log.debug("Entered modify mode")
        return data

    def reset_modify_mode(self) -> Optional[dict[str, object]]:
        """Discard all in-session modifications and restore the original atlas view."""
        data = self._build_modify_view(clear_modified=True)
        if data is not None:
            log.debug("Reset modify mode to original atlas")
        return data

    def exit_modify_mode(self) -> None:
        """Clean up modify mode state."""
        self._clear_modify_state()
        log.debug("Exited modify mode")

    def select_mod_image(self, selected_names: List[str], repack: bool = False) -> Optional[dict[str, object]]:
        """Open a file dialog to select a mod PNG, then process it."""
        if not self._window or not self._modifier:
            return None
        
        file_types = ('PNG Files (*.png)', 'All files (*.*)')
        default_dir = str(self._atlas_path.parent) if self._atlas_path else ''
        
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=file_types,
            directory=default_dir,
        )
        
        if not result:
            return None
        
        return self.process_mod_image(result[0], selected_names, repack)

    def process_mod_image(self, path_str: str, selected_names: List[str], repack: bool = False) -> Optional[dict[str, object]]:
        """Run merge (and optional repack) and return dict with base64 preview + updated region bounds."""
        if not self._modifier:
            return None

        # Multi-page atlases take a dedicated path: extract every region from
        # every page, swap in the mod for the selected regions, and repack all
        # pages. (Mirrors the JS multi-page branch; repack flag is implied.)
        if self._processor and len(self._processor.pages) > 1:
            return self._process_mod_multi_page(path_str, selected_names)

        try:
            mod_path = Path(path_str)
            log.debug("Processing mod image: %s", mod_path)

            merged_image, merged_atlas_text = self._modifier.merge_mod_image(
                mod_path, selected_names
            )
            
            # Store pre-repack state so toggle can revert
            self._pre_repack_image = merged_image
            self._pre_repack_text = merged_atlas_text
            
            # Repack as the final step of the merge process
            if repack:
                log.debug("Running repack...")
                merged_image, merged_atlas_text = self._modifier.repack(
                    merged_image, merged_atlas_text
                )
            
            self._merged_image = merged_image
            self._merged_atlas_text = merged_atlas_text
            self._mark_regions_modified(selected_names)
            # Continue subsequent merges from the pre-repack canvas so toggling
            # repack off still reflects every mod, not an earlier repacked layout.
            self._modifier.adopt_merge_result(
                self._pre_repack_image, self._pre_repack_text
            )

            return self._build_modify_response(merged_image, merged_atlas_text)
            
        except Exception as e:
            log.error("Processing mod image: %s", e)
            if self._window:
                self._window.evaluate_js(f"showToast('Error: {str(e)}', 'error')")
            return None

    def _process_mod_multi_page(
        self, path_str: str, selected_names: List[str]
    ) -> Optional[dict[str, object]]:
        """Multi-page merge: extract every region (whitespace restored), replace
        the selected regions with the mod image, then repack across all pages."""
        if not self._processor:
            return None

        try:
            from PIL import Image
            from atlas_modifier import parse_atlas, repack_multi_page

            # 1. Extract every region (from merged output if continuing a session).
            if self._merged_pages and self._merged_atlas_text:
                all_sprites = self._extract_sprites_from_merged_pages()
            else:
                all_sprites = {}
                for name in self._processor.regions:
                    sprite = self._processor.extract_region(name)
                    if sprite is not None:
                        all_sprites[name] = sprite

            # 2. Replace the selected regions' sprites with the mod image.
            mod_img = Image.open(Path(path_str)).convert("RGBA")
            for name in selected_names:
                if name in all_sprites:
                    all_sprites[name] = mod_img

            # 3. Collect per-page infos and per-region metadata.
            page_infos: list[dict[str, object]] = [
                {
                    "page": p.filename,
                    "format": p.format,
                    "filter": f"{p.filter[0]}, {p.filter[1]}",
                    "repeat": p.repeat,
                    "pma": p.pma,
                }
                for p in self._processor.pages
            ]
            region_metas: dict[str, dict[str, object]] = {
                name: {
                    "atlas_name": r.atlas_name or name,
                    "index": r.index,
                    "split": r.split,
                    "pad": r.pad,
                    "extra_pairs": r.extra_pairs,
                }
                for name, r in self._processor.regions.items()
            }

            # 4. Repack across all pages.
            pages, atlas_text = repack_multi_page(
                all_sprites, len(self._processor.pages), page_infos, region_metas
            )

            self._merged_pages = pages
            self._merged_atlas_text = atlas_text
            self._merged_image = None
            self._pre_repack_image = None
            self._pre_repack_text = None
            self._mark_regions_modified(selected_names)

            _, _, merged_regions = parse_atlas(atlas_text)
            region_bounds: dict[str, list[int]] = {}
            region_pages: dict[str, str] = {}
            for name, info in merged_regions.items():
                region_bounds[name] = [*info.bounds, info.rotate]
                region_pages[name] = info.page

            return self._build_modify_response(
                pages[0] if pages else Image.new("RGBA", (1, 1)),
                atlas_text,
                extra={
                    "regionPages": region_pages,
                    "pages": [str(pi["page"]) for pi in page_infos],
                    "pageCount": len(pages),
                    "previewPage": str(page_infos[0]["page"]) if page_infos else None,
                },
            )

        except Exception as e:
            log.error("Processing multi-page mod image: %s", e)
            if self._window:
                self._window.evaluate_js(f"showToast('Error: {str(e)}', 'error')")
            return None

    def get_modify_page_preview(self, index: int) -> Optional[str]:
        """Return a base64 data-URI for page *index* of the current modify view.

        Serves the repacked merged pages when a multi-page merge is active,
        otherwise the originally-loaded atlas page images (so the page switcher
        works both before and after merging).
        """
        try:
            if self._merged_pages is not None:
                if 0 <= index < len(self._merged_pages):
                    return self._image_to_base64(self._merged_pages[index])
                return None
            if self._processor and 0 <= index < len(self._processor.pages):
                img = self._processor.get_page_image(
                    self._processor.pages[index].filename
                )
                if img is not None:
                    return self._image_to_base64(img)
            return None
        except Exception as e:
            log.error("get_modify_page_preview: %s", e)
            return None

    def save_modified(self) -> str:
        """Open a folder dialog and save the merged atlas files."""
        if not self._merged_atlas_text or not self._window:
            return "Error: No merged data to save."
        if not self._merged_pages and not (self._modifier and self._merged_image):
            return "Error: No merged data to save."

        default_dir = str(self._atlas_path.parent) if self._atlas_path else ''

        result = self._window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=default_dir,
        )

        if not result:
            return "Cancelled"

        try:
            output_dir = Path(result[0])
            if self._merged_pages is not None:
                self._save_multi_page(output_dir)
            else:
                if not self._modifier or self._merged_image is None:
                    return "Error: No merged data to save."
                self._modifier.save(output_dir, self._merged_image, self._merged_atlas_text)
            return f"Saved to: {output_dir}"
        except Exception as e:
            return f"Error: {str(e)}"

    def _save_multi_page(self, output_dir: Path) -> None:
        """Write each repacked page PNG (named by its original page filename),
        the updated atlas text, and copy the .skel if present."""
        if not self._processor or not self._atlas_path or self._merged_pages is None:
            return
        output_dir.mkdir(parents=True, exist_ok=True)

        for i, page_img in enumerate(self._merged_pages):
            if i < len(self._processor.pages):
                page_name = self._processor.pages[i].filename
            else:
                page_name = f"page{i}.png"
            page_img.save(output_dir / Path(page_name).name)

        if self._merged_atlas_text is not None:
            (output_dir / self._atlas_path.name).write_text(
                self._merged_atlas_text, encoding="utf-8"
            )

        skel_path = self._atlas_path.with_suffix(".skel")
        if skel_path.exists():
            shutil.copy(skel_path, output_dir / skel_path.name)

    def toggle_repack(self, repack: bool) -> Optional[dict[str, object]]:
        """Re-apply or remove repack on the existing merge result."""
        if not self._modifier or not self._pre_repack_image or not self._pre_repack_text:
            return None

        try:
            if repack:
                log.debug("Applying repack...")
                image, text = self._modifier.repack(
                    self._pre_repack_image, self._pre_repack_text
                )
            else:
                log.debug("Reverting to pre-repack merge result")
                image = self._pre_repack_image
                text = self._pre_repack_text

            self._merged_image = image
            self._merged_atlas_text = text
            self._modifier.adopt_merge_result(
                self._pre_repack_image, self._pre_repack_text
            )

            return self._build_modify_response(image, text)
        except Exception as e:
            log.error("toggle_repack: %s", e)
            return None

    def debug_log(self, msg: str) -> None:
        log.debug("JS: %s", msg)

    def on_drop(self, e: Any) -> None:
        try:
            files = e['dataTransfer']['files']
            if len(files) > 0:
                path = files[0].get('pywebviewFullPath')
                log.debug("Dropped file path: %s", path)
                if not path:
                    return
                
                path_lower = path.lower()
                
                if path_lower.endswith('.atlas'):
                    # Always load atlas (switch to extract mode if in modify)
                    if self.load_atlas(path):
                        if self._window:
                            self._window.evaluate_js("onAtlasLoadedFromPython()")
                
                elif any(path_lower.endswith(ext) for ext in IMAGE_EXTENSIONS):
                    # Image dropped — only handle in modify mode
                    if self._modifier:
                        self._handle_image_drop(path)
                    else:
                        if self._window:
                            self._window.evaluate_js("showToast('Enter Modify Mode first to drop images.', 'error')")
                else:
                    if self._window:
                        self._window.evaluate_js("showToast('Unsupported file type.', 'error')")
        except Exception as ex:
            log.error("Drop error: %s", ex)

    def _handle_image_drop(self, path: str) -> None:
        """Process a dropped image in modify mode directly, avoiding JS round-trip."""
        if not self._window:
            return

        # Get selected names and repack flag from JS (synchronous eval)
        selected_json = self._window.evaluate_js("JSON.stringify(getSelectedNames())")
        if not selected_json:
            self._window.evaluate_js("showToast('Select at least one region first.', 'error')")
            return

        names: list[str] = json.loads(selected_json)
        if len(names) == 0:
            self._window.evaluate_js("showToast('Select at least one region first.', 'error')")
            return

        repack_val = self._window.evaluate_js("document.getElementById('chk-repack').checked")
        repack = bool(repack_val)

        # Process directly in Python — no JS→Python→JS→Python round-trip
        result = self.process_mod_image(path, names, repack)
        if result:
            result_json = json.dumps(result)
            self._window.evaluate_js(f"window.onModImageProcessed({result_json})")
        else:
            self._window.evaluate_js("showToast('Failed to process mod image.', 'error')")

    def _set_update_progress(
        self,
        *,
        status: str,
        downloaded_bytes: int,
        total_bytes: Optional[int],
        percent: int,
        error: Optional[str] = None,
    ) -> None:
        with self._update_progress_lock:
            self._update_progress = {
                "status": status,
                "downloaded_bytes": downloaded_bytes,
                "total_bytes": total_bytes,
                "percent": percent,
                "error": error,
            }

    def get_update_download_progress(self) -> dict[str, Any]:
        with self._update_progress_lock:
            return dict(self._update_progress)

    def download_update(self) -> dict[str, Any]:
        """Download the Windows installer (Setup.exe) into the update folder."""
        if not is_running_as_exe():
            return {
                "ok": False,
                "error": "Dev mode does not support self-update install flow.",
            }
        if not _is_installed_build():
            return {
                "ok": False,
                "error": "Portable build does not support silent self-update. Use the releases page.",
            }

        self._update_installer_path = None
        self._update_version = None
        self._update_release_url = None
        self._update_ready = False
        self._set_update_progress(
            status="downloading",
            downloaded_bytes=0,
            total_bytes=None,
            percent=0,
        )

        try:
            latest = get_latest_release_info()
            asset = find_windows_installer_asset(latest.assets)

            update_dir = _get_update_dir()
            safe_tag = "".join(
                c if c.isalnum() or c in "._-" else "_"
                for c in (latest.tag_name or latest.latest_version or "latest")
            )
            target_installer_path = update_dir / f"{safe_tag}-{asset.name}"

            def _progress(downloaded: int, total: Optional[int]) -> None:
                percent = int((downloaded * 100) / total) if total and total > 0 else 0
                self._set_update_progress(
                    status="downloading",
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                    percent=max(0, min(100, percent)),
                )

            download_update_asset(
                download_url=asset.browser_download_url,
                target_path=target_installer_path,
                progress_cb=_progress,
            )

            target_exe = _get_running_executable_path()

            metadata = {
                "installer_path": str(target_installer_path),
                "target_exe_path": str(target_exe),
                "relaunch_args": sys.argv[1:],
                "version": latest.latest_version,
                "release_url": latest.release_url,
            }
            metadata_path = update_dir / "pending_update.json"
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )

            self._update_installer_path = target_installer_path
            self._update_version = latest.latest_version
            self._update_release_url = latest.release_url
            self._update_ready = True
            size = target_installer_path.stat().st_size
            self._set_update_progress(
                status="ready",
                downloaded_bytes=size,
                total_bytes=size,
                percent=100,
            )

            return {
                "ok": True,
                "version": latest.latest_version,
                "downloaded_path": str(target_installer_path),
            }
        except Exception as e:
            msg = str(e) or "Unknown update download error"
            self._set_update_progress(
                status="error",
                downloaded_bytes=0,
                total_bytes=None,
                percent=0,
                error=msg,
            )
            return {"ok": False, "error": msg}

    def restart_and_install_update(self) -> dict[str, Any]:
        """Spawn a detached cmd that runs the installer silently and relaunches the app."""
        if not is_running_as_exe():
            return {
                "ok": False,
                "error": "Dev mode does not support restart-and-install self-update.",
            }

        if not self._update_ready or not self._update_installer_path:
            return {
                "ok": False,
                "error": "No downloaded update found. Please download update first.",
            }

        installer_path = self._update_installer_path
        if not installer_path.exists() or installer_path.stat().st_size <= 0:
            return {
                "ok": False,
                "error": "Downloaded installer is missing or invalid.",
            }

        target_exe = _get_running_executable_path()
        if not target_exe.exists() or not target_exe.is_file():
            return {
                "ok": False,
                "error": f"Cannot locate executable for relaunch: {target_exe}",
            }

        update_dir = _get_update_dir()
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        inno_log_path = update_dir / f"inno_install_{timestamp}.log"
        script_text = _build_update_script(
            installer_path=installer_path,
            target_exe=target_exe,
            pid=os.getpid(),
            relaunch_args=list(sys.argv[1:]),
            release_url=self._update_release_url or "",
            inno_log_path=inno_log_path,
        )
        script_path = update_dir / f"install_update_{timestamp}_{os.getpid()}.cmd"

        try:
            script_path.write_text(script_text, encoding="utf-8")

            cmd_exe = os.environ.get("COMSPEC") or "cmd"
            popen_kwargs: dict[str, Any] = {
                "cwd": str(update_dir),
                "close_fds": True,
            }
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = (
                    subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                popen_kwargs["start_new_session"] = True

            subprocess.Popen([cmd_exe, "/d", "/c", str(script_path)], **popen_kwargs)

            if self._window:
                self._window.destroy()

            return {"ok": True}
        except Exception as e:
            return {
                "ok": False,
                "error": f"Failed to launch update installer: {e}",
            }
    
    def _run_update_check(self) -> None:
        """Run update check in background thread, push result to JS when done."""
        try:
            info = check_for_updates()
            if info and self._window:
                # Installed build → silent in-app installer update.
                # Portable build → open the releases page (no silent install).
                # Dev → open the source tree at the tag.
                if _is_installed_build():
                    action = "download"
                    source_tree_url = info.source_tree_url
                elif is_running_as_exe():
                    action = "open_source_tag"
                    source_tree_url = info.release_url
                else:
                    action = "open_source_tag"
                    source_tree_url = info.source_tree_url
                payload = {
                    "latestVersion": info.latest_version,
                    "releaseName": info.release_name,
                    "releaseUrl": info.release_url,
                    "tagName": info.tag_name,
                    "sourceTreeUrl": source_tree_url,
                    "action": action,
                }
                args_json = json.dumps(payload)
                self._window.evaluate_js(
                    f"window.showUpdateNotification({args_json});"
                )
        except Exception as e:
            log.warning("Update check failed: %s", e)

def setup_drop(window: webview.Window, api: Api) -> None:
    """Bind drag-and-drop events. Runs in a background thread via webview.start()."""
    try:
        from webview.dom import DOMEventHandler

        def _no_op(e: Any) -> None:
            pass

        log.debug("Binding drop events...")
        doc = window.dom.document
        doc.events.dragover += DOMEventHandler(_no_op, True, True, debounce=500)  # type: ignore[operator]
        doc.events.drop += DOMEventHandler(api.on_drop, True, True)  # type: ignore[operator]
        log.debug("Drop events bound.")
    except Exception as e:
        log.error("Failed to setup drop events: %s", e)

if __name__ == '__main__':
    # Named mutex so the Inno Setup installer's AppMutex can detect a running
    # instance (used by CloseApplications during silent self-update). The handle
    # is intentionally left open for the process lifetime.
    if sys.platform == 'win32':
        try:
            import ctypes
            _single_instance_mutex = ctypes.windll.kernel32.CreateMutexW(
                None, False, "AtlasToolkitSingleInstanceMutex"
            )
        except Exception:
            _single_instance_mutex = None

    api = Api()

    # Calculate center position for primary monitor
    window_width, window_height = 1200, 800
    if sys.platform == 'win32':
        import ctypes
        screen_width = ctypes.windll.user32.GetSystemMetrics(0)   # SM_CXSCREEN
        screen_height = ctypes.windll.user32.GetSystemMetrics(1)  # SM_CYSCREEN
    else:
        _scr = webview.screens[0]
        screen_width, screen_height = _scr.width, _scr.height
    center_x = (screen_width - window_width) // 2
    center_y = (screen_height - window_height) // 2

    GUI_PATH = get_resource_path("ui/index.html")
    window = webview.create_window(
        f'Atlas Toolkit v{get_current_version()}', 
        url=str(GUI_PATH.absolute().as_uri()),
        width=window_width, height=window_height,
        min_size=(800, 500),
        x=center_x, y=center_y,
        resizable=True,
        js_api=api,
        background_color='#2b2b2b'
    )
    
    if window:
        api.set_window(window)
    else:
        sys.exit(1)
    
    webview.start(
        func=setup_drop, 
        args=(window, api), 
        # debug=True
    )