"""PillowRasterBackend -- the only concrete RasterBackend Phase 3 ships.

Implements atlas_toolkit.raster.base.RasterBackend's full surface,
including the mesh-masking primitives (rasterize_mesh_mask,
composite_destination_in) added in the "mesh" stage.
"""
from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageChops

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

    def read_alpha_mask(self, handle: ImageHandle) -> np.ndarray:
        alpha_channel = self._images[handle].getchannel("A")
        return np.frombuffer(alpha_channel.tobytes(), dtype=np.uint8) != 0

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
        edges: list[tuple[float, float, float, float, int]] = []
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
                if y0 == y1:
                    continue
                direction = 1 if y1 > y0 else -1
                # Canonicalize storage order to (low-y point, high-y point)
                # BEFORE appending -- two edges that represent the same
                # physical segment but arrive with opposite winding (e.g.
                # from a triangle and its exact index-reversed twin) then
                # compute an identical floating-point cross_x, so nonzero-
                # winding cancellation is exact rather than depending on
                # two different orders of float operations agreeing by
                # chance. direction still carries the edge's TRUE original
                # direction, independent of storage order.
                if y0 > y1:
                    x0, y0, x1, y1 = x1, y1, x0, y0
                edges.append((x0, y0, x1, y1, direction))

        winding = np.zeros((height, width), dtype=np.int32)
        if edges:
            cy = (np.arange(height, dtype=np.float64) + 0.5)[:, np.newaxis]  # (H,1)
            cx = (np.arange(width, dtype=np.float64) + 0.5)[np.newaxis, :]   # (1,W)
            for x0, y0, x1, y1, direction in edges:
                # Canonical order guarantees y1 > y0 always now.
                crosses = (cy >= y0) & (cy < y1)
                t = (cy - y0) / (y1 - y0)
                cross_x = x0 + t * (x1 - x0)
                contributes = crosses & (cross_x > cx)
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
        # Porter-Duff destination-in for straight (non-premultiplied)
        # alpha: new_alpha = dest_alpha * mask_alpha (over [0,1]), RGB
        # unchanged. ImageChops.multiply on two "L" (single-channel)
        # images computes exactly (a*b)//255 per pixel, which is this
        # formula in 0-255 space -- but it does NOT itself enforce equal
        # sizes (it silently truncates to the smaller operand's extent on
        # a mismatch, verified directly). The actual guard is the
        # putalpha() call below: Image.putalpha raises ValueError on a
        # size mismatch between dest_img and new_alpha, which is what
        # turns a caller bug (mask, cropped by src_rect when given, not
        # sized to match dest -- see maskRawSprite's own crop-back
        # derivation in engine/modifier.py, which always produces a match
        # in production) into a hard error instead of a silently
        # truncated composite.
        #
        # src_rect's crop uses Image.crop(), which zero-pads (transparent)
        # any out-of-bounds region rather than raising or clamping --
        # unreachable today because mask_crop_rect_for_offsets's rect is
        # always within the full mesh mask's own bounds by construction,
        # but worth knowing if that invariant is ever loosened.
        mask_img = self._images[mask]
        if src_rect is not None:
            x, y, w, h = src_rect
            mask_img = mask_img.crop((x, y, x + w, y + h))
        dest_img = self._images[dest]
        new_alpha = ImageChops.multiply(dest_img.getchannel("A"), mask_img.getchannel("A"))
        dest_img.putalpha(new_alpha)
