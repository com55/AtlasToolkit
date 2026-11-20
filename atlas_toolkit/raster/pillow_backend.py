# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""PillowRasterBackend -- the only concrete RasterBackend Phase 3 ships.

Implements atlas_toolkit.raster.base.RasterBackend's full surface,
including the mesh-masking primitives (rasterize_mesh_mask,
composite_destination_in) added in the "mesh" stage.
"""
from __future__ import annotations

import hashlib
import io

import numpy as np
from PIL import Image, ImageChops

from atlas_toolkit.raster.base import ImageHandle
from atlas_toolkit.raster.lanczos import lanczos_resize_premultiplied, lanczos_resize_rgba
from atlas_toolkit.raster.premultiplied import premultiplied_over


def _without_metadata(img: Image.Image) -> Image.Image:
    """Drop Image.info (icc_profile, dpi, ...): stored images are plain RGBA
    data, and the app's canvas output never carries a colour profile."""
    img.info.clear()
    return img


_MESH_CACHE_MAX_BYTES = 64 * 1024 * 1024  # mask alpha bytes kept per backend


def _mesh_key(uvs: list[float], triangles: list[int], width: int, height: int) -> bytes:
    """Cache key for one mesh mask: the output size, the two list lengths
    (so moving values between uvs and triangles can never collide), then
    the exact uv floats and triangle indices. Geometry, never a region
    name: two regions sharing a mesh at the same size share one entry."""
    digest = hashlib.sha256()
    digest.update(np.array([width, height, len(uvs), len(triangles)], dtype=np.int64).tobytes())
    digest.update(np.asarray(uvs, dtype=np.float64).tobytes())
    digest.update(np.asarray(triangles, dtype=np.int32).tobytes())
    return digest.digest()


# Use the run-length writer while its sort is cheaper than the dense diff:
# about 16 bytes of events per crossing against 1 output byte per pixel.
_RUNS_BYTES_PER_CROSSING = 16


