"""RasterBackend -- the pixel-execution seam for the Python-canonical port.

Port of the abstract contract defined in the architecture spec
(docs/superpowers/specs/2026-09-10-python-canonical-architecture-design.md,
rev 7, §3). This module defines the FULL Protocol -- every method the whole
Phase-3 migration will eventually need, not just what atlas_toolkit's
"basic raster" stage (this one) implements. `PillowRasterBackend`
(pillow_backend.py) is the only concrete backend Phase 3 ships; no
CanvasRasterBackend exists or is planned -- per the architecture decision,
JS stops being a raster implementation entirely once the port lands.

Binding invariants for every concrete backend (not just PillowRasterBackend,
should another ever exist):

- Every `ImageHandle` a backend hands back refers to an RGBA image, for the
  handle's entire lifetime. `decode_png` converts non-RGBA source PNGs
  (Pillow opens PNGs as P/RGB/L/LA/RGBA depending on the file) to RGBA on
  load; every other allocating method (`create`, `crop_rotate`,
  `rotate_image`, `clone`) only ever produces RGBA images by construction.
  Without this, `paste`'s alpha-compositing math and `get_pixels`'s
  4-bytes-per-pixel contract would silently misbehave on non-RGBA input.
- `create(width, height)`'s initial fill is transparent (RGBA
  `(0, 0, 0, 0)`), matching a DOM canvas's implicit default -- pinned
  explicitly since Pillow's `Image.new` has no implicit default to rely on.
- A handle is valid from its allocation until its `dispose()` call. Using a
  disposed or never-allocated handle is a caller bug; `PillowRasterBackend`
  surfaces this as the natural `KeyError` from its internal registry dict
  lookup -- no dedicated exception type, since nothing in this spec or its
  real callers needs to distinguish "disposed" from "never existed."
  `AtlasSession` owns only handles allocated through its private facade and
  releases its roots and temporary reads on close; foreign backend handles
  remain the caller's responsibility.
- Never assert byte-identity of `encode_png`'s OUTPUT against another
  runtime's PNG encoder (a browser's `canvas.toBlob`, another Pillow
  build). Encoders differ byte-for-byte even for pixel-identical images.
  Cross-language parity is a DECODED-PIXEL property, checked by decoding
  both sides before comparing -- see tests/python/gen_js_reference_raster.mjs
  and its comparison tests for how this plan does it.
"""
from __future__ import annotations

from typing import NewType, Protocol

import numpy as np

ImageHandle = NewType("ImageHandle", int)


