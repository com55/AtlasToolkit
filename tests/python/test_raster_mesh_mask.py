# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
# tests/python/test_raster_mesh_mask.py

import pytest

from spine_atlas_toolkit.raster.pillow_backend import PillowRasterBackend

def _alpha_flat(backend, handle):
    w, h = backend.size(handle)
    img = backend._images[handle]
    alpha = img.getchannel("A")
    return w, h, list(alpha.tobytes())


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


def _banded_alpha(uvs, triangles, width, height):
    """The per-edge banded rasterizer that shipped until 2026-11-20, frozen
    as the oracle for the vectorized one (same pixels, same errors)."""
    import math

    import numpy as np

    edges = []
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
            edges.append((x0, y0, x1, y1, direction))
    winding = np.zeros((height, width), dtype=np.int32)
    if edges:
        cy = (np.arange(height, dtype=np.float64) + 0.5)[:, np.newaxis]
        cx = (np.arange(width, dtype=np.float64) + 0.5)[np.newaxis, :]
        for x0, y0, x1, y1, direction in edges:
            r0 = max(0, math.floor(y0 - 0.5))
            r1 = min(height, math.ceil(y1 - 0.5) + 1)
            if r0 >= r1:
                continue
            band = cy[r0:r1]
            crosses = (band >= y0) & (band < y1)
            t = (band - y0) / (y1 - y0)
            cross_x = x0 + t * (x1 - x0)
            winding[r0:r1] += np.where(crosses & (cross_x > cx), direction, 0).astype(np.int32)
    return np.where(winding != 0, 255, 0).astype(np.uint8)


def _corpus():
    """Synthetic meshes: random, pixel-centre and pixel-edge vertices,
    reversed twins, off-canvas, degenerate sizes and huge coordinates."""
    import numpy as np

    cases = []
    sizes = [(0, 0), (0, 7), (7, 0), (1, 1), (1, 9), (9, 1), (37, 23), (64, 64), (101, 7), (5, 90), (200, 150)]
    for seed in range(60):
        rng = np.random.default_rng(1000 + seed)
        width, height = sizes[seed % len(sizes)]
        n = 3 * int(rng.integers(1, 12))
        style = seed % 5
        if style == 0:
            scale = 2.0 * np.array([max(width, 1), max(height, 1)] * n)
            uvs = rng.integers(-4, 2 * max(width, height, 1) + 4, 2 * n) / scale
        elif style == 1:
            uvs = rng.uniform(-0.3, 1.3, 2 * n)
        elif style == 2:
            uvs = rng.uniform(0.2, 0.8, 2 * n)  # all inside: dense overlaps
        elif style == 3:
            uvs = rng.choice([-1e300, -2.0, 0.0, 0.25, 0.5, 1.0, 3.0, 1e300], 2 * n)
        else:
            grid = rng.integers(0, 5, 2 * n) / 4.0  # shared vertices and edges
            uvs = grid
        triangles = [int(v) for v in rng.permutation(n)]
        if seed % 4 == 1:
            triangles += triangles[::-1]  # reversed twins: winding cancellation
        cases.append((f"seed{seed}", [float(v) for v in uvs], triangles, width, height))
    tri = [0, 1, 2]
    for name, uvs in [
        ("x_nan", [float("nan"), 0.1, 0.9, 0.2, 0.5, 0.9]),
        ("x_inf", [float("inf"), 0.1, 0.9, 0.2, 0.5, 0.9]),
        ("x_-inf", [float("-inf"), 0.1, 0.9, 0.2, 0.5, 0.9]),
        ("x_inf_twice", [float("inf"), 0.1, float("inf"), 0.9, 0.5, 0.5]),
        ("y_huge", [0.1, -1e300, 0.9, 1e300, 0.5, 0.5]),
        ("y_all_inf", [0.1, float("inf"), 0.9, float("inf"), 0.5, float("inf")]),  # all horizontal: empty
    ]:
        cases.append((name, uvs, tri, 20, 20))
    cases.append(("odd_uvs", [0.1, 0.1, 0.9, 0.2, 0.5, 0.9, 0.3], tri, 16, 16))
    cases.append(("negative_index", [0.1, 0.1, 0.9, 0.2, 0.5, 0.9], [0, 1, -1], 16, 16))
    cases.append(("no_triangles", [0.1, 0.2], [], 8, 8))
    return cases


def _outcome(fn, *args):
    try:
        return "ok", fn(*args)
    except Exception as error:  # noqa: BLE001 - the error type is the result
        return "error", type(error)


