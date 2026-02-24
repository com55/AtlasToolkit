from __future__ import annotations
import sys
import base64
import webview
import time
import threading
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional
from atlas_converter import auto_convert_atlas
from atlas_extracter import AtlasProcessor
from atlas_modifier import AtlasModifier
from updater import check_for_updates, get_current_version, is_running_as_exe

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

def get_resource_path(path: str) -> Path:
    """Get path to a resource file embedded in the executable.

    In Nuitka onefile mode, ``__file__`` resolves to the temporary directory
    where embedded resources are unpacked.
    """
    return Path(__file__).parent / path


IMAGE_EXTENSIONS = {'.png'}


class Api:
    def __init__(self) -> None:
        self._atlas_path: Optional[Path] = None
        self._processor: Optional[AtlasProcessor] = None
        self._window: Optional[webview.Window] = None
        # Modify mode state
        self._modifier: Optional[AtlasModifier] = None
        self._merged_image: Optional[Image] = None
        self._merged_atlas_text: Optional[str] = None
        # Pre-repack state (merge output before repack was applied)
        self._pre_repack_image: Optional[Image] = None
        self._pre_repack_text: Optional[str] = None

    def set_window(self, window: webview.Window) -> None:
        self._window = window

    def startup_check(self) -> bool:
        """Called by JS when pywebview is ready"""
        time.sleep(0.5)

        threading.Thread(target=self._run_update_check, daemon=True).start()

        if len(sys.argv) > 1 and sys.argv[1].endswith('.atlas'):
            return self.load_atlas(sys.argv[1])
        else:
            return False

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
            
            required_pages = [line.strip() for line in content.splitlines() if line.strip().endswith('.png')]
            image_loader = {}
            
            if not self._window:
                return False

            for page_name in required_pages:
                expected_path = atlas_dir / page_name
                if expected_path.exists():
                    image_loader[page_name] = expected_path
                else:
                    self._window.evaluate_js(f"alert('Image \\\"{page_name}\\\" not found. Please locate it.')")
                    file_types = (f'{page_name} ({page_name})', 'PNG Files (*.png)', 'All files (*.*)')
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
        self._pre_repack_image = None
        self._pre_repack_text = None

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

            from PIL import Image
            monitor = Image.new('RGBA', (max_w, max_h), (0, 0, 0, 0))

            for img in reversed(images):
                monitor.paste(img, (0, 0), img)

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

    def enter_modify_mode(self) -> Optional[dict[str, object]]:
        """Prepare the AtlasModifier from the current loaded atlas.
        
        Returns:
            Dict with 'image' (base64) and 'regions' ({name: [x,y,w,h]}), or None.
        """
        if not self._processor or not self._atlas_path:
            return None
        
        try:
            atlas_text = self._atlas_path.read_text(encoding='utf-8')
            
            # Get the first loaded page image as the base
            base_image = self._processor.get_page_image()
            if not base_image:
                log.error("No loaded images in processor")
                return None
            
            self._modifier = AtlasModifier(auto_convert_atlas(atlas_text), self._atlas_path, base_image)
            self._merged_image = None
            self._merged_atlas_text = None
            log.debug("Entered modify mode")
            
            # Build region bounds dict for client-side overlay
            # Each value: [x, y, w, h, rotate]
            region_bounds: dict[str, list[int]] = {}
            for name, info in self._modifier.regions.items():
                region_bounds[name] = [*info.bounds, info.rotate]
            
            return {
                "image": self._image_to_base64(base_image),
                "regions": region_bounds,
            }
            
        except Exception as e:
            log.error("Entering modify mode: %s", e)
            return None

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
            
            # Parse updated bounds from merged atlas text
            from atlas_modifier import parse_atlas
            _, _, merged_regions = parse_atlas(merged_atlas_text)
            region_bounds: dict[str, list[int]] = {}
            for name, info in merged_regions.items():
                region_bounds[name] = [*info.bounds, info.rotate]
            
            return {
                "image": self._image_to_base64(merged_image),
                "regions": region_bounds,
            }
            
        except Exception as e:
            log.error("Processing mod image: %s", e)
            if self._window:
                self._window.evaluate_js(f"showToast('Error: {str(e)}', 'error')")
            return None

    def save_modified(self) -> str:
        """Open a folder dialog and save the merged atlas files."""
        if not self._modifier or not self._merged_image or not self._merged_atlas_text or not self._window:
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
            self._modifier.save(output_dir, self._merged_image, self._merged_atlas_text)
            return f"Saved to: {output_dir}"
        except Exception as e:
            return f"Error: {str(e)}"

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

            from atlas_modifier import parse_atlas
            _, _, merged_regions = parse_atlas(text)
            region_bounds: dict[str, list[int]] = {}
            for name, info in merged_regions.items():
                region_bounds[name] = [*info.bounds, info.rotate]

            return {
                "image": self._image_to_base64(image),
                "regions": region_bounds,
            }
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
                        # Get currently selected names from JS
                        if self._window:
                            self._window.evaluate_js("""
                                (async () => {
                                    const names = getSelectedNames();
                                    if (names.length === 0) {
                                        showToast('Select at least one region first.', 'error');
                                        return;
                                    }
                                    const repack = document.getElementById('chk-repack').checked;
                                    const result = await pywebview.api.process_mod_image('%s', names, repack);
                                    window.onModImageProcessed(result);
                                })();
                            """ % path.replace('\\', '\\\\').replace("'", "\\'"))
                    else:
                        if self._window:
                            self._window.evaluate_js("showToast('Enter Modify Mode first to drop images.', 'error')")
                else:
                    if self._window:
                        self._window.evaluate_js("showToast('Unsupported file type.', 'error')")
        except Exception as ex:
            log.error("Drop error: %s", ex)
    
    def _run_update_check(self) -> None:
        """Run update check in background thread, push result to JS when done."""
        try:
            info = check_for_updates()
            if info and self._window:
                safe_name = info.release_name.replace("'", "\\'")
                safe_url  = info.release_url.replace("'", "\\'")
                self._window.evaluate_js(
                    f"window.showUpdateNotification("
                    f"'{info.latest_version}', '{safe_name}', '{safe_url}')"
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
        doc.events.dragenter += DOMEventHandler(_no_op, True, True)  # type: ignore[operator]
        doc.events.dragover += DOMEventHandler(_no_op, True, True, debounce=500)  # type: ignore[operator]
        doc.events.drop += DOMEventHandler(api.on_drop, True, True)  # type: ignore[operator]
        log.debug("Drop events bound.")
    except Exception as e:
        log.error("Failed to setup drop events: %s", e)

if __name__ == '__main__':
    api = Api()
    
    # Calculate center position for primary monitor
    window_width, window_height = 900, 600
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