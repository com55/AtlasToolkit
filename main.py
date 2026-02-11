from __future__ import annotations
import sys
import base64
import webview
import time
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional
from atlas_extracter import AtlasProcessor
from atlas_modifier import AtlasModifier

if TYPE_CHECKING:
    from PIL.Image import Image


# Suppress noisy pywebview/WebView2 accessibility internal errors
import logging
logging.getLogger('pywebview').setLevel(logging.CRITICAL)


IMAGE_EXTENSIONS = {'.png'}


class Api:
    def __init__(self) -> None:
        self.atlas_path: Optional[Path] = None
        self.processor: Optional[AtlasProcessor] = None
        self.window: Optional[webview.Window] = None
        # Modify mode state
        self.modifier: Optional[AtlasModifier] = None
        self.merged_image: Optional[Image] = None
        self.merged_atlas_text: Optional[str] = None

    def set_window(self, window: webview.Window) -> None:
        self.window = window

    def startup_check(self) -> bool:
        """Called by JS when pywebview is ready"""
        time.sleep(0.5) 
        if len(sys.argv) > 1 and sys.argv[1].endswith('.atlas'):
            return self.load_atlas(sys.argv[1])
        else:
            return False

    def choose_file(self) -> bool:
        if not self.window:
            return False
        file_types = ('Atlas Files (*.atlas)', 'All files (*.*)')
        result = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False, file_types=file_types)
        if result:
            return self.load_atlas(result[0])
        return False

    def load_atlas(self, path_str: str) -> bool:
        print(f"DEBUG: load_atlas received path: {repr(path_str)}")
        try:
            self.atlas_path = Path(path_str)
            atlas_dir = self.atlas_path.parent
            
            with open(self.atlas_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            required_pages = [line.strip() for line in content.splitlines() if line.strip().endswith('.png')]
            image_loader = {}
            
            if not self.window:
                return False

            for page_name in required_pages:
                expected_path = atlas_dir / page_name
                if expected_path.exists():
                    image_loader[page_name] = expected_path
                else:
                    self.window.evaluate_js(f"alert('Image \\\"{page_name}\\\" not found. Please locate it.')")
                    file_types = (f'{page_name} ({page_name})', 'PNG Files (*.png)', 'All files (*.*)')
                    result = self.window.create_file_dialog(
                        webview.FileDialog.OPEN, 
                        allow_multiple=False, 
                        file_types=file_types,
                        directory=str(atlas_dir)
                    )
                    if result:
                        image_loader[page_name] = Path(result[0])
                    else:
                        self.window.evaluate_js("alert('Load cancelled.')")
                        return False

            self.processor = AtlasProcessor(content, image_loader)
            self.window.set_title(f"Atlas Extracter - {self.atlas_path.name}")
            
            # Clear modify state when loading a new atlas
            self._clear_modify_state()
            
            return True
            
        except Exception as e:
            if self.window:
                self.window.evaluate_js(f"alert('Error: {str(e)}')")
            return False

    def _clear_modify_state(self) -> None:
        """Reset all modify mode state."""
        self.modifier = None
        self.merged_image = None
        self.merged_atlas_text = None

    def get_region_names(self) -> List[str]:
        if not self.processor: return []
        return list(self.processor.regions.keys())

    def get_preview(self, names: List[str]) -> Optional[str]:
        if not self.processor: return None
        if not names: return None
        
        try:
            images: List[Image] = []
            max_w, max_h = 0, 0
            
            valid_names = [n for n in names if n in self.processor.regions]
            
            for name in valid_names:
                img = self.processor.extract_region(name)
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
            print(f"Preview Error: {e}")
        return None

    def _image_to_base64(self, img: Image) -> str:
        """Convert a PIL Image to a base64 data URI string."""
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode('utf-8')}"

    def extract_files(self, region_names: Optional[List[str]]) -> str:
        if not self.processor or not self.atlas_path or not self.window: 
            return "No atlas loaded or window not ready."
        
        target_regions = region_names if region_names else list(self.processor.regions.keys())
        is_single = len(target_regions) == 1
        default_dir = str(self.atlas_path.parent)
        save_path: Any = None
        
        if is_single:
            result = self.window.create_file_dialog(
                webview.FileDialog.SAVE, 
                directory=default_dir, 
                save_filename=f"{target_regions[0]}.png"
            )
            if result: save_path = result[0]
        else:
            result = self.window.create_file_dialog(
                webview.FileDialog.FOLDER, 
                directory=default_dir
            )
            if result: save_path = result[0]

        if not save_path: return "Cancelled"

        success_count = 0
        try:
            for name in target_regions:
                img = self.processor.extract_region(name)
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
        if not self.processor or not self.atlas_path:
            return None
        
        try:
            atlas_text = self.atlas_path.read_text(encoding='utf-8')
            
            # Get the first loaded page image as the base
            base_image = self.processor.get_page_image()
            if not base_image:
                print("ERROR: No loaded images in processor")
                return None
            
            self.modifier = AtlasModifier(atlas_text, self.atlas_path, base_image)
            self.merged_image = None
            self.merged_atlas_text = None
            print("DEBUG: Entered modify mode")
            
            # Build region bounds dict for client-side overlay
            # Each value: [x, y, w, h, rotate]
            region_bounds: dict[str, list[int]] = {}
            for name, info in self.modifier.regions.items():
                region_bounds[name] = [*info.bounds, info.rotate]
            
            return {
                "image": self._image_to_base64(base_image),
                "regions": region_bounds,
            }
            
        except Exception as e:
            print(f"ERROR entering modify mode: {e}")
            return None

    def exit_modify_mode(self) -> None:
        """Clean up modify mode state."""
        self._clear_modify_state()
        print("DEBUG: Exited modify mode")

    def select_mod_image(self, selected_names: List[str]) -> Optional[dict[str, object]]:
        """Open a file dialog to select a mod PNG, then process it."""
        if not self.window or not self.modifier:
            return None
        
        file_types = ('PNG Files (*.png)', 'All files (*.*)')
        default_dir = str(self.atlas_path.parent) if self.atlas_path else ''
        
        result = self.window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=file_types,
            directory=default_dir,
        )
        
        if not result:
            return None
        
        return self.process_mod_image(result[0], selected_names)

    def process_mod_image(self, path_str: str, selected_names: List[str]) -> Optional[dict[str, object]]:
        """Run merge and return dict with base64 preview + updated region bounds."""
        if not self.modifier:
            return None
        
        try:
            mod_path = Path(path_str)
            print(f"DEBUG: Processing mod image: {mod_path}")
            
            merged_image, merged_atlas_text = self.modifier.merge_mod_image(
                mod_path, selected_names
            )
            
            self.merged_image = merged_image
            self.merged_atlas_text = merged_atlas_text
            
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
            print(f"ERROR processing mod image: {e}")
            if self.window:
                self.window.evaluate_js(f"showToast('Error: {str(e)}', 'error')")
            return None

    def save_modified(self) -> str:
        """Open a folder dialog and save the merged atlas files."""
        if not self.modifier or not self.merged_image or not self.merged_atlas_text or not self.window:
            return "Error: No merged data to save."
        
        default_dir = str(self.atlas_path.parent) if self.atlas_path else ''
        
        result = self.window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=default_dir,
        )
        
        if not result:
            return "Cancelled"
        
        try:
            output_dir = Path(result[0])
            self.modifier.save(output_dir, self.merged_image, self.merged_atlas_text)
            return f"Saved to: {output_dir}"
        except Exception as e:
            return f"Error: {str(e)}"

    def debug_log(self, msg: str) -> None:
        print(f"JS_DEBUG: {msg}")

    def on_drop(self, e: Any) -> None:
        try:
            files = e['dataTransfer']['files']
            if len(files) > 0:
                path = files[0].get('pywebviewFullPath')
                print(f"DEBUG: Dropped file path: {path}")
                if not path:
                    return
                
                path_lower = path.lower()
                
                if path_lower.endswith('.atlas'):
                    # Always load atlas (switch to extract mode if in modify)
                    if self.load_atlas(path):
                        if self.window:
                            self.window.evaluate_js("onAtlasLoadedFromPython()")
                
                elif any(path_lower.endswith(ext) for ext in IMAGE_EXTENSIONS):
                    # Image dropped — only handle in modify mode
                    if self.modifier:
                        # Get currently selected names from JS
                        if self.window:
                            self.window.evaluate_js("""
                                (async () => {
                                    const names = getSelectedNames();
                                    if (names.length === 0) {
                                        showToast('Select at least one region first.', 'error');
                                        return;
                                    }
                                    const result = await pywebview.api.process_mod_image('%s', names);
                                    window.onModImageProcessed(result);
                                })();
                            """ % path.replace('\\', '\\\\').replace("'", "\\'"))
                    else:
                        if self.window:
                            self.window.evaluate_js("showToast('Enter Modify Mode first to drop images.', 'error')")
                else:
                    if self.window:
                        self.window.evaluate_js("showToast('Unsupported file type.', 'error')")
        except Exception as ex:
            print(f"Drop Error: {ex}")

