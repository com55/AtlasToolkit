import pytest

from atlas_toolkit.raster.pillow_backend import PillowRasterBackend


def test_composite_destination_in_raises_not_implemented():
    backend = PillowRasterBackend()
    dest = backend.create(2, 2)
    mask = backend.create(2, 2)
    with pytest.raises(NotImplementedError, match="mesh"):
        backend.composite_destination_in(dest, mask)
