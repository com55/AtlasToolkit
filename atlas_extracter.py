from __future__ import annotations
import logging
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple, Union

from PIL import Image

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

@dataclass
class AtlasRegion:
    name: str
    page_filename: str
    index: int = -1
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    offsets: Optional[Tuple[int, int, int, int]] = None
    rotate: int = 0

@dataclass
class AtlasPage:
    filename: str
    size: Tuple[int, int] = (0, 0)
    format: str = "RGBA8888"
    filter: Tuple[str, str] = ("Nearest", "Nearest")
    repeat: str = "none"
    scale_x: float = 1.0
    scale_y: float = 1.0

class AtlasProcessor:
    def __init__(self, atlas_content: str, image_loader: Mapping[str, Union[str, bytes, Path, Image.Image]]):
        self.atlas_content = atlas_content
        self.pages: List[AtlasPage] = []
        self.regions: Dict[str, AtlasRegion] = {} 
        self._loaded_images: Dict[str, Image.Image] = {} 
        self._page_map: Dict[str, AtlasPage] = {}
        self._cache: Dict[str, Image.Image] = {} # Cache for extracted regions
        
        self._parse_atlas()
        if image_loader:
            self._load_images(image_loader)

    def _parse_atlas(self) -> None:
        lines = [line.strip() for line in self.atlas_content.splitlines()]
        iterator = iter(lines)
        
        current_page: Optional[AtlasPage] = None
        current_region: Optional[AtlasRegion] = None

        while True:
            try:
                line = next(iterator)
            except StopIteration:
                break

            if not line:
                # --- จุดที่แก้ไข --- 
                # เจอบรรทัดว่าง "ห้าม" Reset current_page หรือ current_region ทิ้ง
                # เพราะบางไฟล์มีบรรทัดว่างคั่นระหว่าง Name กับ Properties
                continue

            # 1. Check Page (ends with .png)
            if line.endswith('.png'):
                current_page = AtlasPage(filename=line)
                self.pages.append(current_page)
                self._page_map[line] = current_page
                current_region = None # New page starts, reset region context
                continue

            # 2. Check Key-Value Pair
            if ':' in line:
                key, value_str = line.split(':', 1)
                key = key.strip().lower()
                values = [v.strip() for v in value_str.split(',')]

                if current_region:
                    # Region Properties
                    if key == 'bounds':
                        if len(values) >= 4:
                            current_region.x = int(values[0])
                            current_region.y = int(values[1])
                            current_region.w = int(values[2])
                            current_region.h = int(values[3])
                    elif key == 'xy': # Support LibGDX old format
                        current_region.x = int(values[0])
                        current_region.y = int(values[1])
                    elif key == 'size': # Support LibGDX old format
                        current_region.w = int(values[0])
                        current_region.h = int(values[1])
                    elif key == 'rotate':
                        val = values[0].lower()
                        if val == 'true':
                            current_region.rotate = 90
                        elif val == 'false':
                            current_region.rotate = 0
                        else:
                            try:
                                current_region.rotate = int(val)
                            except ValueError:
                                current_region.rotate = 0
                    elif key == 'offsets':
                        if len(values) >= 4:
                            current_region.offsets = tuple(map(int, values)) # type: ignore
                    elif key == 'index':
                        current_region.index = int(values[0])

                elif current_page:
                    if key == 'size':
                        current_page.size = (int(values[0]), int(values[1]))
                    elif key == 'format':
                        current_page.format = values[0]
                    elif key == 'filter':
                        current_page.filter = (values[0], values[1])
                    elif key == 'repeat':
                        current_page.repeat = values[0]

            else:
                # 3. If no colon and not .png -> It's a Region Name
                if current_page is None:
                    continue
                
                # Found a new region name -> Create object
                current_region = AtlasRegion(name=line, page_filename=current_page.filename)
                self.regions[line] = current_region

    def _load_images(self, loader: Mapping[str, Union[str, bytes, Path, Image.Image]]) -> None:
        for page in self.pages:
            source = loader.get(page.filename)
            if source is None:
                for key, val in loader.items():
                    if page.filename in str(key):
                        source = val
                        break
            
            if source is None:
                logging.debug(f"❌ Image NOT FOUND for page: {page.filename}, skipping...")
                continue

            try:
                if isinstance(source, (str, Path)):
                    img = Image.open(source).convert('RGBA')
                elif isinstance(source, bytes):
                    img = Image.open(BytesIO(source)).convert('RGBA')
                else:
                    img = source.convert('RGBA')
                
                # Auto Scale Check (Resize if mismatch)
                if page.size != (0, 0):
                    atlas_w, atlas_h = page.size
                    real_w, real_h = img.size
                    
                    if real_w != atlas_w or real_h != atlas_h:
                        logging.warning(f"⚠️ Scale Mismatch: Atlas={atlas_w}x{atlas_h}, Real={real_w}x{real_h}. Resizing...")
                        img = img.resize((atlas_w, atlas_h), Image.Resampling.LANCZOS)
                
                self._loaded_images[page.filename] = img
                logging.info(f"✅ Loaded {page.filename} ({img.size})")
                
            except Exception as e:
                logging.error(f"Failed to load image {page.filename}: {e}")

    def get_page_image(self, page_filename: Optional[str] = None) -> Optional[Image.Image]:
        """Get a loaded page image by filename, or the first one if not specified."""
        if page_filename:
            return self._loaded_images.get(page_filename)
        if self._loaded_images:
            return next(iter(self._loaded_images.values()))
        return None

    def extract_region(self, name: str) -> Optional[Image.Image]:
        region = self.regions.get(name)
        if not region: return None

        base_img = self._loaded_images.get(region.page_filename)
        if not base_img: return None

        # Logic from 1atlas_processor copy.py
        x, y, raw_w, raw_h = region.x, region.y, region.w, region.h
        rot = region.rotate

        # Crop packed sprite
        # If rotated (90/270), dimensions in atlas are swapped
        crop_w = raw_h if rot in (90, 270) else raw_w
        crop_h = raw_w if rot in (90, 270) else raw_h

        # Use base_img size for safety check only (optional, strict crop)
        sprite = base_img.crop((x, y, x + crop_w, y + crop_h))

        # Rotate
        if rot:
            if rot == 90:
                sprite = sprite.transpose(Image.Transpose.ROTATE_270)
            elif rot == 270:
                sprite = sprite.transpose(Image.Transpose.ROTATE_90)
            elif rot == 180:
                sprite = sprite.transpose(Image.Transpose.ROTATE_180)
            
            # Update dimensions after rotation (should match raw_w, raw_h if 90/270 ??? No, wait)
            # 1atlas_processor logic: w, h = sprite.size (after rotation)

        current_w, current_h = sprite.size

        # Offsets
        if region.offsets:
            off_x, off_y, orig_w, orig_h = region.offsets
            
            # 1atlas_processor copy.py Logic:
            # paste_x = off_x
            # paste_y = orig_h - off_y - h (where h is current sprite height)
            
            canvas = Image.new('RGBA', (orig_w, orig_h), (0, 0, 0, 0))
            paste_x = off_x
            paste_y = orig_h - off_y - current_h
            
            canvas.paste(sprite, (paste_x, paste_y))
            self._cache[name] = canvas
            return canvas
        else:
            self._cache[name] = sprite
            return sprite

    def extract_all(self) -> List[Tuple[str, Image.Image]]:
        results = []
        for name in self.regions:
            try:
                img = self.extract_region(name)
                if img:
                    results.append((name, img))
            except Exception as e:
                logging.error(f"Failed to extract {name}: {e}")
        return results