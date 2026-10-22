"""repack(piece_sprites=...): pieces replace a region's
sprite and offsets without being re-extracted (spec 2026-10-13 §3.5)."""
import io

from PIL import Image

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.engine.composite_split import Piece
from atlas_toolkit.engine.modifier import AtlasModifier, region_meta
from atlas_toolkit.domain.effective_model import derive_effective_model
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

TEXT = "page.png\nsize: 16,8\nA\n bounds: 0,0,6,5\n offsets: 1,2,10,8\nB\n bounds: 6,0,4,4\n"


def _setup():
    raster = PillowRasterBackend()
    page = Image.new("RGBA", (16, 8), (10, 20, 30, 255))
    buf = io.BytesIO(); page.save(buf, format="PNG")
    base = raster.decode_png(buf.getvalue())
    doc = AtlasDocument.parse(TEXT)
    modifier = AtlasModifier(doc, base, raster, "page.png")
    names = list(modifier.region_names)
    effective = derive_effective_model({n: region_meta(modifier.regions[n]) for n in names}, [])
    return raster, modifier, effective


def test_piece_replaces_sprite_and_offsets_without_touching_inputs():
    raster, modifier, effective = _setup()
    regions_before = dict(effective["regions"])
    a_offsets_before = list(modifier.regions["A"].offsets)
    piece_image = raster.from_pixels(3, 2, bytes([200, 0, 0, 255]) * 6)
    pack = modifier.repack(
        {}, piece_sprites={"A": Piece(piece_image, [4, 5, 20, 16])},
    )
    region = pack.document.regions_by_key()["A"]
    assert list(region.offsets) == [4, 5, 20, 16]
    x, y, w, h, _rot = pack.region_bounds["A"]
    assert (w, h) == (3, 2)
    page = raster.export(pack.image)
    assert page.getpixel((x, y)) == (200, 0, 0, 255)
    assert effective["regions"] == regions_before            # caller's mapping unchanged
    assert effective["regions"]["A"]["offsets"] == a_offsets_before
    assert list(modifier.regions["A"].offsets) == a_offsets_before


def test_no_pieces_is_byte_identical():
    raster, modifier, effective = _setup()
    plain = modifier.repack({})
    empty = modifier.repack({}, piece_sprites={})
    assert plain.document.serialize() == empty.document.serialize()
    assert raster.get_pixels(plain.image) == raster.get_pixels(empty.image)
