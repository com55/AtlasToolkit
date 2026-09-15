import pytest

from atlas_toolkit.raster.pillow_backend import PillowRasterBackend


def test_rasterize_mesh_mask_raises_not_implemented():
    backend = PillowRasterBackend()
    with pytest.raises(NotImplementedError, match="mesh"):
        backend.rasterize_mesh_mask([0.0, 0.0, 1.0, 0.0, 0.0, 1.0], [0, 1, 2], 10, 10)


def test_composite_destination_in_raises_not_implemented():
    backend = PillowRasterBackend()
    dest = backend.create(2, 2)
    mask = backend.create(2, 2)
    with pytest.raises(NotImplementedError, match="mesh"):
        backend.composite_destination_in(dest, mask)
