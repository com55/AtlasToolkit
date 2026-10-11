"""adopt/export: in-memory images in and out of the raster backend, always copied."""
from PIL import Image

from atlas_toolkit.engine._images import _SessionRaster
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend


def patterned(width=3, height=2, mode="RGBA"):
    img = Image.frombytes(
        "RGBA", (width, height),
        bytes(v for y in range(height) for x in range(width) for v in (x * 40, y * 90, 7, 200)),
    )
    return img.convert(mode) if mode != "RGBA" else img


def test_adopt_copies_and_converts_to_rgba():
    raster = PillowRasterBackend()
    source = patterned(mode="RGB")
    handle = raster.adopt(source)
    assert raster.size(handle) == (3, 2)
    stored = raster.export(handle)
    assert stored.mode == "RGBA"
    assert stored.getpixel((2, 1)) == (80, 90, 7, 255)
    source.putpixel((0, 0), (1, 2, 3))
    assert raster.export(handle).getpixel((0, 0)) == (0, 0, 7, 255)


def test_adopt_of_rgba_is_still_a_copy():
    raster = PillowRasterBackend()
    source = patterned()
    handle = raster.adopt(source)
    source.putpixel((1, 1), (9, 9, 9, 9))
    assert raster.export(handle).getpixel((1, 1)) == (40, 90, 7, 200)


def test_palette_transparency_survives_adopt():
    raster = PillowRasterBackend()
    palette = Image.new("P", (2, 1))
    palette.putpalette([255, 0, 0, 0, 255, 0] + [0] * 762)
    palette.putpixel((0, 0), 0)
    palette.putpixel((1, 0), 1)
    palette.info["transparency"] = 0
    exported = raster.export(raster.adopt(palette))
    assert exported.getpixel((0, 0))[3] == 0
    assert exported.getpixel((1, 0)) == (0, 255, 0, 255)


def test_export_is_a_copy():
    raster = PillowRasterBackend()
    handle = raster.adopt(patterned())
    out = raster.export(handle)
    out.putpixel((0, 0), (5, 5, 5, 5))
    assert raster.export(handle).getpixel((0, 0)) == (0, 0, 7, 200)


def test_session_raster_tracks_adopted_handles():
    backend = PillowRasterBackend()
    facade = _SessionRaster(backend)
    handle = facade.adopt(patterned())
    assert facade.checkpoint() == frozenset({handle})
    assert facade.export(handle).getpixel((1, 0)) == (40, 0, 7, 200)
    facade.release_except(frozenset())
    assert backend._images == {}
