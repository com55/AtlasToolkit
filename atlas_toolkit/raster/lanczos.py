"""Separable Lanczos-3 resize for straight RGBA (lanczos_resize_rgba, port of
www/js/lanczos-resize.js's CPU path lanczosResizeRGBA) and for premultiplied
RGBA (lanczos_resize_premultiplied, spec 2026-10-26 §3.1). Used by Forced
Resizing in the View Mode preview composite.

Parity target is the CPU function, not resizeCanvasLanczos: in a browser
that one prefers a WebGL2 shader whose float/texture precision differs.
The numerics follow the JS step for step so the output is byte-identical:
weights and the horizontal-pass rows are stored as float32 (JS
Float32Array), everything else is float64, taps are summed one at a time
in kernel order, and the final rounding is Math.round (half up), not
Python's half-to-even. JS skips work outside the opaque bounding box;
those pixels come out transparent either way, so this computes them all.
"""
from __future__ import annotations

import math

import numpy as np

LANCZOS_A = 3


def _lanczos(x: float) -> float:
    x = abs(x)
    if x == 0:
        return 1.0
    if x >= LANCZOS_A:
        return 0.0
    pix = math.pi * x
    return (math.sin(pix) / pix) * (math.sin(pix / LANCZOS_A) / (pix / LANCZOS_A))


def _build_kernels(src_size: int, dst_size: int) -> tuple[np.ndarray, np.ndarray]:
    """Per dest pixel: clamped source indexes and normalized float32 weights,
    padded to a rectangle with zero weights (adding +0.0 is exact)."""
    scale = dst_size / src_size
    filter_scale = scale if scale < 1 else 1
    support = LANCZOS_A / filter_scale
    rows: list[tuple[list[int], list[float]]] = []
    for d in range(dst_size):
        center = (d + 0.5) / scale - 0.5
        acc_i: list[int] = []
        acc_w: list[float] = []
        for i in range(math.floor(center - support), math.ceil(center + support) + 1):
            w = _lanczos((i - center) * filter_scale)
            if w == 0:
                continue
            idx = 0 if i < 0 else src_size - 1 if i >= src_size else i
            if acc_i and acc_i[-1] == idx:
                acc_w[-1] += w
            else:
                acc_i.append(idx)
                acc_w.append(w)
        total = 0.0
        for w in acc_w:
            total += w
        inv = 1 / total if total != 0 else 0
        rows.append((acc_i, [w * inv for w in acc_w]))
    taps = max(len(i) for i, _ in rows)
    index = np.zeros((dst_size, taps), dtype=np.intp)
    weight = np.zeros((dst_size, taps), dtype=np.float32)
    for d, (acc_i, acc_w) in enumerate(rows):
        index[d, :len(acc_i)] = acc_i
        weight[d, :len(acc_w)] = acc_w
    return index, weight


def _round_half_up_clamped(v: np.ndarray) -> np.ndarray:
    """clamp8: <=0 -> 0, >=255 -> 255, else Math.round (ties toward +inf)."""
    f = np.floor(v)
    rounded = np.where(v - f >= 0.5, f + 1, f)
    return np.clip(rounded, 0, 255).astype(np.uint8)


def _filter(premultiplied: np.ndarray, src_w: int, src_h: int, dst_w: int, dst_h: int) -> np.ndarray:
    """Both separable passes over float64 premultiplied RGBA; returns the
    unrounded float64 (dst_h, dst_w, 4) result. Rows stay float32 between
    the passes (JS Float32Array)."""
    x_index, x_weight = _build_kernels(src_w, dst_w)
    horizontal = np.zeros((src_h, dst_w, 4), dtype=np.float64)
    for k in range(x_index.shape[1]):
        horizontal += premultiplied[:, x_index[:, k], :] * x_weight[:, k].astype(np.float64)[None, :, None]
    rows = horizontal.astype(np.float32)

    y_index, y_weight = _build_kernels(src_h, dst_h)
    vertical = np.zeros((dst_h, dst_w, 4), dtype=np.float64)
    for k in range(y_index.shape[1]):
        vertical += rows[y_index[:, k]].astype(np.float64) * y_weight[:, k].astype(np.float64)[:, None, None]
    return vertical


def lanczos_resize_rgba(src: np.ndarray, dst_w: int, dst_h: int) -> np.ndarray:
    """`src` is a (height, width, 4) uint8 straight-RGBA array; returns a new
    (dst_h, dst_w, 4) uint8 array."""
    src_h, src_w = src.shape[:2]
    if src_w == dst_w and src_h == dst_h:
        return src.copy()
    if src_w == 0 or src_h == 0:
        # Nothing to sample (a zero-size region): the scaled frame is empty.
        return np.zeros((dst_h, dst_w, 4), dtype=np.uint8)
    alpha = src[..., 3].astype(np.float64)
    premultiplied = np.empty(src.shape, dtype=np.float64)
    premultiplied[..., :3] = src[..., :3] * (alpha / 255)[..., None]
    premultiplied[..., 3] = alpha

    vertical = _filter(premultiplied, src_w, src_h, dst_w, dst_h)

    out = np.zeros((dst_h, dst_w, 4), dtype=np.uint8)
    a = vertical[..., 3]
    keep = a > 0.5
    inv = 255 / a[keep]
    out[keep, :3] = _round_half_up_clamped(vertical[keep, :3] * inv[:, None])
    out[keep, 3] = _round_half_up_clamped(a[keep])
    return out


def lanczos_resize_premultiplied(src: np.ndarray, dst_w: int, dst_h: int) -> np.ndarray:
    """`src` is (height, width, 4) uint8 PREMULTIPLIED RGBA (a pma page);
    returns premultiplied (dst_h, dst_w, 4) uint8. Same kernels and passes
    as lanczos_resize_rgba, but no premultiply / un-premultiply and no
    a > 0.5 keep-mask, so additive RGB on alpha-0 pixels survives. Where
    the filtered alpha overshoots 255 all four channels are scaled by
    255/a (spec 2026-10-26 §3.1, option b) -- the straight path's RGB/alpha
    ratio there, without over-bright edges. Elsewhere (a <= 255, a < 0
    included) channels clamp independently: negative-lobe ringing may leave
    small RGB on alpha-0 pixels next to edges (accepted, force_resize only)."""
    src_h, src_w = src.shape[:2]
    if src_w == dst_w and src_h == dst_h:
        return src.copy()
    if src_w == 0 or src_h == 0:
        return np.zeros((dst_h, dst_w, 4), dtype=np.uint8)
    vertical = _filter(src.astype(np.float64), src_w, src_h, dst_w, dst_h)
    alpha = vertical[..., 3]
    over = alpha > 255
    vertical[over] *= (255 / alpha[over])[:, None]
    return _round_half_up_clamped(vertical)
