"""PillowRasterBackend's mesh-mask cache: geometry-keyed, budgeted, and a
hit is indistinguishable from a miss."""
import numpy as np
import pytest

from atlas_toolkit.raster import pillow_backend
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend, _mesh_key

UVS = [0.1, 0.0, 0.9, 0.2, 0.5, 1.0, 0.0, 0.8]
TRIS = [0, 1, 2, 0, 2, 3]


@pytest.fixture
def counted(monkeypatch):
    calls = []
    real = PillowRasterBackend._rasterize_alpha
    monkeypatch.setattr(
        PillowRasterBackend, "_rasterize_alpha",
        lambda self, *args: calls.append(args) or real(self, *args),
    )
    return calls


def test_hit_is_pixel_identical_and_a_fresh_image(counted):
    raster = PillowRasterBackend()
    first = raster.rasterize_mesh_mask(UVS, TRIS, 23, 17)
    second = raster.rasterize_mesh_mask(list(UVS), list(TRIS), 23, 17)
    assert len(counted) == 1
    assert second != first
    assert raster._images[second] is not raster._images[first]
    assert raster.get_pixels(second) == raster.get_pixels(first)
    alpha = np.frombuffer(raster.get_pixels(first), dtype=np.uint8).reshape(17, 23, 4)[:, :, 3]
    assert (alpha == 0).any() and (alpha == 255).any()


def test_hit_matches_an_uncached_backend():
    cached = PillowRasterBackend()
    cached.rasterize_mesh_mask(UVS, TRIS, 23, 17)
    hit = cached.rasterize_mesh_mask(UVS, TRIS, 23, 17)
    fresh = PillowRasterBackend()
    assert cached.get_pixels(hit) == fresh.get_pixels(fresh.rasterize_mesh_mask(UVS, TRIS, 23, 17))


@pytest.mark.parametrize("change", ["width", "height", "uv", "triangle_order"])
def test_any_geometry_change_is_a_miss(counted, change):
    raster = PillowRasterBackend()
    raster.rasterize_mesh_mask(UVS, TRIS, 23, 17)
    uvs, tris, width, height = list(UVS), list(TRIS), 23, 17
    if change == "width":
        width = 24
    elif change == "height":
        height = 18
    elif change == "uv":
        uvs[3] = 0.25
    else:
        tris = [0, 2, 1, 0, 2, 3]
    raster.rasterize_mesh_mask(uvs, tris, width, height)
    assert len(counted) == 2


def test_key_separates_uvs_from_triangles():
    """Same concatenated bytes, different split: a float64 built from the
    first two int32 triangle indices moves from triangles into uvs."""
    moved = np.frombuffer(np.array(TRIS[:2], dtype=np.int32).tobytes(), dtype=np.float64)[0]
    assert _mesh_key(UVS, TRIS, 23, 17) != _mesh_key(UVS + [float(moved)], TRIS[2:], 23, 17)


def test_entries_over_the_budget_are_not_stored(counted, monkeypatch):
    monkeypatch.setattr(pillow_backend, "_MESH_CACHE_MAX_BYTES", 23 * 17)
    raster = PillowRasterBackend()
    raster.rasterize_mesh_mask(UVS, TRIS, 23, 17)  # 391 bytes: exactly fits
    raster.rasterize_mesh_mask(UVS, TRIS, 30, 30)  # 900 more: over budget
    raster.rasterize_mesh_mask(UVS, TRIS, 30, 30)
    raster.rasterize_mesh_mask(UVS, TRIS, 23, 17)
    assert [args[2:] for args in counted] == [(23, 17), (30, 30), (30, 30)]


def test_clear_mesh_cache_forgets_entries(counted):
    raster = PillowRasterBackend()
    raster.rasterize_mesh_mask(UVS, TRIS, 23, 17)
    raster.clear_mesh_cache()
    raster.rasterize_mesh_mask(UVS, TRIS, 23, 17)
    assert len(counted) == 2


def test_cache_is_per_backend(counted):
    PillowRasterBackend().rasterize_mesh_mask(UVS, TRIS, 23, 17)
    PillowRasterBackend().rasterize_mesh_mask(UVS, TRIS, 23, 17)
    assert len(counted) == 2


def test_transposed_size_is_a_miss(counted):
    """Same byte count, other shape: sharing a key would reshape the cached
    alpha into a scrambled mask without any error."""
    raster = PillowRasterBackend()
    raster.rasterize_mesh_mask(UVS, TRIS, 23, 17)
    swapped = raster.rasterize_mesh_mask(UVS, TRIS, 17, 23)
    assert len(counted) == 2
    fresh = PillowRasterBackend()
    assert raster.get_pixels(swapped) == fresh.get_pixels(fresh.rasterize_mesh_mask(UVS, TRIS, 17, 23))
