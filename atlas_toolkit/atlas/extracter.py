from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple, Union

from PIL import Image

from atlas_toolkit.core.document import AtlasDocument, Page, Region
from atlas_toolkit.core.region_ops import (
    crop_and_rotate as _crop_and_rotate,
    extract_region_from_page,
)

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


class AtlasProcessor:
    def __init__(
        self,
        atlas_content: str,
        image_loader: Mapping[str, Union[str, bytes, Path, Image.Image]],
    ):
        self.atlas_content = atlas_content
        self.pages: List[Page] = []
        self.regions: Dict[str, Region] = {}
        self._loaded_images: Dict[str, Image.Image] = {}
        self._page_map: Dict[str, Page] = {}
        self._cache: Dict[str, Image.Image] = {}

        doc = AtlasDocument.parse(atlas_content)
        self.pages = doc.pages
        self._page_map = {p.filename: p for p in self.pages}
        self.regions = doc.regions_by_key()

        if image_loader:
            self._load_images(image_loader)

    def _load_images(
        self, loader: Mapping[str, Union[str, bytes, Path, Image.Image]]
    ) -> None:
        for page in self.pages:
            source = loader.get(page.filename)
            if source is None:
                for key, val in loader.items():
                    if page.filename in str(key):
                        source = val
                        break

            if source is None:
                logging.debug(
                    "Image NOT FOUND for page: %s, skipping...", page.filename
                )
                continue

            try:
                if isinstance(source, (str, Path)):
                    img = Image.open(source).convert("RGBA")
                elif isinstance(source, bytes):
                    img = Image.open(BytesIO(source)).convert("RGBA")
                elif isinstance(source, Image.Image):
                    img = source.convert("RGBA")
                else:
                    logging.error(
                        "Unsupported image source type for %s: %s",
                        page.filename,
                        type(source),
                    )
                    continue

                if page.size != (0, 0):
                    atlas_w, atlas_h = page.size
                    real_w, real_h = img.size

                    if real_w != atlas_w or real_h != atlas_h:
                        page.scale_x = real_w / atlas_w
                        page.scale_y = real_h / atlas_h
                        logging.warning(
                            "Scale Mismatch: Atlas=%sx%s, Real=%sx%s. "
                            "Scaling atlas coords (x%.3f, x%.3f)",
                            atlas_w,
                            atlas_h,
                            real_w,
                            real_h,
                            page.scale_x,
                            page.scale_y,
                        )

                self._loaded_images[page.filename] = img
                logging.info("Loaded %s (%s)", page.filename, img.size)

            except Exception as e:
                logging.error("Failed to load image %s: %s", page.filename, e)

    @staticmethod
    def crop_and_rotate(
        image: Image.Image, x: int, y: int, w: int, h: int, rotate: int
    ) -> Image.Image:
        return _crop_and_rotate(image, x, y, w, h, rotate)

    def get_page_image(self, page_filename: Optional[str] = None) -> Optional[Image.Image]:
        if page_filename:
            return self._loaded_images.get(page_filename)
        if self._loaded_images:
            return next(iter(self._loaded_images.values()))
        return None

    def extract_region(self, name: str) -> Optional[Image.Image]:
        region = self.regions.get(name)
        if not region:
            return None

        base_img = self._loaded_images.get(region.page_filename)
        if not base_img:
            return None

        page = self._page_map.get(region.page_filename)
        sprite = extract_region_from_page(base_img, region, page)
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
                logging.error("Failed to extract %s: %s", name, e)
        return results
