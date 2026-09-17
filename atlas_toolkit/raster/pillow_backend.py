"""PillowRasterBackend -- the only concrete RasterBackend Phase 3 ships.

Implements atlas_toolkit.raster.base.RasterBackend's full non-mesh surface.
rasterize_mesh_mask and composite_destination_in raise NotImplementedError
-- see base.py's docstrings on those two methods for why (a separate,
later "mesh" stage per the architecture spec's §7 ordering).
"""
from __future__ import annotations

import io

from PIL import Image

from atlas_toolkit.raster.base import ImageHandle


class PillowRasterBackend:
    def __init__(self) -> None:
        self._images: dict[ImageHandle, Image.Image] = {}
        self._next_handle = 1

    def _store(self, img: Image.Image) -> ImageHandle:
        # INVARIANT: never store an Image object that is already reachable
        # from another handle. Every caller here passes a freshly-created
        # object (Image.open/new, .crop, .transpose, .copy), so this holds
        # today -- but `paste` now mutates its destination IN PLACE rather
        # than replacing the registry entry, so two handles sharing one
        # object would mean a paste into one silently corrupting the other.
        # The invariant was inert before that change and is load-bearing
        # after it; a future allocation site that returns an existing
        # object (an alias/view helper, a cache) must clone first.
        handle = ImageHandle(self._next_handle)
        self._next_handle += 1
        self._images[handle] = img
        return handle

    def decode_png(self, data: bytes) -> ImageHandle:
        img = Image.open(io.BytesIO(data))
        img.load()  # force decode while the BytesIO buffer is still alive
        return self._store(img.convert("RGBA"))

    def encode_png(self, handle: ImageHandle) -> bytes:
        buf = io.BytesIO()
        self._images[handle].save(buf, format="PNG")
        return buf.getvalue()

    def create(self, width: int, height: int) -> ImageHandle:
        return self._store(Image.new("RGBA", (width, height), (0, 0, 0, 0)))

    def size(self, handle: ImageHandle) -> tuple[int, int]:
        return self._images[handle].size

    def crop_rotate(
        self, handle: ImageHandle, x: int, y: int, w: int, h: int, rotate: int
    ) -> ImageHandle:
        img = self._images[handle]
        is_swapped = rotate in (90, 270)
        crop_w, crop_h = (h, w) if is_swapped else (w, h)
        cropped = img.crop((x, y, x + crop_w, y + crop_h))
        if rotate == 90:
            result = cropped.transpose(Image.Transpose.ROTATE_270)
        elif rotate == 270:
            result = cropped.transpose(Image.Transpose.ROTATE_90)
        elif rotate == 180:
            result = cropped.transpose(Image.Transpose.ROTATE_180)
        else:
            result = cropped
        return self._store(result)

    def rotate_image(self, handle: ImageHandle, degrees: int) -> ImageHandle:
        w, h = self.size(handle)
        if degrees in (90, 270):
            return self.crop_rotate(handle, 0, 0, h, w, degrees)
        return self.crop_rotate(handle, 0, 0, w, h, degrees)

    def clone(self, handle: ImageHandle) -> ImageHandle:
        return self._store(self._images[handle].copy())

    def dispose(self, handle: ImageHandle) -> None:
        del self._images[handle]

    def paste(self, dest: ImageHandle, src: ImageHandle, x: int, y: int) -> None:
        # Composites only the overlapping rectangle, not the whole
        # destination. The previous whole-canvas version was O(destination
        # area) per call, which was harmless while the only caller pasted
        # once per mod image but is the hot path in _pack_and_emit (one
        # paste per unique sprite onto the full packed sheet).
        #
        # Image.alpha_composite needs both operands the same size, and the
        # clipped source region is exactly the clipped destination region,
        # so no transparent overlay is needed here at all. The final
        # Image.paste writes back a region that was already composited, so
        # its raw-overwrite semantics (see this module's alpha gotcha) are
        # correct rather than dangerous.
        dest_img = self._images[dest]
        src_img = self._images[src]
        dest_w, dest_h = dest_img.size
        src_w, src_h = src_img.size

        left = max(0, x)
        top = max(0, y)
        right = min(dest_w, x + src_w)
        bottom = min(dest_h, y + src_h)
        if left >= right or top >= bottom:
            return  # entirely outside the destination -- drawImage no-ops too

        region = dest_img.crop((left, top, right, bottom))
        clipped_src = src_img.crop((left - x, top - y, right - x, bottom - y))
        dest_img.paste(Image.alpha_composite(region, clipped_src), (left, top))

    def get_pixels(self, handle: ImageHandle) -> bytes:
        return self._images[handle].tobytes()

    def read_alpha_mask(self, handle: ImageHandle) -> list[bool]:
        alpha_channel = self._images[handle].getchannel("A")
        return [byte != 0 for byte in alpha_channel.tobytes()]

    def rasterize_mesh_mask(
        self, uvs: list[float], triangles: list[int], width: int, height: int,
    ) -> ImageHandle:
        raise NotImplementedError(
            "rasterize_mesh_mask lands in the separate 'mesh' stage (spec "
            "§7 ordering: basic raster -> modifier -> repack -> mesh), not "
            "this one -- see atlas_toolkit/raster/base.py's docstring."
        )

    def composite_destination_in(
        self, dest: ImageHandle, mask: ImageHandle,
        src_rect: tuple[int, int, int, int] | None = None,
    ) -> None:
        raise NotImplementedError(
            "composite_destination_in lands in the separate 'mesh' stage "
            "(spec §7 ordering), not this one -- see "
            "atlas_toolkit/raster/base.py's docstring."
        )