@pytest.mark.parametrize("case", _corpus(), ids=lambda case: case[0])
@pytest.mark.parametrize("path", ["chosen", "runs", "dense"])
def test_vectorized_rasterizer_matches_the_banded_oracle(case, path):
    """Both writers, forced, and the size-based choice give the oracle's
    bytes on every synthetic mesh."""
    import numpy as np

    from spine_atlas_toolkit.raster import pillow_backend as pb

    _, uvs, triangles, width, height = case

    def run(*args):
        if path == "chosen":
            return pb.PillowRasterBackend()._rasterize_alpha(*args)
        writer = pb.alpha_from_runs if path == "runs" else pb.alpha_from_dense
        return writer(*pb.mesh_crossings(*args), args[2], args[3])

    want = _outcome(_banded_alpha, uvs, triangles, width, height)
    got = _outcome(run, uvs, triangles, width, height)
    assert got[0] == want[0]
    if want[0] == "ok":
        assert got[1].dtype == np.uint8 and got[1].shape == (height, width)
        assert np.array_equal(got[1], want[1])


@pytest.mark.parametrize("name,uvs,error", [
    ("y_nan", [0.1, float("nan"), 0.9, 0.2, 0.5, 0.9], ValueError),
    ("y_inf", [0.1, float("inf"), 0.9, 0.2, 0.5, 0.9], OverflowError),
    ("y_-inf", [0.1, float("-inf"), 0.9, 0.2, 0.5, 0.9], OverflowError),
    ("y_nan_before_inf", [0.1, float("nan"), 0.9, 0.2, 0.5, float("inf")], ValueError),
    ("y_inf_before_nan", [0.1, float("inf"), 0.9, 0.2, 0.5, float("nan")], OverflowError),
    ("y_finite_then_nan", [0.1, 0.2, 0.9, float("nan"), 0.5, 0.9], ValueError),
    ("x_index", [0.1, 0.1, 0.9, 0.2], IndexError),
])
def test_bad_geometry_raises_what_the_banded_loop_raised(name, uvs, error):
    triangles = [0, 1, 2]
    with pytest.raises(error):
        _banded_alpha(uvs, triangles, 20, 20)
    with pytest.raises(error):
        PillowRasterBackend()._rasterize_alpha(uvs, triangles, 20, 20)


def test_triangle_list_not_a_multiple_of_three_raises_index_error():
    with pytest.raises(IndexError):
        _banded_alpha([0.1, 0.1, 0.9, 0.2, 0.5, 0.9], [0, 1, 2, 0], 8, 8)
    with pytest.raises(IndexError):
        PillowRasterBackend()._rasterize_alpha([0.1, 0.1, 0.9, 0.2, 0.5, 0.9], [0, 1, 2, 0], 8, 8)


def test_crossings_use_int64_so_wasm32_computes_the_same_keys():
    """Pyodide's intp is 32-bit; positions on uncapped original sizes must
    not depend on it."""
    import numpy as np

    from spine_atlas_toolkit.raster.pillow_backend import mesh_crossings

    row, k, d = mesh_crossings([0, 0, 1, 0, 0, 1], [0, 1, 2], 50, 40)
    assert row.dtype == k.dtype == d.dtype == np.int64


def test_a_large_sparse_mask_takes_the_runs_writer(monkeypatch):
    from spine_atlas_toolkit.raster import pillow_backend as pb

    calls = []
    monkeypatch.setattr(pb, "alpha_from_runs", lambda *a: calls.append("runs") or pb.alpha_from_dense(*a))
    pb.PillowRasterBackend()._rasterize_alpha([0.1, 0.1, 0.9, 0.2, 0.5, 0.9], [0, 1, 2], 400, 300)
    assert calls == ["runs"]


def test_mask_image_is_white_rgb_with_the_rasterized_alpha():
    import numpy as np

    backend = PillowRasterBackend()
    uvs, triangles = [0.1, 0.1, 0.9, 0.2, 0.5, 0.9], [0, 1, 2]
    rgba = np.asarray(backend.export(backend.rasterize_mesh_mask(uvs, triangles, 30, 20)))
    assert np.array_equal(rgba[..., 3], _banded_alpha(uvs, triangles, 30, 20))
    assert (rgba[..., :3] == 255).all()
    hit = backend.export(backend.rasterize_mesh_mask(uvs, triangles, 30, 20))  # cache hit
    assert np.array_equal(np.asarray(hit), rgba)