def setup_drop(window: webview.Window, api: Api) -> None:
    def on_loaded() -> None:
        print("DEBUG: Window loaded, binding events...")
        try:
            from webview.dom import DOMEventHandler

            # Bind drop handler on document (events bubble up from #drop-overlay)
            window.dom.document.on('drop', DOMEventHandler(api.on_drop, True, True))

            print("DEBUG: Event binding finished")
        except Exception as e:
            print(f"ERROR in on_loaded: {e}")

    window.events.loaded += on_loaded
    print("DEBUG: setup_drop: Registered on_loaded callback")

if __name__ == '__main__':
    api = Api()
    
    # Calculate Center Position
    import ctypes
    user32 = ctypes.windll.user32
    screen_width, screen_height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    window_width, window_height = 900, 600
    
    center_x = (screen_width - window_width) // 2
    center_y = (screen_height - window_height) // 2

    GUI_PATH = Path(__file__).parent / "ui" / "index.html"
    window = webview.create_window(
        'Atlas Extracter GUI', 
        url=str(GUI_PATH.absolute().as_uri()),
        width=window_width, height=window_height,
        x=center_x, y=center_y,
        resizable=True,
        js_api=api,
        background_color='#2b2b2b'
    )
    
    if window:
        api.set_window(window)
    else:
        sys.exit(1) 
    
    webview.start(setup_drop, (window, api))