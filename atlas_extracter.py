from __future__ import annotations
import logging
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple, Union

from PIL import Image

logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')

@dataclass
class AtlasRegion:
    name: str           # unique key (e.g. "arm#2" for a duplicate region name)
    atlas_name: str     # real name written to the atlas (e.g. "arm")
    page_filename: str
    index: int = -1
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    offsets: Optional[Tuple[int, int, int, int]] = None
    rotate: int = 0
    split: Optional[List[int]] = None
    pad: Optional[List[int]] = None
    extra_pairs: List[Tuple[str, List[str]]] = field(default_factory=list)

@dataclass
class AtlasPage:
    filename: str
    size: Tuple[int, int] = (0, 0)
    format: str = "RGBA8888"
    filter: Tuple[str, str] = ("Nearest", "Nearest")
    repeat: str = "none"
    pma: bool = False
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
        region_name_counts: Dict[str, int] = {}

        def get_unique_region_key(atlas_name: str) -> str:
            nxt = region_name_counts.get(atlas_name, 0) + 1
            region_name_counts[atlas_name] = nxt
            return atlas_name if nxt == 1 else f"{atlas_name}#{nxt}"

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
                    elif key == 'size' and current_region.w == 0:
                        # Only apply size to region if we haven't got bounds yet
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
                            current_region.offsets = tuple(map(int, values[:4])) # type: ignore
                    elif key == 'index':
                        current_region.index = int(values[0])
                    elif key == 'split' and len(values) >= 4:
                        current_region.split = [int(v) for v in values]
                    elif key == 'pad' and len(values) >= 4:
                        current_region.pad = [int(v) for v in values]
                    else:
                        current_region.extra_pairs.append((key, list(values)))

                elif current_page:
                    if key == 'size':
                        current_page.size = (int(values[0]), int(values[1]))
                    elif key == 'format':
                        current_page.format = values[0]
                    elif key == 'filter':
                        current_page.filter = (values[0], values[1])
                    elif key == 'repeat':
                        current_page.repeat = values[0]
                    elif key == 'pma':
                        current_page.pma = str(values[0]).strip().lower() == 'true'

            else:
                # 3. If no colon and not .png -> It's a Region Name
                if current_page is None:
                    continue
                
                # Found a new region name -> Create object.
                # Duplicate names (common with Spine index: multi-region sprites)
                # get a unique dict key; the real name is kept in atlas_name.
                region_key = get_unique_region_key(line)
                current_region = AtlasRegion(
                    name=region_key,
                    atlas_name=line,
                    page_filename=current_page.filename,
                )
                self.regions[region_key] = current_region

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
                elif isinstance(source, Image.Image):
                    img = source.convert('RGBA')
                else:
                    logging.error(f"Unsupported image source type for {page.filename}: {type(source)}")
                    continue
                
                # Auto Scale Check (scale atlas coords to match real image)
                if page.size != (0, 0):
                    atlas_w, atlas_h = page.size
                    real_w, real_h = img.size
                    
                    if real_w != atlas_w or real_h != atlas_h:
                        page.scale_x = real_w / atlas_w
                        page.scale_y = real_h / atlas_h
                        logging.warning(
                            f"⚠️ Scale Mismatch: Atlas={atlas_w}x{atlas_h}, "
                            f"Real={real_w}x{real_h}. "
                            f"Scaling atlas coords (x{page.scale_x:.3f}, x{page.scale_y:.3f})"
                        )
                
                self._loaded_images[page.filename] = img
                logging.info(f"✅ Loaded {page.filename} ({img.size})")
                
            except Exception as e:
                logging.error(f"Failed to load image {page.filename}: {e}")

    @staticmethod
    def crop_and_rotate(
        image: Image.Image, x: int, y: int, w: int, h: int, rotate: int,
    ) -> Image.Image:
        """Crop a region from *image* and undo atlas rotation.

        Args:
            image: Source atlas image.
            x, y: Top-left position of the region in the atlas.
            w, h: Region dimensions (before atlas rotation is applied).
            rotate: Atlas rotation value (0, 90, 180, 270).

        Returns:
            The cropped sprite in its original (unrotated) orientation.
        """
        # When rotated 90/270, the atlas stores w/h swapped
        crop_w = h if rotate in (90, 270) else w
        crop_h = w if rotate in (90, 270) else h

        sprite = image.crop((x, y, x + crop_w, y + crop_h))

        if rotate == 90:
            sprite = sprite.transpose(Image.Transpose.ROTATE_270)
        elif rotate == 270:
            sprite = sprite.transpose(Image.Transpose.ROTATE_90)
        elif rotate == 180:
            sprite = sprite.transpose(Image.Transpose.ROTATE_180)

        return sprite

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

        x, y, raw_w, raw_h = region.x, region.y, region.w, region.h
        rot = region.rotate

        # Apply page scale factors (atlas coords → real image coords)
        page = self._page_map.get(region.page_filename)
        if page and (page.scale_x != 1.0 or page.scale_y != 1.0):
            sx, sy = page.scale_x, page.scale_y
            x = round(x * sx)
            y = round(y * sy)
            raw_w = round(raw_w * sx)
            raw_h = round(raw_h * sy)

        sprite = self.crop_and_rotate(base_img, x, y, raw_w, raw_h, rot)
        current_w, current_h = sprite.size

        # Offsets
        if region.offsets:
            off_x, off_y, orig_w, orig_h = region.offsets

            # Scale offsets to match real image
            if page and (page.scale_x != 1.0 or page.scale_y != 1.0):
                sx, sy = page.scale_x, page.scale_y
                off_x = round(off_x * sx)
                off_y = round(off_y * sy)
                orig_w = round(orig_w * sx)
                orig_h = round(orig_h * sy)

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