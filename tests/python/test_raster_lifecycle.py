import io

import pytest
from PIL import Image

from atlas_toolkit.raster.pillow_backend import PillowRasterBackend


def _png_bytes(mode: str, size: tuple[int, int], color) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_decode_png_normalizes_rgb_source_to_rgba():
    backend = PillowRasterBackend()
    handle = backend.decode_png(_png_bytes("RGB", (3, 2), (10, 20, 30)))
    decoded = backend._images[handle]
    assert decoded.mode == "RGBA"
    assert decoded.getpixel((0, 0)) == (10, 20, 30, 255)


def test_decode_png_normalizes_palette_source_to_rgba():
    backend = PillowRasterBackend()
    pal_bytes = _png_bytes("P", (2, 2), 0)
    handle = backend.decode_png(pal_bytes)
    assert backend._images[handle].mode == "RGBA"


def test_decode_encode_round_trips_pixels():
    backend = PillowRasterBackend()
    original = _png_bytes("RGBA", (4, 4), (1, 2, 3, 200))
    handle = backend.decode_png(original)
    reencoded = backend.encode_png(handle)
    roundtripped = Image.open(io.BytesIO(reencoded)).convert("RGBA")
    assert roundtripped.getpixel((0, 0)) == (1, 2, 3, 200)


def test_create_fills_transparent():
    backend = PillowRasterBackend()
    handle = backend.create(5, 3)
    img = backend._images[handle]
    assert img.mode == "RGBA"
    assert img.size == (5, 3)
    assert img.getpixel((0, 0)) == (0, 0, 0, 0)
    assert img.getpixel((4, 2)) == (0, 0, 0, 0)


def test_size_matches_created_dimensions():
    backend = PillowRasterBackend()
    handle = backend.create(7, 11)
    assert backend.size(handle) == (7, 11)


def test_clone_is_independent_copy():
    backend = PillowRasterBackend()
    original = backend.decode_png(_png_bytes("RGBA", (2, 2), (9, 9, 9, 255)))
    cloned = backend.clone(original)
    assert cloned != original
    backend._images[cloned].putpixel((0, 0), (255, 0, 0, 255))
    assert backend._images[original].getpixel((0, 0)) == (9, 9, 9, 255)
    assert backend._images[cloned].getpixel((0, 0)) == (255, 0, 0, 255)


def test_dispose_removes_handle():
    backend = PillowRasterBackend()
    handle = backend.create(1, 1)
    backend.dispose(handle)
    with pytest.raises(KeyError):
        backend.size(handle)
