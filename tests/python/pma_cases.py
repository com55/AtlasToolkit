"""Shared fixtures for the pma tests (spec 2026-10-26-pma-mesh-aware).
Not a test module: import it as tests.python.pma_cases."""
from __future__ import annotations

import numpy as np

import atlas_toolkit.api as api
from atlas_toolkit.packer import PackerOptions

QUAD = [0, 1, 2, 0, 2, 3]
GLOW_IN = (90, 60, 30, 0)     # additive alpha-0 pixel inside a hull
GLOW_OUT = (70, 50, 20, 0)    # additive alpha-0 pixel outside a hull
PACKERS = [
    ("shelf", PackerOptions(mesh_aware=True)),
    ("rectangles", PackerOptions(mesh_aware=True)),
    ("polygons", PackerOptions(padding=2)),
]


def mesh(u0: float, v0: float, u1: float, v1: float) -> dict:
    """Axis-aligned rectangle mesh in UV space (edges on whole pixels when
    the fixture sizes are chosen for it)."""
    return {"type": "Mesh", "uvs": [u0, v0, u1, v0, u1, v1, u0, v1], "triangles": QUAD, "hull_length": 4}


def premultiplied_pattern(width: int, height: int, seed: int) -> np.ndarray:
    """Valid premultiplied RGBA (RGB <= A): every pixel differs by position
    and seed, alphas cycle through opaque and partial values."""
    ys, xs = np.mgrid[0:height, 0:width]
    alpha = np.array([255, 200, 128, 64, 255, 30], np.uint8)[(xs + 3 * ys + seed) % 6]
    arr = np.zeros((height, width, 4), np.uint8)
    arr[..., 0] = (xs * 13 + seed * 7) % 256
    arr[..., 1] = (ys * 29 + seed * 3) % 256
    arr[..., 2] = (xs * 5 + ys * 11 + seed) % 256
    arr[..., :3] = np.minimum(arr[..., :3], alpha[..., None])
    arr[..., 3] = alpha
    return arr


def with_glow(arr: np.ndarray, x: int, y: int, colour: tuple[int, int, int, int]) -> np.ndarray:
    out = arr.copy()
    out[y, x] = colour
    return out


def fake_skeleton(monkeypatch, meshes: dict) -> None:
    """SpineAtlas.load(..., skeleton=b"x") then sees `meshes` as attachments."""
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": meshes})
