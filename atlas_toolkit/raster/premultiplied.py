"""Premultiplied-alpha arithmetic shared by the backend paste, the pma
preview stack and the pma composite split (spec 2026-10-26 §3.9). The one
implementation of `src + under * (1 - src_a/255)`. Float64, unrounded:
callers round once. premultiply / unpremultiply convert uint8 RGBA between
straight and premultiplied, rounding half-even (spec 2026-10-28 §3.2). Pure
numpy; raster/ imports nothing from domain/."""
from __future__ import annotations

import numpy as np


def attenuate(under: np.ndarray, src_alpha: np.ndarray) -> np.ndarray:
    """`under` (..., 4) seen through a layer whose alpha is `src_alpha` (...)."""
    return np.asarray(under, np.float64) * (1 - np.asarray(src_alpha, np.float64) / 255)[..., None]


def premultiplied_over(src: np.ndarray, under: np.ndarray) -> np.ndarray:
    """Porter-Duff source-over on premultiplied RGBA, all four channels."""
    src = np.asarray(src, np.float64)
    return src + attenuate(under, src[..., 3])


def premultiply(rgba: np.ndarray) -> np.ndarray:
    """Straight uint8 RGBA (..., 4) to premultiplied: RGB = rint(c * a / 255)."""
    rgba = np.asarray(rgba, np.uint8)
    alpha = rgba[..., 3:].astype(np.float64)
    out = rgba.copy()
    out[..., :3] = np.clip(np.rint(rgba[..., :3].astype(np.float64) * alpha / 255), 0, 255).astype(np.uint8)
    return out


def unpremultiply(rgba: np.ndarray) -> np.ndarray:
    """Premultiplied uint8 RGBA (..., 4) to straight: RGB = rint(c * 255 / a),
    clipped to 255 (additive pixels lose their excess); alpha 0 becomes
    (0, 0, 0, 0). premultiply(unpremultiply(p)) == p whenever RGB <= a."""
    rgba = np.asarray(rgba, np.uint8)
    alpha = rgba[..., 3:].astype(np.float64)
    rgb = np.rint(rgba[..., :3].astype(np.float64) * 255 / np.maximum(alpha, 1))
    out = rgba.copy()
    out[..., :3] = np.where(alpha > 0, np.clip(rgb, 0, 255), 0).astype(np.uint8)
    return out
