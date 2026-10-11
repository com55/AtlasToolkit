"""Repack keeps page-level unknown lines such as `scale:` (deliberate JS divergence)."""
import io

from PIL import Image

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.engine.modifier import AtlasModifier
from atlas_toolkit.engine.session import AtlasSession
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

ATLAS = (
    "p.png\nsize: 8,4\nscale: 0.5\nfoo: bar, baz\n"
    "a\n bounds: 0,0,4,4\n"
    "b\n bounds: 4,0,4,4\n"
)


def _png(width, height):
    pixels = bytes(v for y in range(height) for x in range(width) for v in (x * 30, y * 50, 90, 255))
    buf = io.BytesIO()
    Image.frombytes("RGBA", (width, height), pixels).save(buf, format="PNG")
    return buf.getvalue()


def test_mod_repack_keeps_page_extra_lines():
    session = AtlasSession.load(ATLAS, {"p.png": _png(8, 4)}, PillowRasterBackend())
    session.apply_mod_image(_png(4, 4), ["a"])
    text, _ = session.save()
    page = AtlasDocument.parse(text).pages[0]
    assert page.extra_pairs == [("scale", ["0.5"]), ("foo", ["bar", "baz"])]
    assert "scale: 0.5\n" in text and "foo: bar, baz\n" in text
    session.close()


def test_structural_repack_keeps_page_extra_lines():
    session = AtlasSession.load(ATLAS, {"p.png": _png(8, 4)}, PillowRasterBackend())
    session.rename_region("a", "renamed")
    text, _ = session.save()
    assert AtlasDocument.parse(text).pages[0].extra_pairs == [("scale", ["0.5"]), ("foo", ["bar", "baz"])]
    session.close()


def test_page_info_and_rebuild_copy_extra_pairs():
    raster = PillowRasterBackend()
    document = AtlasDocument.parse(ATLAS)
    modifier = AtlasModifier(document, raster.decode_png(_png(8, 4)), raster, "p.png")
    info = modifier._page_info()
    info["extra_pairs"][0][1].append("mutated")
    assert document.pages[0].extra_pairs[0] == ("scale", ["0.5"])
    info = modifier._page_info()
    rebuilt = AtlasDocument.from_rebuild_args(info, [8, 4], [], {})
    info["extra_pairs"][1][1].append("mutated")
    assert rebuilt.pages[0].extra_pairs == [("scale", ["0.5"]), ("foo", ["bar", "baz"])]