class RasterBackend(Protocol):
    def decode_png(self, data: bytes) -> ImageHandle: ...
    def encode_png(self, handle: ImageHandle) -> bytes: ...
    def create(self, width: int, height: int) -> ImageHandle: ...
    def size(self, handle: ImageHandle) -> tuple[int, int]: ...
    def from_pixels(self, width: int, height: int, data: bytes) -> ImageHandle:
        """New width x height image from raw RGBA bytes, row-major (4 bytes/
        pixel) -- the inverse of get_pixels."""

    def crop_rotate(
        self, handle: ImageHandle, x: int, y: int, w: int, h: int, rotate: int
    ) -> ImageHandle:
        """Mirrors core-region-ops.js's cropAndRotate exactly: crop then
        place into a canvas sized (w, h) as stored in the atlas.
        rotate in {0, 90, 180, 270} -- the STORED atlas rotation to undo,
        not an arbitrary rotation angle (core-region-ops.js:1-18's header
        comment documents the PIL-rotation-direction mapping this is
        derived from: rotate==90 -> PIL ROTATE_270 (undo via 90 CW),
        rotate==270 -> PIL ROTATE_90 (undo via 90 CCW), rotate==180 ->
        PIL ROTATE_180)."""

    def rotate_image(self, handle: ImageHandle, degrees: int) -> ImageHandle:
        """Rotate the WHOLE image, using the exact same `degrees` convention
        as crop_rotate's `rotate` parameter above (not a general/arbitrary
        rotation angle) -- this is a convenience wrapper for the common
        "whole canvas, no real crop" case atlas-modifier.js's
        _rotate90CCW/_rotate90CW/_rotate180 helpers cover (atlas-modifier.js:
        1002-1023), each of which is itself implemented purely via
        cropAndRotate(src, 0, 0, <swapped-or-not dims>, <rotate value>) --
        there is no separate whole-canvas rotation algorithm in the real
        JS to port independently. degrees=0 is an identity crop of the
        whole image (equivalent to clone())."""

    def paste(self, dest: ImageHandle, src: ImageHandle, x: int, y: int, *, premultiplied: bool = False) -> None:
        """Mutates dest in place -- same contract as ctx.drawImage(src, x, y):
        a Porter-Duff "source-over" alpha composite of src onto dest at
        (x, y), NOT a raw overwrite. A naive Image.paste(src, box) is wrong
        here: with no mask it is a memcpy that overwrites dest's alpha
        channel instead of blending into it.

        Anything outside dest's bounds is clipped, and a paste landing
        entirely outside is a no-op -- matching drawImage. Clipping is
        per-axis, so a source may overhang one edge while being clipped on
        another.

        This says nothing about HOW a backend achieves it: PillowRasterBackend
        originally built a full-size transparent overlay and composited the
        whole canvas, and now composites only the overlapping rectangle. Both
        satisfy this contract identically -- no caller can distinguish them,
        since every caller reaches images only through handles.

        With `premultiplied=True` (a pma page, spec 2026-10-26 §3.1) the
        composite is premultiplied over on the clipped rect, computed
        through the shared helper (raster/premultiplied.py), rounded
        half-even once and clipped to 0..255: where dest is (0,0,0,0)
        under src the result is src byte for byte, alpha-0 RGB included."""

    def rasterize_mesh_mask(
        self, uvs: list[float], triangles: list[int], width: int, height: int,
    ) -> ImageHandle:
        """Standalone opaque-white-inside mask, nonzero-winding-rule compound-
        path fill -- matches region-mesh-mask.js's rasterizeMeshMask exactly.
        `PillowRasterBackend`'s implementation is a from-scratch numpy-
        vectorized rasterizer (Pillow's ImageDraw has no native winding-rule
        compound-path fill primitive): pixel-center sampling, edges
        canonicalized to (low-y, high-y) storage order with true winding
        direction carried separately so that opposite-winding copies of the
        same physical edge (e.g. two independently-authored meshes sharing a
        boundary) compute an identical floating-point crossing test and
        cancel exactly under nonzero-winding fill, rather than merely
        approximately. No antialiasing (hard edges) -- Canvas's fill is
        antialiased; that difference is confined to polygon edges, not the
        interior, and is an accepted, documented divergence, not a bug."""

    def composite_destination_in(
        self, dest: ImageHandle, mask: ImageHandle,
        src_rect: tuple[int, int, int, int] | None = None,
        *, premultiplied: bool = False,
    ) -> None:
        """Mutates dest, keeping only pixels where mask is opaque (Porter-Duff
        destination-in, correct for straight/non-premultiplied alpha).
        `PillowRasterBackend`'s implementation composes `dest`'s and (the
        optionally `src_rect`-cropped) `mask`'s alpha channels via
        `PIL.ImageChops.multiply` (exact `(a*b)//255` per pixel), then calls
        `putalpha`. `ImageChops.multiply` itself does not enforce that its
        two operands share a size (it silently truncates to the smaller
        extent on a mismatch); `putalpha` is what actually raises
        `ValueError` on a size mismatch, turning a caller bug into a hard
        error rather than a silently truncated composite.

        With `premultiplied=True` (a pma page, spec 2026-10-26 §3.1) every
        channel c, RGB and alpha alike, becomes `(c * m) // 255` where m is
        the mask alpha -- the same integer formula the straight path uses
        for alpha: m = 0 gives (0,0,0,0), m = 255 leaves the pixel
        unchanged, and a partial m scales RGB and alpha together."""

    def read_alpha_mask(self, handle: ImageHandle) -> np.ndarray:
        """1-D bool ndarray, one entry per pixel, row-major, True where alpha > 0 -- matches
        repack-nest.js's thresholdAlpha exactly (`data[i*4+3] > 0 ? 1 : 0`,
        repack-nest.js:258). Feeds domain/packing.py's silhouette-based
        placement decisions, a later stage -- this stage only provides the
        primitive itself."""

    def get_pixels(self, handle: ImageHandle) -> bytes:
        """Raw RGBA bytes, row-major (4 bytes/pixel). Feeds
        engine/modifier.py's _canvasHash dedup (atlas-modifier.js:359-387,
        a later stage), which needs full color data, not just alpha --
        read_alpha_mask alone would silently collide sprites that differ in
        color but share a silhouette. That JS function's double-getImageData
        "priming read" is a workaround for a documented Chromium-specific
        GPU-readback bug; it does not apply to Pillow and is not ported --
        a single get_pixels call is sufficient."""

    def resize_lanczos(self, handle: ImageHandle, width: int, height: int, *, premultiplied: bool = False) -> ImageHandle:
        """New width x height image. With premultiplied=False (straight):
        Lanczos-3 resample in premultiplied alpha, byte-identical to
        lanczos-resize.js's CPU lanczosResizeRGBA (see raster/lanczos.py).
        Used by Forced Resizing in the preview.

        With `premultiplied=True` (a pma page, spec 2026-10-26 §3.1) the
        handle's pixels are treated as already premultiplied and resized
        via lanczos_resize_premultiplied: no premultiply / un-premultiply,
        no a > 0.5 keep-mask, and filtered alpha above 255 scales all four
        channels by 255/a before the half-up clamp."""

    def adopt(self, native: object) -> ImageHandle:
        """Store a COPY of a backend-native in-memory image (a PIL.Image for
        PillowRasterBackend), converted to RGBA. The caller keeps its object;
        later changes to it never reach the handle."""

    def export(self, handle: ImageHandle) -> object:
        """Return a COPY of the handle's image as a backend-native object
        the caller owns (a PIL.Image for PillowRasterBackend)."""

    def clone(self, handle: ImageHandle) -> ImageHandle: ...
    def dispose(self, handle: ImageHandle) -> None: ...
