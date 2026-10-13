# tests/python/test_raster_mesh_mask.py
import json
from pathlib import Path

import pytest

from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

FIXTURES = Path(__file__).parent / "fixtures"
ORACLE_PATH = FIXTURES / "mesh_mask_js_oracle.json"
SAMPLE_PATH = Path(__file__).parent.parent / "fixtures" / "mesh-sample.json"

_needs_oracle = pytest.mark.skipif(
    not ORACLE_PATH.exists(),
    reason="mesh_mask_js_oracle.json not generated -- run tests/python/gen_js_reference_mesh_mask.mjs",
)


def _load_oracle():
    return json.loads(ORACLE_PATH.read_text())


def _alpha_flat(backend, handle):
    w, h = backend.size(handle)
    img = backend._images[handle]
    alpha = img.getchannel("A")
    return w, h, list(alpha.tobytes())


@_needs_oracle
def test_mesh_sample_matches_js_oracle_closely():
    # Hard-edge rasterizer vs antialiased Canvas fill -- exact byte parity
    # is not the contract (base.py's docstring says so explicitly). 97%
    # floors well below the PoC's own measured 99.70% on this exact case,
    # leaving headroom for minor implementation differences while still
    # failing hard on a wrong-shape or wrong-axis rasterizer.
    sample = json.loads(SAMPLE_PATH.read_text())["SAMPLE_1"]
    oracle = _load_oracle()["mesh_sample"]
    backend = PillowRasterBackend()
    handle = backend.rasterize_mesh_mask(sample["uvs"], sample["triangles"], 200, 200)
    w, h, alpha = _alpha_flat(backend, handle)
    assert (w, h) == (oracle["w"], oracle["h"])
    agree = sum(1 for a, b in zip(alpha, oracle["alpha"]) if (a > 0) == (b > 0))
    ratio = agree / len(alpha)
    assert ratio >= 0.97, f"only {ratio:.4%} pixel agreement with JS oracle"
    # Interior sanity check independent of the aggregate ratio: the mesh's
    # fan centre (uv 0.47, 0.2 -> px 94, py 40) must be opaque on both sides.
    interior_i = (40 * 200) + 94
    assert alpha[interior_i] > 0
    assert oracle["alpha"][interior_i] > 0


@_needs_oracle
def test_single_triangle_matches_js_oracle():
    oracle = _load_oracle()["single_triangle"]
    backend = PillowRasterBackend()
    handle = backend.rasterize_mesh_mask([0, 0, 1, 0, 0, 1], [0, 1, 2], 50, 50)
    w, h, alpha = _alpha_flat(backend, handle)
    assert (w, h) == (oracle["w"], oracle["h"])
    agree = sum(1 for a, b in zip(alpha, oracle["alpha"]) if (a > 0) == (b > 0))
    assert agree / len(alpha) >= 0.97


def test_winding_cancellation_produces_zero_opaque_pixels():
    # EXACT assertion, not an aggregate-agreement one -- this is the
    # winding-cancellation-hole bug class this project has hit before.
    # Two triangles with identical geometry but opposite index order must
    # fully cancel under nonzero-winding fill: the interior pixels sit far
    # from any edge, so antialiasing cannot explain a nonzero count here.
    backend = PillowRasterBackend()
    handle = backend.rasterize_mesh_mask(
        [0, 0, 1, 0, 0, 1], [0, 1, 2, 2, 1, 0], 50, 50,
    )
    _, _, alpha = _alpha_flat(backend, handle)
    assert sum(1 for a in alpha if a > 0) == 0


@_needs_oracle
def test_winding_cancellation_matches_js_oracle_exactly():
    oracle = _load_oracle()["winding_cancel"]
    assert sum(1 for a in oracle["alpha"] if a > 0) == 0, (
        "oracle fixture itself does not show cancellation -- regenerate or fix the generator"
    )


