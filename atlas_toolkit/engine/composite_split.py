"""Cut a replace_composite image back into one piece per region: the
inverse of extract_composite(force_resize=True). Spec
2026-10-13-composite-split §3.2-3.4.

Each survivor's canvas is scaled to fill the input image (per axis, per
region), its rect scales with it, and its piece is the input inside that
rect, cleared outside its own mesh hull. Pixels several hulls cover go to
every holder; a semi-transparent one has its alpha split exactly
(domain/alpha_split.py). Pixels no hull covers are dropped."""
from __future__ import annotations

from typing import NamedTuple

import numpy as np

from atlas_toolkit.domain.alpha_split import split_alphas
from atlas_toolkit.engine.modifier import mask_raw_sprite
from atlas_toolkit.raster.base import ImageHandle, RasterBackend


class Piece(NamedTuple):
    image: ImageHandle
    offsets: list[int]


def scaled_rect(meta: dict, width: int, height: int) -> tuple[int, int, int, int]:
    """(x0, y0, w, h) of the region's sprite once its canvas is scaled to
    width x height. Integer floor/ceil outward, clamped into the image,
    never empty. A region without offsets uses its own sprite as canvas."""
    w, h = meta["bounds"][2], meta["bounds"][3]
    offsets = meta["offsets"]
    left, bottom, cw, ch = offsets if offsets is not None else (0, 0, w, h)
    top = ch - bottom - h
    x0 = max(0, min(width - 1, left * width // cw))
    x1 = max(x0 + 1, min(width, -(-(left + w) * width // cw)))
    y0 = max(0, min(height - 1, top * height // ch))
    y1 = max(y0 + 1, min(height, -(-(top + h) * height // ch)))
    return x0, y0, x1 - x0, y1 - y0


def _hull(raster: RasterBackend, geometry: dict, offsets: list[int], w: int, h: int) -> np.ndarray:
    """Bool (h, w): the region's mesh hull inside its scaled rect -- the same
    mask the repack footprint and extract(mesh_crop=True) derive later."""
    probe = raster.from_pixels(w, h, b"\xff" * (w * h * 4))
    try:
        mask_raw_sprite(raster, probe, geometry, {"offsets": offsets})
        return raster.read_alpha_mask(probe).reshape(h, w)
    finally:
        raster.dispose(probe)


def split_composite(
    raster: RasterBackend,
    source: ImageHandle,
    survivors: list[str],
    metas: dict[str, dict],
    geometry: dict[str, dict],
) -> dict[str, Piece]:
    """One Piece per survivor (stack order, first = top). Every piece's
    offsets describe a canvas of the source's own size."""
    width, height = raster.size(source)
    pixels = np.frombuffer(raster.get_pixels(source), dtype=np.uint8).reshape(height, width, 4)
    rects, offsets, holders = {}, {}, np.zeros((len(survivors), height, width), bool)
    for k, name in enumerate(survivors):
        x0, y0, w, h = scaled_rect(metas[name], width, height)
        rects[name] = (x0, y0, w, h)
        offsets[name] = [x0, height - (y0 + h), width, height]
        holders[k, y0:y0 + h, x0:x0 + w] = _hull(raster, geometry[name], offsets[name], w, h)
    alphas = split_alphas(pixels[..., 3], holders)
    pieces: dict[str, Piece] = {}
    for k, name in enumerate(survivors):
        x0, y0, w, h = rects[name]
        piece = pixels[y0:y0 + h, x0:x0 + w].copy()
        piece[..., 3] = alphas[k, y0:y0 + h, x0:x0 + w]
        piece[piece[..., 3] == 0] = 0
        if not piece[..., 3].any():
            image = raster.from_pixels(1, 1, bytes(4))
            pieces[name] = Piece(image, [x0, height - y0 - 1, width, height])
            continue
        pieces[name] = Piece(raster.from_pixels(w, h, piece.tobytes()), offsets[name])
    return pieces
