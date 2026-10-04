"""Pristine extraction and processor options; page images are borrowed."""
from __future__ import annotations

from copy import deepcopy
from math import isfinite

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.domain.region_ops import round_half_even
from atlas_toolkit.engine.modifier import js_object_key_order
from atlas_toolkit.raster.base import ImageHandle, RasterBackend


class AtlasProcessor:
    def __init__(
        self, document: AtlasDocument, page_images: dict[str, ImageHandle], raster: RasterBackend,
    ) -> None:
        self.document = document
        self.pages = document.pages
        regions = document.regions_by_key()
        self.regions = {key: regions[key] for key in js_object_key_order(list(regions))}
        self._page_images = dict(page_images)
        self._page_map = {page.filename: page for page in self.pages}
        self._raster = raster
        self._mesh_lookup = None
        self._mask_enabled = False
        self._repack_mask_enabled = False
        self._nest_enabled = False
        self._nest_gap_distance = 4

    def get_page_image(self, page_name: str | None = None) -> ImageHandle | None:
        if page_name is None:
            return next(iter(self._page_images.values()), None)
        return self._page_images.get(page_name)

    def set_mesh_mask_data(
        self, lookup: dict | None, enabled: bool, repack_enabled: bool,
    ) -> None:
        self._mesh_lookup = deepcopy(lookup)
        self._mask_enabled = bool(enabled)
        self._repack_mask_enabled = bool(repack_enabled)

    def _mesh_available_for(self, name: str) -> dict | None:
        region = self.regions.get(name)
        if region is None or self._mesh_lookup is None:
            return None
        page = self._page_map.get(region.page_filename)
        if page is None or page.pma:
            return None
        return self._mesh_lookup.get(name)

    def get_mesh_geometry(self, name: str) -> dict | None:
        return self._mesh_available_for(name) if self._mask_enabled else None

    def get_repack_mesh_geometry(self, name: str) -> dict | None:
        return self._mesh_available_for(name) if self._repack_mask_enabled else None

    def set_nest_options(self, enabled: bool, gap_distance: object) -> None:
        self._nest_enabled = bool(enabled)
        self._nest_gap_distance = gap_distance

    def get_nest_options(self) -> dict:
        gap = self._nest_gap_distance
        valid_gap = (
            not isinstance(gap, bool)
            and (
                isinstance(gap, int)
                or isinstance(gap, float) and isfinite(gap)
            )
        )
        return {
            "enabled": self._nest_enabled,
            "gap_distance": gap if valid_gap else 4,
        }

    def extract_region(self, name: str) -> ImageHandle | None:
        """Return a fresh caller-owned image, or None for an unresolved key/page."""
        region = self.regions.get(name)
        if region is None:
            return None
        source = self.get_page_image(region.page_filename)
        if source is None:
            return None
        raster = self._raster
        page = self._page_map[region.page_filename]
        sx = sy = 1.0
        if page.size[0] and page.size[1]:
            width, height = raster.size(source)
            sx, sy = width / page.size[0], height / page.size[1]
        scaled = sx != 1.0 or sy != 1.0
        x, y, w, h = region.x, region.y, region.w, region.h
        if scaled:
            x, w = round_half_even(x * sx), round_half_even(w * sx)
            y, h = round_half_even(y * sy), round_half_even(h * sy)
        output = raster.crop_rotate(source, x, y, w, h, region.rotate)
        try:
            if region.offsets:
                left, bottom, original_w, original_h = region.offsets
                if scaled:
                    left, original_w = round_half_even(left * sx), round_half_even(original_w * sx)
                    bottom, original_h = round_half_even(bottom * sy), round_half_even(original_h * sy)
                sprite = output
                # Keep ownership of the crop if allocation fails.
                output = raster.create(original_w, original_h)
                try:
                    raster.paste(output, sprite, left, original_h - bottom - h)
                finally:
                    raster.dispose(sprite)
            geometry = self.get_mesh_geometry(name)
            if geometry is not None:
                mask = raster.rasterize_mesh_mask(
                    geometry["uvs"], geometry["triangles"], *raster.size(output),
                )
                try:
                    raster.composite_destination_in(output, mask)
                finally:
                    raster.dispose(mask)
            return output
        except BaseException:
            raster.dispose(output)
            raise

    def get_preview_image(self, names: list[str]) -> ImageHandle | None:
        images: list[ImageHandle] = []
        composite: ImageHandle | None = None
        try:
            for name in names:
                if name in self.regions:
                    image = self.extract_region(name)
                    if image is not None:
                        images.append(image)
            if not images:
                return None
            if len(images) == 1:
                return images.pop()

            max_w = max(self._raster.size(image)[0] for image in images)
            max_h = max(self._raster.size(image)[1] for image in images)
            composite = self._raster.create(max_w, max_h)
            for image in reversed(images):
                self._raster.paste(composite, image, 0, 0)
            result = composite
            composite = None
            return result
        finally:
            for image in images:
                self._raster.dispose(image)
            if composite is not None:
                self._raster.dispose(composite)
