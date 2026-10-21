"""Deferred multi-page builds: each touched page is packed on its own;
untouched pages stay pristine (spec 2026-10-21 §3.1, §3.7)."""
import numpy as np
import pytest
from PIL import Image

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.domain import packing
from atlas_toolkit.domain.alpha_split import SPLIT
from atlas_toolkit.domain.packing import CanvasTooLargeError
from atlas_toolkit.engine.session import AtlasSession
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

ATLAS = (
    "one.png\nsize: 16,8\n"
    "A\n bounds: 0,0,6,5\n"
    "B\n bounds: 6,0,8,6\n"
    "\ntwo.png\nsize: 16,8\n"
    "C\n bounds: 0,0,8,4\n"
    "D\n bounds: 8,0,6,6\n"
)


def page(width, height, seed):
    return Image.frombytes("RGBA", (width, height), bytes(
        v for y in range(height) for x in range(width)
        for v in ((x * 15 + seed) % 256, (y * 30 + seed) % 256, seed, 255)))


def solid(width, height, rgb):
    return Image.new("RGBA", (width, height), (*rgb, 255))


def raw(image):
    return image.size, image.convert("RGBA").tobytes()


def load(text=ATLAS, pages=None):
    pages = pages or {"one.png": page(16, 8, 10), "two.png": page(16, 8, 90)}
    return AtlasSession.load_images(text, pages, PillowRasterBackend(), defer_repack=True)


def child(text, pages):
    return AtlasSession.load_images(text, pages, PillowRasterBackend())


def page_section(text, filename):
    doc = AtlasDocument.parse(text)
    return AtlasDocument([p for p in doc.pages if p.filename == filename]).serialize()


def test_replace_on_one_page_repacks_only_that_page():
    session = load()
    session.apply_mod_image(solid(6, 5, (1, 2, 3)), ["A"])
    text, pages = session.pack_pending()
    assert AtlasDocument.parse(text).page_filenames() == ["one.png", "two.png"]
    assert list(pages) == ["one.png", "two.png"]
    assert page_section(text, "two.png") == page_section(ATLAS, "two.png")
    assert raw(pages["two.png"]) == raw(page(16, 8, 90))
    assert raw(child(text, pages).extract_images(["A"])["A"]) == raw(solid(6, 5, (1, 2, 3)))
    assert raw(child(text, pages).extract_images(["B"])["B"]) == raw(load().extract_images(["B"])["B"])


def test_immediate_mode_still_refuses_multi_page():
    session = AtlasSession.load_images(
        ATLAS, {"one.png": page(16, 8, 10), "two.png": page(16, 8, 90)}, PillowRasterBackend())
    with pytest.raises(NotImplementedError):
        session.apply_mod_image(solid(6, 5, (1, 2, 3)), ["A"])


@pytest.mark.parametrize("edited", ["one.png", "two.png"])
def test_scaled_page_touched_or_untouched(edited):
    # two.png's real image is twice its declared size.
    session = load(pages={"one.png": page(16, 8, 10), "two.png": page(32, 16, 90)})
    if edited == "two.png":
        session.apply_mod_image(solid(16, 8, (4, 5, 6)), ["C"])
    else:
        session.apply_mod_image(solid(6, 5, (4, 5, 6)), ["A"])
    text, pages = session.pack_pending()
    doc = AtlasDocument.parse(text)
    two = next(p for p in doc.pages if p.filename == "two.png")
    if edited == "two.png":
        assert two.size == list(pages["two.png"].size)
        assert raw(child(text, pages).extract_images(["D"])["D"]) == raw(load(
            pages={"one.png": page(16, 8, 10), "two.png": page(32, 16, 90)}).extract_images(["D"])["D"])
    else:
        assert two.size == [16, 8]                                  # declared size kept
        assert raw(pages["two.png"]) == raw(page(32, 16, 90))       # pristine pixels


def test_cross_page_composite_gives_each_page_its_own_copy():
    session = load()
    session.apply_mod_image(solid(7, 5, (9, 9, 9)), ["A", "C"], composite=True)
    batch = session._mod_batches[-1]
    assert batch["is_full_canvas"] is True
    assert session._modded_sprites["A"] != session._modded_sprites["C"]
    text, pages = session.pack_pending()
    built = child(text, pages)
    assert raw(built.extract_images(["A"])["A"]) == raw(solid(7, 5, (9, 9, 9)))
    assert raw(built.extract_images(["C"])["C"]) == raw(solid(7, 5, (9, 9, 9)))
    regions = AtlasDocument.parse(text).regions_by_key()
    assert regions["A"].page_filename == "one.png" and regions["C"].page_filename == "two.png"


SPLIT_ATLAS = (
    "one.png\nsize: 64,32\n"
    "A\n bounds: 0,0,16,10\n offsets: 0,6,32,16\n"
    "Other\n bounds: 40,0,6,6\n"
    "\ntwo.png\nsize: 64,32\n"
    "C\n bounds: 0,0,12,10\n offsets: 2,0,32,16\n"
)


