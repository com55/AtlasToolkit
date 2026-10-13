"""Pristine extraction and processor options; page images are borrowed."""
from __future__ import annotations

from copy import deepcopy
from math import isfinite

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.domain.region_ops import extracted_canvas_size, scaled_region_geometry
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

    def extract_region(self, name: str, mask: bool = True) -> ImageHandle | None:
        """Return a fresh caller-owned image, or None for an unresolved key/page.
        mask=False skips the mesh crop (get_preview_image applies it itself
        after a forced resize)."""
        region = self.regions.get(name)
        if region is None:
            return None
        source = self.get_page_image(region.page_filename)
        if source is None:
            return None
        raster = self._raster
        x, y, w, h, offsets = scaled_region_geometry(region, *self._page_scale(region, source))
        output = raster.crop_rotate(source, x, y, w, h, region.rotate)
        try:
            if offsets:
                left, bottom, original_w, original_h = offsets
                sprite = output
                # Keep ownership of the crop if allocation fails.
                output = raster.create(original_w, original_h)
                try:
                    raster.paste(output, sprite, left, original_h - bottom - h)
                finally:
                    raster.dispose(sprite)
            if mask:
                self._mask_to_mesh(name, output)
            return output
        except BaseException:
            raster.dispose(output)
            raise

    def _mask_to_mesh(self, name: str, image: ImageHandle) -> None:
        """Crop `image` in place to the region's mesh, rasterized at the
        image's own size (no-op when Mesh Cropping gives no geometry)."""
        geometry = self.get_mesh_geometry(name)
        if geometry is None:
            return
        raster = self._raster
        mask = raster.rasterize_mesh_mask(geometry["uvs"], geometry["triangles"], *raster.size(image))
        try:
            raster.composite_destination_in(image, mask)
        finally:
            raster.dispose(mask)

    def _page_scale(self, region, source: ImageHandle) -> tuple[float, float]:
        """Loaded image size / declared page size (1.0 when undeclared)."""
        page = self._page_map[region.page_filename]
        if not (page.size[0] and page.size[1]):
            return 1.0, 1.0
        width, height = self._raster.size(source)
        return width / page.size[0], height / page.size[1]

    def preview_hull_layout(self, names: list[str], force_resize: bool) -> dict:
        """Hull polylines for the View Mode overlay -- port of
        AtlasProcessor.previewHullLayout. Ignores the Mesh Cropping toggle;
        skips a region whose page image is not loaded."""
        entries = []
        for name in names:
            region = self.regions.get(name)
            if region is None:
                continue
            source = self.get_page_image(region.page_filename)
            if source is None:
                continue
            width, height = extracted_canvas_size(region, *self._page_scale(region, source))
            mesh = self._mesh_available_for(name)
            entries.append({
                "width": width,
                "height": height,
                # Copy: the result leaves the session; the lookup must not.
                "uvs": list(mesh["uvs"]) if mesh else None,
                "hull_length": mesh.get("hull_length") if mesh else 0,
            })
        return layout_preview_hulls(entries, bool(force_resize))

    def get_preview_image(
        self, names: list[str], force_resize: bool = False,
    ) -> ImageHandle | None:
        """Stack the extracted regions, earlier names on top. One image is
        returned as-is. Forced resizing Lanczos-resizes every image to the
        largest-area one (renderPreviewCanvases).

        DELIBERATE DIVERGENCE from JS (user ruling 2026-10-13): with Mesh
        Cropping, a region that forced resizing stretches is resized
        UNMASKED and then masked with its mesh at the target size. Masking
        first let the Lanczos kernel smear the edge past the hull; this way
        the composite stops exactly at every hull, so replace_composite with
        split_composite round-trips it without loss. Unstretched regions and
        force_resize=False are unchanged."""
        images: list[ImageHandle] = []
        image_names: list[str] = []
        composite: ImageHandle | None = None
        try:
            for name in names:
                if name in self.regions:
                    image = self.extract_region(name, mask=False)
                    if image is not None:
                        images.append(image)
                        image_names.append(name)
            if not images:
                return None
            if len(images) == 1:
                image = images.pop()
                self._mask_to_mesh(image_names[0], image)
                return image

            frame = preview_composite_frame(
                [self._raster.size(image) for image in images], force_resize,
            )
            composite = self._raster.create(frame["width"], frame["height"])
            for image, name in zip(reversed(images), reversed(image_names)):
                if not frame["scale"] or self._raster.size(image) == (frame["width"], frame["height"]):
                    self._mask_to_mesh(name, image)
                    self._raster.paste(composite, image, 0, 0)
                    continue
                resized = self._raster.resize_lanczos(image, frame["width"], frame["height"])
                try:
                    self._mask_to_mesh(name, resized)
                    self._raster.paste(composite, resized, 0, 0)
                finally:
                    self._raster.dispose(resized)
            result = composite
            composite = None
            return result
        finally:
            for image in images:
                self._raster.dispose(image)
            if composite is not None:
                self._raster.dispose(composite)


def pick_force_resize_target(sizes: list[tuple[int, int]]) -> tuple[int, int]:
    """Greatest area, in list order; an equal area keeps the earlier entry."""
    best = sizes[0]
    for size in sizes[1:]:
        if size[0] * size[1] > best[0] * best[1]:
            best = size
    return best


def preview_composite_frame(sizes: list[tuple[int, int]], force_resize: bool) -> dict:
    """Frame for the multi-image preview composite. Forced resizing scales
    every image to the single largest area; otherwise max(w) x max(h),
    images unscaled. Port of atlas-extracter.js's previewCompositeFrame."""
    width = max(size[0] for size in sizes)
    height = max(size[1] for size in sizes)
    if not force_resize or len(sizes) < 2:
        return {"width": width, "height": height, "scale": False}
    width, height = pick_force_resize_target(sizes)
    return {"width": width, "height": height, "scale": True}


def layout_preview_hulls(entries: list[dict], force_resize: bool) -> dict:
    """Place each hull in the composite space of the preview image. Port of
    atlas-extracter.js's layoutPreviewHulls."""
    if not entries:
        return {"frame": {"width": 0, "height": 0, "scale": False}, "items": []}
    frame = preview_composite_frame([(e["width"], e["height"]) for e in entries], force_resize)
    items = []
    for e in entries:
        stretch = frame["scale"] and (e["width"] != frame["width"] or e["height"] != frame["height"])
        items.append({
            "width": e["width"],
            "height": e["height"],
            "scale_x": frame["width"] / e["width"] if stretch else 1,
            "scale_y": frame["height"] / e["height"] if stretch else 1,
            "uvs": e.get("uvs"),
            "hull_length": e.get("hull_length") or 0,
        })
    return {"frame": frame, "items": items}


def hull_polyline(item: dict) -> list[tuple[float, float]] | None:
    """Closed hull in composite pixels: the first hull_length UV pairs are
    Spine's outline, later pairs are interior. Port of hullPolyline."""
    n = int(item["hull_length"] or 0)
    uvs = item["uvs"]
    if uvs is None or n < 3 or len(uvs) < n * 2:
        return None
    return [
        (
            uvs[i * 2] * item["width"] * item["scale_x"],
            uvs[i * 2 + 1] * item["height"] * item["scale_y"],
        )
        for i in range(n)
    ]
