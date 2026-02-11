from __future__ import annotations
import sys
import base64
import webview
import time
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional
from atlas_extracter import AtlasProcessor

if TYPE_CHECKING:
    from PIL.Image import Image


# HTML_TEMPLATE removed. Using ui/index.html instead.

# Suppress noisy pywebview/WebView2 accessibility internal errors
import logging
logging.getLogger('pywebview').setLevel(logging.CRITICAL)


class Api:
    def __init__(self) -> None:
        self.atlas_path: Optional[Path] = None
        self.processor: Optional[AtlasProcessor] = None
        self.window: Optional[webview.Window] = None

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
                    self.window.evaluate_js(f"alert('Image \"{page_name}\" not found. Please locate it.')")
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
            return True
            
        except Exception as e:
            if self.window:
                self.window.evaluate_js(f"alert('Error: {str(e)}')")
            return False

    def get_region_names(self) -> List[str]:
        if not self.processor: return []
        return list(self.processor.regions.keys())

    def get_preview(self, names: List[str]) -> Optional[str]:
        if not self.processor: return None
        if not names: return None
        
        try:
            # 1. Extract all images and store them with their index/order
            # User wants: "First in list = Top layer".
            # Painter's alg: Draw bottom layer first.
            # So we need to draw reverse of the input list.
            
            images: List[Image] = []
            max_w, max_h = 0, 0
            
            # Filter valid names and loading
            valid_names = [n for n in names if n in self.processor.regions]
            
            for name in valid_names:
                img = self.processor.extract_region(name)
                if img:
                    images.append(img)
                    max_w = max(max_w, img.width)
                    max_h = max(max_h, img.height)
            
            if not images:
                return None

            # 2. Create Canvas
            from PIL import Image
            monitor = Image.new('RGBA', (max_w, max_h), (0, 0, 0, 0))

            # 3. Draw in REVERSE order (Bottom -> Top)
            # Input `names` is sorted by UI (index 0..N). 
            # If names[0] is Top, we must draw it LAST.
            for img in reversed(images):
                # Center the image or align top-left? 
                # Usually atlas parts have offsets baked in by extract_region.
                # If they are just loose parts, aligning 0,0 is standard.
                monitor.paste(img, (0, 0), img)

            buffered = BytesIO()
            monitor.save(buffered, format="PNG")
            return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode('utf-8')}"
            
        except Exception as e:
            print(f"Preview Error: {e}")
        return None

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
            if result: save_path = result
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

    def debug_log(self, msg: str) -> None:
        print(f"JS_DEBUG: {msg}")

    def on_drop(self, e: Any) -> None:
        try:
            files = e['dataTransfer']['files']
            if len(files) > 0:
                path = files[0].get('pywebviewFullPath')
                print(f"DEBUG: Dropped file path: {path}")
                if path and path.lower().endswith('.atlas'):
                    if self.load_atlas(path):
                        if self.window:
                            self.window.evaluate_js("onAtlasLoadedFromPython()")
                else:
                    if self.window:
                        self.window.evaluate_js("showToast('Please drop a valid .atlas file.', 'error')")
        except Exception as ex:
            print(f"Drop Error: {ex}")

def setup_drop(window: webview.Window, api: Api):
    def on_loaded():
        print("DEBUG: Window loaded, binding events...")
        try:
            from webview.dom import DOMEventHandler
            overlay = window.dom.get_element('#drop-overlay')
            if overlay:
                print("DEBUG: Found #drop-overlay, binding drop event")
                overlay.on('drop', DOMEventHandler(api.on_drop, True, True))
            else:
                print("ERROR: Could not find #drop-overlay in DOM")
            
            # Fallback
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