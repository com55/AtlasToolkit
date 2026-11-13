# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Spine's texture packer "Bleed": give transparent pixels next to an image
the image's colour, so linear texture filtering at a region's edge does not
blend in the black of (0, 0, 0, 0). For straight-alpha pages only.

Same rule as libGDX's ColorBleedEffect, which Spine's packer builds on:
each pass sets the RGB of every alpha-0 pixel that has a non-blank pixel
among its 8 neighbours to the unweighted integer mean of those neighbours'
RGB; alpha stays 0. Pixels set in a pass become sources only in the next
pass, so `iterations` passes reach that many pixels out."""
from __future__ import annotations

import numpy as np

BLEED_ITERATIONS = 2  # Spine's bleedIterations default

_NEIGHBOURS = [(dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dy, dx) != (0, 0)]


def color_bleed(rgba: np.ndarray, iterations: int = BLEED_ITERATIONS) -> np.ndarray:
    """A bled copy of an (h, w, 4) uint8 straight-alpha image."""
    out = rgba.copy()
    height, width = out.shape[:2]
    known = out[..., 3] > 0
    rgb = out[..., :3].astype(np.int32)
    for _ in range(max(0, iterations)):
        pending = ~known
        if not pending.any():
            break
        padded_known = np.pad(known, 1)
        padded_rgb = np.pad(rgb * known[..., None], ((1, 1), (1, 1), (0, 0)))
        total = np.zeros((height, width, 3), np.int32)
        count = np.zeros((height, width), np.int32)
        for dy, dx in _NEIGHBOURS:
            rows, cols = slice(1 + dy, 1 + dy + height), slice(1 + dx, 1 + dx + width)
            total += padded_rgb[rows, cols]
            count += padded_known[rows, cols]
        newly = pending & (count > 0)
        if not newly.any():
            break
        rgb[newly] = total[newly] // count[newly][:, None]
        known = known | newly
    out[..., :3] = rgb.astype(np.uint8)
    return out
