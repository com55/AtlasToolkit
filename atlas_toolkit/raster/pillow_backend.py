"""PillowRasterBackend -- the only concrete RasterBackend Phase 3 ships.

Implements atlas_toolkit.raster.base.RasterBackend's full non-mesh surface.
rasterize_mesh_mask and composite_destination_in raise NotImplementedError
-- see base.py's docstrings on those two methods for why (a separate,
later "mesh" stage per the architecture spec's §7 ordering).
"""
from __future__ import annotations

import io

import numpy as np
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
        # Pixel-center nonzero-winding-rule polygon fill -- Pillow's
        # ImageDraw has no compound-path winding-rule fill primitive, so
        # this is a from-scratch rasterizer, algorithm validated by the
        # poc/pyodide-pillow feasibility spike (commit 3b30de0) against
        # real Canvas ctx.fill('nonzero') output: 99.7-99.9% pixel
        # agreement, full winding-cancellation parity. No antialiasing
        # (hard edges) -- Canvas's fill is antialiased; that difference is
        # confined to polygon edges, not the interior, and is expected
        # (see this method's docstring in raster/base.py).
        edges: list[tuple[float, float, float, float]] = []
        for i in range(0, len(triangles), 3):
            ia, ib, ic = triangles[i] * 2, triangles[i + 1] * 2, triangles[i + 2] * 2
            pts = [
                (uvs[ia] * width, uvs[ia + 1] * height),
                (uvs[ib] * width, uvs[ib + 1] * height),
                (uvs[ic] * width, uvs[ic + 1] * height),
            ]
            for k in range(3):
                x0, y0 = pts[k]
                x1, y1 = pts[(k + 1) % 3]
                if y0 != y1:
                    edges.append((x0, y0, x1, y1))

        winding = np.zeros((height, width), dtype=np.int32)
        if edges:
            # Pixel centers, scaled by 2 to use integer arithmetic and avoid
            # floating-point precision issues with opposite-direction edges
            # (winding cancellation). Pixel (px,py) has center at (px+0.5, py+0.5),
            # which becomes (2*px+1, 2*py+1) in scaled coordinates.
            cy_scaled = (np.arange(height, dtype=np.int64) * 2 + 1)[:, np.newaxis]
            cx_scaled = (np.arange(width, dtype=np.int64) * 2 + 1)[np.newaxis, :]

            for x0, y0, x1, y1 in edges:
                # Scale edge endpoints by 2
                x0_s, y0_s = int(x0 * 2), int(y0 * 2)
                x1_s, y1_s = int(x1 * 2), int(y1 * 2)

                if y1_s > y0_s:
                    crosses = (cy_scaled >= y0_s) & (cy_scaled < y1_s)
                    direction = 1
                else:
                    crosses = (cy_scaled >= y1_s) & (cy_scaled < y0_s)
                    direction = -1

                # Compute cross_x in scaled coordinates using integer arithmetic
                # cross_x_s = x0_s + (cy_scaled - y0_s) * (x1_s - x0_s) / (y1_s - y0_s)
                # Rearranged to avoid division: cross_x_s * (y1_s - y0_s) = ...
                # We test: cross_x_s * dy_s > cx_scaled * dy_s
                # Which is: (x0_s + t * dx_s) * dy_s > cx_scaled * dy_s
                # where t = (cy_scaled - y0_s) / (y1_s - y0_s)
                # This avoids floating-point division entirely.
                dy_s = y1_s - y0_s
                dx_s = x1_s - x0_s
                numerator = (cy_scaled - y0_s) * dx_s + x0_s * dy_s  # (cy - y0) * dx + x0 * dy
                threshold = cx_scaled * dy_s                          # cx * dy

                if dy_s > 0:
                    contributes = crosses & (numerator > threshold)
                else:
                    contributes = crosses & (numerator < threshold)

                winding += np.where(contributes, direction, 0).astype(np.int32)

        alpha = np.where(winding != 0, 255, 0).astype(np.uint8)
        rgba = np.empty((height, width, 4), dtype=np.uint8)
        rgba[:, :, 0:3] = 255
        rgba[:, :, 3] = alpha
        img = Image.fromarray(rgba, mode="RGBA")
        return self._store(img)

    def composite_destination_in(
        self, dest: ImageHandle, mask: ImageHandle,
        src_rect: tuple[int, int, int, int] | None = None,
    ) -> None:
        raise NotImplementedError(
            "composite_destination_in lands in the separate 'mesh' stage "
            "(spec §7 ordering), not this one -- see "
            "atlas_toolkit/raster/base.py's docstring."
        )