def mesh_crossings(
    uvs: list[float], triangles: list[int], width: int, height: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Every (row, k, direction) where a mesh edge crosses a pixel-centre row.
    k counts the pixel centres strictly left of the crossing, so the edge
    adds `direction` to the winding of pixels 0..k-1 of that row.

    Same decisions as the original per-edge loop, made for all edges at once:
    edges stored low-y first with their true direction (exact cancellation of
    opposite twins), horizontal edges skipped, rows whose centre lies in
    [y0, y1), and a pixel counted when crossing_x > its centre (searchsorted
    with side="left" counts exactly those; a NaN crossing counts none).
    Errors match the loop too: a bad index raises IndexError, and the first
    non-finite y raises ValueError (NaN) or OverflowError (inf), like
    math.floor. Row and key arithmetic is int64 so wasm32 (32-bit intp)
    computes the same values as a 64-bit desktop."""
    u = np.asarray(uvs, dtype=np.float64).reshape(-1)
    t = np.asarray(triangles, dtype=np.int64).reshape(-1)
    if t.size % 3:
        raise IndexError("list index out of range")
    t = t.reshape(-1, 3)
    xs = u[t * 2] * width
    ys = u[t * 2 + 1] * height
    x0, y0 = xs.reshape(-1), ys.reshape(-1)
    x1, y1 = xs[:, [1, 2, 0]].reshape(-1), ys[:, [1, 2, 0]].reshape(-1)
    keep = y0 != y1
    x0, y0, x1, y1 = x0[keep], y0[keep], x1[keep], y1[keep]
    d = np.where(y1 > y0, 1, -1).astype(np.int64)
    swap = y0 > y1
    x0, x1 = np.where(swap, x1, x0), np.where(swap, x0, x1)
    y0, y1 = np.where(swap, y1, y0), np.where(swap, y0, y1)
    bad = ~(np.isfinite(y0) & np.isfinite(y1))
    if bad.any():
        i = int(np.argmax(bad))
        value = y0[i] if not np.isfinite(y0[i]) else y1[i]
        if np.isnan(value):
            raise ValueError("cannot convert float NaN to integer")
        raise OverflowError("cannot convert float infinity to integer")
    # Clamp in float before the cast: a huge y must not overflow the int.
    r0 = np.clip(np.floor(y0 - 0.5), 0, height).astype(np.int64)
    r1 = np.clip(np.ceil(y1 - 0.5) + 1, 0, height).astype(np.int64)
    n = np.maximum(r1 - r0, 0)
    counts = n.astype(np.intp)
    edge = np.repeat(np.arange(d.size, dtype=np.int64), counts)
    row = np.arange(edge.size, dtype=np.int64) - np.repeat(np.cumsum(n) - n, counts) + r0[edge]
    centre = row + 0.5
    inside = (centre >= y0[edge]) & (centre < y1[edge])
    edge, row, centre = edge[inside], row[inside], centre[inside]
    along = (centre - y0[edge]) / (y1[edge] - y0[edge])
    with np.errstate(invalid="ignore"):  # inf - inf and 0 * inf give NaN: no pixel
        cross = x0[edge] + along * (x1[edge] - x0[edge])
    live = ~np.isnan(cross)
    edge, row, cross = edge[live], row[live], cross[live]
    centres = np.arange(width, dtype=np.float64) + 0.5
    k = np.searchsorted(centres, cross, side="left").astype(np.int64)
    return row, k, d[edge]


def alpha_from_dense(
    row: np.ndarray, k: np.ndarray, d: np.ndarray, width: int, height: int,
) -> np.ndarray:
    """(height, width) uint8 0/255 from crossings: an int32 difference array
    per row (+d at column 0, -d at column k), summed along x."""
    stride = width + 1
    diff = np.zeros(height * stride, dtype=np.int32)
    weight = d.astype(np.int32)
    np.add.at(diff, row * stride, weight)
    np.add.at(diff, row * stride + k, -weight)
    winding = np.cumsum(diff.reshape(height, stride)[:, :width], axis=1, dtype=np.int32)
    out = (winding != 0).view(np.uint8)
    out *= 255
    return out


def alpha_from_runs(
    row: np.ndarray, k: np.ndarray, d: np.ndarray, width: int, height: int,
) -> np.ndarray:
    """Same result as alpha_from_dense for sparse crossings: sort the +-1
    events by flat position, sum them per position, and write each run of
    equal winding with one np.repeat (1 byte per pixel, no int32 canvas).
    An event at k == width lands on the next row's start; a row's events sum
    to zero, so it never leaks into that row."""
    total = width * height
    if d.size == 0 or total == 0:
        return np.zeros((height, width), dtype=np.uint8)
    start = row * width
    # Packed key: position * 2 + (1 for a +1 event, 0 for a -1 event).
    key = np.concatenate([start, start + k]) * 2 + np.concatenate([d > 0, d < 0]).astype(np.int64)
    key.sort()
    position = key >> 1
    weight = (key & 1) * 2 - 1
    first = np.flatnonzero(np.r_[True, position[1:] != position[:-1]])
    winding = np.cumsum(np.add.reduceat(weight, first))
    lengths = np.diff(np.r_[np.int64(0), position[first], np.int64(total)]).astype(np.intp)
    filled = np.r_[np.int64(0), winding] != 0
    values = np.where(filled, np.uint8(255), np.uint8(0))
    return np.repeat(values, lengths).reshape(height, width)


def _destination_in(dest_img: Image.Image, mask_img: Image.Image, premultiplied: bool) -> None:
    """composite_destination_in's per-pixel step on two same-size images,
    in place on dest_img."""
    if premultiplied:
        # pma page (spec 2026-10-26 §3.1): every channel x mask, the same
        # integer formula as straight alpha, so outside the mask RGB is
        # cleared too and partial coverage scales colour with alpha.
        mask_alpha = np.asarray(mask_img.getchannel("A"), dtype=np.uint16)
        scaled = (np.asarray(dest_img, dtype=np.uint16) * mask_alpha[..., None]) // 255
        dest_img.paste(Image.frombytes("RGBA", dest_img.size, scaled.astype(np.uint8).tobytes()), (0, 0))
        return
    dest_img.putalpha(ImageChops.multiply(dest_img.getchannel("A"), mask_img.getchannel("A")))


class PillowRasterBackend:
    def __init__(self) -> None:
        self._images: dict[ImageHandle, Image.Image] = {}
        self._next_handle = 1
        # Mesh-mask alpha by _mesh_key. Owned by this backend and gone with
        # it; a full cache stops taking entries rather than evicting.
        self._mesh_cache: dict[bytes, bytes] = {}
        self._mesh_cache_bytes = 0

    def _store(self, img: Image.Image) -> ImageHandle:
        # INVARIANT: never store an Image object that is already reachable
        # from another handle. Every caller here passes a freshly-created
        # object (Image.open/new, .convert, .crop, .transpose, .copy), so this holds
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
        return self._store(_without_metadata(img.convert("RGBA")))

    def encode_png(self, handle: ImageHandle) -> bytes:
        buf = io.BytesIO()
        self._images[handle].save(buf, format="PNG")
        return buf.getvalue()

    def create(self, width: int, height: int) -> ImageHandle:
        return self._store(Image.new("RGBA", (width, height), (0, 0, 0, 0)))

    def from_pixels(self, width: int, height: int, data: bytes) -> ImageHandle:
        return self._store(Image.frombytes("RGBA", (width, height), bytes(data)))

    def size(self, handle: ImageHandle) -> tuple[int, int]:
        return self._images[handle].size

    def crop_rotate(
        self, handle: ImageHandle, x: int, y: int, w: int, h: int, rotate: int
    ) -> ImageHandle:
        img = self._images[handle]
        is_swapped = rotate in (90, 270)
        crop_w, crop_h = (h, w) if is_swapped else (w, h)
        # Pillow zero-pads a crop past the edge, so atlas text could make it
        # allocate any size. A real region never outgrows its own page.
        if crop_w > img.width or crop_h > img.height:
            raise ValueError(
                f"crop {crop_w}x{crop_h} is larger than its {img.width}x{img.height} source image",
            )
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

    def adopt(self, native: Image.Image) -> ImageHandle:
        # convert() returns a copy even when the mode is already RGBA, so the
        # stored object is never the caller's (_store's no-aliasing invariant).
        return self._store(_without_metadata(native.convert("RGBA")))

    def view(self, handle: ImageHandle) -> Image.Image:
        """Borrow the stored image; callers must not mutate it."""
        return self._images[handle]

    def export(self, handle: ImageHandle) -> Image.Image:
        return self._images[handle].copy()

    def resize_lanczos(self, handle: ImageHandle, width: int, height: int, *, premultiplied: bool = False) -> ImageHandle:
        resize = lanczos_resize_premultiplied if premultiplied else lanczos_resize_rgba
        resized = resize(np.asarray(self._images[handle]), width, height)
        return self._store(Image.frombytes("RGBA", (width, height), resized.tobytes()))

    def dispose(self, handle: ImageHandle) -> None:
        del self._images[handle]

    def paste(self, dest: ImageHandle, src: ImageHandle, x: int, y: int, *, premultiplied: bool = False) -> None:
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
        if premultiplied:
            # pma page (spec 2026-10-26 §3.1): premultiplied over, rounded
            # once. Onto a clear dest this is a byte copy, so additive RGB
            # on alpha-0 pixels survives (alpha_composite would zero it).
            out = premultiplied_over(np.asarray(clipped_src, np.float64), np.asarray(region, np.float64))
            data = np.clip(np.rint(out), 0, 255).astype(np.uint8).tobytes()
            dest_img.paste(Image.frombytes("RGBA", region.size, data), (left, top))
            return
        dest_img.paste(Image.alpha_composite(region, clipped_src), (left, top))

    def get_pixels(self, handle: ImageHandle) -> bytes:
        return self._images[handle].tobytes()

    def read_alpha_mask(self, handle: ImageHandle) -> np.ndarray:
        alpha_channel = self._images[handle].getchannel("A")
        return np.frombuffer(alpha_channel.tobytes(), dtype=np.uint8) != 0

    def rasterize_mesh_mask(
        self, uvs: list[float], triangles: list[int], width: int, height: int,
    ) -> ImageHandle:
        # Repacks rasterize the same mesh at the same size several times
        # (masking a modded sprite, then its footprint), and repeated
        # builds of one atlas redo all of it, so the alpha is cached. A hit
        # and a miss both build a brand-new image from the same alpha
        # bytes, so callers cannot tell them apart and no two handles ever
        # share an Image object (see _store's invariant).
        key = _mesh_key(uvs, triangles, width, height)
        alpha = self._mesh_cache.get(key)
        if alpha is None:
            alpha = self._rasterize_alpha(uvs, triangles, width, height).tobytes()
            if self._mesh_cache_bytes + len(alpha) <= _MESH_CACHE_MAX_BYTES:
                self._mesh_cache[key] = alpha
                self._mesh_cache_bytes += len(alpha)
        white = Image.new("L", (width, height), 255)
        mask = Image.frombytes("L", (width, height), alpha)
        return self._store(Image.merge("RGBA", (white, white, white, mask)))

    def clear_mesh_cache(self) -> None:
        self._mesh_cache.clear()
        self._mesh_cache_bytes = 0

    def _rasterize_alpha(
        self, uvs: list[float], triangles: list[int], width: int, height: int,
    ) -> np.ndarray:
        # Pixel-center nonzero-winding-rule polygon fill -- Pillow's
        # ImageDraw has no compound-path winding-rule fill primitive, so
        # this is a from-scratch rasterizer, algorithm validated by the
        # poc/pyodide-pillow feasibility spike (commit 3b30de0) against
        # real Canvas ctx.fill('nonzero') output: 99.7-99.9% pixel
        # agreement, full winding-cancellation parity. No antialiasing
        # (hard edges) -- Canvas's fill is antialiased; that difference is
        # confined to polygon edges, not the interior, and is expected
        # (see rasterize_mesh_mask's docstring in raster/base.py).
        row, k, d = mesh_crossings(uvs, triangles, width, height)
        if _RUNS_BYTES_PER_CROSSING * d.size < width * height:
            return alpha_from_runs(row, k, d, width, height)
        return alpha_from_dense(row, k, d, width, height)

    def composite_destination_in(
        self, dest: ImageHandle, mask: ImageHandle,
        src_rect: tuple[int, int, int, int] | None = None,
        *, premultiplied: bool = False,
    ) -> None:
        # Porter-Duff destination-in for straight (non-premultiplied)
        # alpha: new_alpha = dest_alpha * mask_alpha (over [0,1]), RGB
        # unchanged. ImageChops.multiply on two "L" (single-channel)
        # images computes exactly (a*b)//255 per pixel, which is this
        # formula in 0-255 space -- but it does NOT itself enforce equal
        # sizes (it silently truncates to the smaller operand's extent on
        # a mismatch, verified directly). The explicit size check below
        # raises ValueError instead, which turns a caller bug (mask,
        # cropped by src_rect when given, not sized to match dest -- see
        # maskRawSprite's own crop-back derivation in engine/modifier.py,
        # which always produces a match in production) into a hard error
        # instead of a silently truncated composite.
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
        if mask_img.size != dest_img.size:
            raise ValueError("images do not match")
        # Work only on the box that holds dest's non-zero pixels (any
        # channel, so pma's alpha-0 RGB counts). Outside it dest is
        # (0, 0, 0, 0), which both formulas below leave unchanged, so the
        # result is the same bytes as masking the whole image. An extracted
        # region pasted onto its original canvas often fills a fifth of it.
        box = dest_img.getbbox(alpha_only=False)
        if box is None:
            return
        if box == (0, 0, *dest_img.size):
            _destination_in(dest_img, mask_img, premultiplied)
            return
        part = dest_img.crop(box)
        _destination_in(part, mask_img.crop(box), premultiplied)
        dest_img.paste(part, box[:2])