def opaque_composite():
    arr = np.zeros((16, 32, 4), np.uint8)
    arr[..., 0] = np.arange(32)[None, :] * 8
    arr[..., 1] = np.arange(16)[:, None] * 16
    arr[..., 3] = 255
    return arr


def test_cross_page_split_sends_each_piece_to_its_page():
    session = load(SPLIT_ATLAS, {"one.png": page(64, 32, 10), "two.png": page(64, 32, 90)})
    session.apply_mod_image(Image.fromarray(opaque_composite(), "RGBA"), ["A", "C"], composite=True)
    text, pages = session.pack_pending(split_composite=True)
    regions = AtlasDocument.parse(text).regions_by_key()
    assert regions["A"].page_filename == "one.png" and regions["C"].page_filename == "two.png"
    assert regions["A"].offsets == [0, 6, 32, 16] and [regions["A"].w, regions["A"].h] == [16, 10]
    assert regions["C"].offsets == [2, 0, 32, 16] and [regions["C"].w, regions["C"].h] == [12, 10]


def test_cross_page_piece_keeps_the_composites_resolution():
    # two.png's real image is half its declared size: C's scaled canvas is
    # 16x8, but its piece is stored at the composite's 32x16 (spec §3.7, r2 M4).
    session = load(SPLIT_ATLAS, {"one.png": page(64, 32, 10), "two.png": page(32, 16, 90)})
    session.apply_mod_image(Image.fromarray(opaque_composite(), "RGBA"), ["A", "C"], composite=True)
    text, pages = session.pack_pending(split_composite=True)
    c = AtlasDocument.parse(text).regions_by_key()["C"]
    assert c.offsets == [2, 0, 32, 16] and [c.w, c.h] == [12, 10]


def test_cross_page_split_scales_rgb_only_on_the_pma_page():
    text = SPLIT_ATLAS.replace("two.png\nsize: 64,32\n", "two.png\nsize: 64,32\npma: true\n")
    session = load(text, {"one.png": page(64, 32, 10), "two.png": page(64, 32, 90)})
    arr = opaque_composite()
    arr[7, 5] = (100, 50, 20, 128)            # inside A's rect (0,0,16,10) and C's (2,6,14,16)
    session.apply_mod_image(Image.fromarray(arr, "RGBA"), ["A", "C"], composite=True)
    out_text, pages = session.pack_pending(split_composite=True)
    built = child(out_text, pages)
    top, bottom = SPLIT[128]
    a = built.extract_images(["A"])["A"].getpixel((5, 7))
    c = built.extract_images(["C"])["C"].getpixel((5, 7))
    assert a == (100, 50, 20, top)                                      # straight page
    assert c == (*(int(np.rint(v * bottom / 128)) for v in (100, 50, 20)), bottom)


def test_canvas_too_large_names_its_page(monkeypatch):
    session = load()
    session.apply_mod_image(solid(8, 4, (1, 1, 1)), ["C"])
    monkeypatch.setattr(packing, "MAX_CANVAS", 7)       # C 8x4 alone is over 7
    with pytest.raises(CanvasTooLargeError) as raised:
        session.pack_pending()
    assert raised.value.page == "two.png"


def test_single_page_canvas_too_large_has_no_page(monkeypatch):
    session = AtlasSession.load_images(
        "p.png\nsize: 16,8\nA\n bounds: 0,0,8,4\n", {"p.png": page(16, 8, 1)},
        PillowRasterBackend(), defer_repack=True)
    session.apply_mod_image(solid(8, 4, (1, 1, 1)), ["A"])
    monkeypatch.setattr(packing, "MAX_CANVAS", 7)
    with pytest.raises(CanvasTooLargeError) as raised:
        session.pack_pending()
    assert not hasattr(raised.value, "page")


def test_later_replace_keeps_its_member_out_of_a_cross_page_split():
    pages = {"one.png": page(64, 32, 10), "two.png": page(64, 32, 90)}
    session = load(SPLIT_ATLAS, pages)
    session.apply_mod_image(Image.fromarray(opaque_composite(), "RGBA"), ["A", "C"], composite=True)
    session.apply_mod_image(solid(16, 10, (7, 7, 7)), ["A"])
    text, out = session.pack_pending(split_composite=True)
    c = AtlasDocument.parse(text).regions_by_key()["C"]
    assert c.offsets == [2, 0, 32, 16] and [c.w, c.h] == [12, 10]        # C is still a piece
    ref = load(SPLIT_ATLAS, pages)
    ref.apply_mod_image(solid(16, 10, (7, 7, 7)), ["A"])
    ref_text, ref_out = ref.pack_pending(split_composite=True)
    assert raw(child(text, out).extract_images(["A"])["A"]) == raw(child(ref_text, ref_out).extract_images(["A"])["A"])
