"""Premultiplied-alpha arithmetic shared by the backend paste, the pma
preview stack and the pma composite split (spec 2026-10-26 §3.9). The one
implementation of `src + under * (1 - src_a/255)`. Float64, unrounded:
callers round once. Pure numpy; raster/ imports nothing from domain/."""
from __future__ import annotations

import numpy as np


def attenuate(under: np.ndarray, src_alpha: np.ndarray) -> np.ndarray:
    """`under` (..., 4) seen through a layer whose alpha is `src_alpha` (...)."""
    return np.asarray(under, np.float64) * (1 - np.asarray(src_alpha, np.float64) / 255)[..., None]


def premultiplied_over(src: np.ndarray, under: np.ndarray) -> np.ndarray:
    """Porter-Duff source-over on premultiplied RGBA, all four channels."""
    src = np.asarray(src, np.float64)
    return src + attenuate(under, src[..., 3])
