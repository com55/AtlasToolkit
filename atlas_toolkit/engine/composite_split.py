"""Cut a replace_composite image back into one piece per region: the
inverse of extract_composite(force_resize=True). Spec
2026-10-13-composite-split §3.2-3.4.

Each survivor's canvas is scaled to fill the input image (per axis, per
region), its rect scales with it, and its piece is the input inside that
rect, cleared outside its own mesh hull. A region without mesh geometry
holds its whole rect instead. Pixels several holders cover go to every
holder; a semi-transparent one has its alpha split exactly
(domain/alpha_split.py). Pixels no holder covers are dropped.
On a pma page the stored RGB is premultiplied. When every survivor is
pma, the pieces are exact: premultiplied over of them, bottom first and
rounded once, gives back every covered input pixel, and an alpha-0
pixel's colour (additive) goes to the topmost holder. A mixed batch keeps
RGB x (its alpha / input alpha) on its pma pieces. pma pieces are cleared
outside their holder (spec 2026-10-26 §3.5)."""
from __future__ import annotations

from typing import NamedTuple

import numpy as np

from atlas_toolkit.domain.alpha_split import _BOTTOM, _TOP, split_alphas
from atlas_toolkit.engine.modifier import mask_raw_sprite
from atlas_toolkit.raster.base import ImageHandle, RasterBackend
from atlas_toolkit.raster.premultiplied import attenuate, premultiplied_over


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


def _hull(raster: RasterBackend, geometry: dict | None, offsets: list[int], w: int, h: int) -> np.ndarray:
    """Bool (h, w): the region's mesh hull inside its scaled rect -- the same
    mask the repack footprint and extract(mesh_crop=True) derive later. No
    geometry: the whole rect."""
    if geometry is None:
        return np.ones((h, w), bool)
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
    geometry: dict[str, dict | None],
    pma_names: frozenset[str] | set[str] = frozenset(),
) -> dict[str, Piece]:
    """One Piece per survivor (stack order, first = top). Every piece's
    offsets describe a canvas of the source's own size. A survivor whose
    geometry is None is cut to its rect. A survivor in `pma_names` lives on
    a pma page: an all-pma batch is cut exactly, a mixed one scales pma RGB
    by its share of the pixel's alpha (spec 2026-10-26 §3.5)."""
    width, height = raster.size(source)
    pixels = np.frombuffer(raster.get_pixels(source), dtype=np.uint8).reshape(height, width, 4)
    rects, offsets, holders = {}, {}, np.zeros((len(survivors), height, width), bool)
    for k, name in enumerate(survivors):
        x0, y0, w, h = scaled_rect(metas[name], width, height)
        rects[name] = (x0, y0, w, h)
        offsets[name] = [x0, height - (y0 + h), width, height]
        holders[k, y0:y0 + h, x0:x0 + w] = _hull(raster, geometry[name], offsets[name], w, h)
    pma = [name in pma_names for name in survivors]
    windows = [np.s_[y0:y0 + h, x0:x0 + w] for x0, y0, w, h in (rects[name] for name in survivors)]
    if survivors and all(pma):
        layers = _exact_premultiplied_layers(pixels, holders)
        crops = [layers[k][window] for k, window in enumerate(windows)]
    else:
        crops = _shared_layers(pixels, holders, pma, windows)
    pieces: dict[str, Piece] = {}
    for k, name in enumerate(survivors):
        x0, y0, w, h = rects[name]
        piece = np.ascontiguousarray(crops[k])
        # An empty piece keeps its full rect: in game the mesh maps the whole
        # canvas around the packed rect, so a smaller rect would let the hull
        # sample a neighbouring sprite.
        pieces[name] = Piece(raster.from_pixels(w, h, piece.tobytes()), offsets[name])
    return pieces