@_needs_oracle
def test_non_square_axis_check_matches_js_oracle():
    # Width != height with an asymmetric triangle -- would fail if width
    # and height (or u and v) were ever swapped internally, unlike a
    # square fixture which cannot distinguish the two axes.
    oracle = _load_oracle()["non_square_axis_check"]
    backend = PillowRasterBackend()
    handle = backend.rasterize_mesh_mask(
        [0.1, 0.6, 0.3, 0.6, 0.1, 0.9], [0, 1, 2], 300, 100,
    )
    w, h, alpha = _alpha_flat(backend, handle)
    assert (w, h) == (oracle["w"], oracle["h"]) == (300, 100)
    agree = sum(1 for a, b in zip(alpha, oracle["alpha"]) if (a > 0) == (b > 0))
    assert agree / len(alpha) >= 0.97


def test_no_triangles_produces_fully_transparent_mask():
    backend = PillowRasterBackend()
    handle = backend.rasterize_mesh_mask([], [], 10, 10)
    _, _, alpha = _alpha_flat(backend, handle)
    assert sum(alpha) == 0


def test_result_is_rgba_opaque_white_where_filled():
    backend = PillowRasterBackend()
    handle = backend.rasterize_mesh_mask([0, 0, 1, 0, 0, 1], [0, 1, 2], 10, 10)
    img = backend._images[handle]
    assert img.mode == "RGBA"
    # Pixel (2,2) sits inside the triangle spanning u,v in [0,1] on a 10x10
    # canvas -- must be opaque white, not just nonzero alpha.
    assert img.getpixel((2, 2)) == (255, 255, 255, 255)


def _reference_alpha(uvs, triangles, width, height):
    """The original full-canvas edge loop, kept as the oracle for the banded
    rasterizer: every edge tested against every pixel centre."""
    import numpy as np

    winding = np.zeros((height, width), dtype=np.int32)
    cy = (np.arange(height, dtype=np.float64) + 0.5)[:, np.newaxis]
    cx = (np.arange(width, dtype=np.float64) + 0.5)[np.newaxis, :]
    for i in range(0, len(triangles), 3):
        ia, ib, ic = triangles[i] * 2, triangles[i + 1] * 2, triangles[i + 2] * 2
        pts = [(uvs[ia] * width, uvs[ia + 1] * height), (uvs[ib] * width, uvs[ib + 1] * height),
               (uvs[ic] * width, uvs[ic + 1] * height)]
        for k in range(3):
            x0, y0 = pts[k]
            x1, y1 = pts[(k + 1) % 3]
            if y0 == y1:
                continue
            direction = 1 if y1 > y0 else -1
            if y0 > y1:
                x0, y0, x1, y1 = x1, y1, x0, y0
            crosses = (cy >= y0) & (cy < y1)
            cross_x = x0 + (cy - y0) / (y1 - y0) * (x1 - x0)
            winding += np.where(crosses & (cross_x > cx), direction, 0).astype(np.int32)
    return np.where(winding != 0, 255, 0).astype(np.uint8)


@pytest.mark.parametrize("seed", range(12))
def test_banded_rasterizer_matches_the_full_canvas_reference(seed):
    import numpy as np

    rng = np.random.default_rng(seed)
    width, height = [(37, 23), (64, 64), (101, 7), (5, 90)][seed % 4]
    n = 3 * int(rng.integers(1, 9))
    if seed % 3 == 0:
        # Vertices exactly on pixel edges and pixel centres, where a band
        # computed in floats is most likely to be off by one row.
        uvs = [float(v) for v in (rng.integers(-4, 2 * max(width, height) + 4, 2 * n) / (2.0 * np.array([width, height] * n)))]
    else:
        uvs = [float(v) for v in rng.uniform(-0.3, 1.3, 2 * n)]  # partly off-canvas
    triangles = [int(v) for v in rng.permutation(n)]
    if seed % 4 == 1:
        triangles += triangles[::-1]  # reversed twins: winding cancellation
    backend = PillowRasterBackend()
    mask = backend.rasterize_mesh_mask(uvs, triangles, width, height)
    got = np.asarray(backend.export(mask))[..., 3]
    assert np.array_equal(got, _reference_alpha(uvs, triangles, width, height))