def _shared_layers(pixels: np.ndarray, holders: np.ndarray, pma: list[bool], windows: list) -> list[np.ndarray]:
    """Straight and mixed batches (spec 2026-10-26 §3.5): the SPLIT alpha
    hand-out, one layer per survivor cut to its rect window. Straight layers
    are byte-identical to before. pma layers keep RGB x share, give an
    alpha-0 pixel's colour to the topmost covering holder when it is pma,
    and are cleared outside their holder."""
    alphas = split_alphas(pixels[..., 3], holders)
    if any(pma):
        covered = holders.any(axis=0)
        top = np.argmax(holders, axis=0)
    layers = []
    for k, (is_pma, window) in enumerate(zip(pma, windows)):
        source = pixels[window]
        layer = source.copy()
        layer[..., 3] = alphas[k][window]
        if is_pma:
            # float64: rgb * alpha overflows uint8. A share is <= 1.
            total = source[..., 3].astype(np.float64)
            share = np.divide(layer[..., 3].astype(np.float64), total, out=np.zeros_like(total), where=total > 0)
            layer[..., :3] = np.rint(source[..., :3].astype(np.float64) * share[..., None]).astype(np.uint8)
            additive = covered[window] & (top[window] == k) & (total == 0)
            layer[additive, :3] = source[additive, :3]
            layer[~holders[k][window]] = 0
        else:
            layer[layer[..., 3] == 0] = 0
        layers.append(layer)
    return layers


def _exact_premultiplied_layers(pixels: np.ndarray, holders: np.ndarray) -> np.ndarray:
    """All-pma batches (spec 2026-10-26 §3.5): pieces whose premultiplied
    over, bottom first and rounded once, gives back every covered pixel
    exactly. Alpha: SPLIT hand-out top-down, except the residual holder (the
    first covering one where the chain would take everything left) takes
    rint((A - above) / T_above) and the holders below it 0. RGB: share
    scaling, except the topmost covering holder takes the residual
    rint(C - attenuate(below)), clipped to 0..255 (additive is legal).
    Every layer is (0,0,0,0) outside its holder. One pass per holder each
    way: the alpha above and its transmittance are kept as running sums."""
    count = holders.shape[0]
    big_a = pixels[..., 3].astype(np.int64)
    colour = pixels[..., :3].astype(np.float64)
    opaque = big_a == 255
    alphas = np.zeros(holders.shape, np.uint8)
    remaining = np.where(opaque, 0, big_a)
    settled = np.zeros(big_a.shape, bool)
    left_below = holders.sum(axis=0)
    above = np.zeros(big_a.shape, np.float64)       # alpha of the holders above, stacked
    transmit = np.ones(big_a.shape, np.float64)     # 1 - above / 255
    for k in range(count):
        held = holders[k]
        left_below = left_below - held
        active = held & ~opaque & ~settled
        residual = active & ((left_below == 0) | (remaining < 2))
        shared = active & ~residual
        quotient = np.divide(big_a - above, transmit, out=np.zeros_like(transmit), where=transmit > 0)
        # The clip is reachable: at A in {200, 210, 214} under 9+ shared
        # holders the chain above overshoots A by up to 0.194, the quotient
        # rounds to -1 or -2, and 0 is still exact (overshoot < 0.5).
        value = np.clip(np.rint(quotient), 0, 255)
        alphas[k] = np.where(
            held & opaque, 255,
            np.where(shared, _TOP[remaining.astype(np.uint8)], np.where(residual, value, 0)),
        )
        remaining = np.where(shared, _BOTTOM[remaining.astype(np.uint8)], np.where(residual, 0, remaining))
        settled |= residual
        taken = alphas[k].astype(np.float64)
        above = above + taken * transmit
        transmit = transmit * (1 - taken / 255)

    covered = holders.any(axis=0)
    top = np.argmax(holders, axis=0)
    total = big_a.astype(np.float64)
    layers = np.zeros((count, *pixels.shape), np.uint8)
    layers[..., 3] = alphas
    under = np.zeros((*big_a.shape, 4), np.float64)
    for k in reversed(range(count)):
        share = np.divide(alphas[k], total, out=np.zeros_like(total), where=total > 0)
        lower = holders[k] & (top != k)
        is_top = covered & (top == k)
        residual_rgb = np.clip(np.rint(colour - attenuate(under, alphas[k])[..., :3]), 0, 255)
        rgb = np.where(lower[..., None], np.rint(colour * share[..., None]), 0)
        layers[k, ..., :3] = np.where(is_top[..., None], residual_rgb, rgb)
        under = premultiplied_over(layers[k], under)
    layers[~holders] = 0
    return layers
