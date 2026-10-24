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


def names_on(text, filename):
    doc = AtlasDocument.parse(text)
    return [r.atlas_name for p in doc.pages if p.filename == filename for r in p.regions]


def test_add_with_an_explicit_page():
    session = load()
    session.add_region(solid(3, 3, (7, 7, 7)), "N", page="two.png")
    assert session.added_region_pages() == {"N": "two.png"}
    assert [s.page_filename for s in session.snapshot().regions if s.key == "N"] == ["two.png"]
    text, pages = session.pack_pending()
    assert "N" in names_on(text, "two.png") and "N" not in names_on(text, "one.png")
    assert page_section(text, "one.png") == page_section(ATLAS, "one.png")
    assert raw(child(text, pages).extract_images(["N"])["N"]) == raw(solid(3, 3, (7, 7, 7)))


def test_unknown_page_records_nothing_and_decodes_nothing():
    session = load()
    with pytest.raises(ValueError):
        session.add_region(b"not a png", "N", page="three.png")
    assert session._mod_batches == []


def test_auto_page_is_the_lightest_and_spreads():
    # one.png: A 30 + B 48 = 78 px²; two.png: C 32 + D 36 = 68 px².
    session = load()
    session.add_region(solid(4, 4, (1, 1, 1)), "N1")          # two.png: 68 -> 84
    session.add_region(solid(2, 2, (1, 1, 1)), "N2")          # one.png: 78 -> 82
    assert session.added_region_pages() == {"N1": "two.png", "N2": "one.png"}


def test_auto_page_counts_removes_and_replaces():
    session = load()
    session.remove_regions(["A", "B"])                        # one.png weighs 0
    session.add_region(solid(9, 9, (1, 1, 1)), "N1")
    assert session.added_region_pages()["N1"] == "one.png"
    other = load()
    other.apply_mod_image(solid(1, 1, (1, 1, 1)), ["A"])      # one.png: 1 + 48 = 49
    other.apply_mod_image(solid(1, 1, (1, 1, 1)), ["B"])      # one.png: 1 + 1 = 2
    other.add_region(solid(3, 3, (1, 1, 1)), "N")
    assert other.added_region_pages()["N"] == "one.png"


def test_auto_page_counts_a_shared_composite_once_and_ties_pick_the_earlier_page():
    session = load()
    # One composite over A and B (one page): counts 10x10 once -> 100 > 68.
    session.apply_mod_image(solid(10, 10, (1, 1, 1)), ["A", "B"], composite=True)
    session.add_region(solid(4, 8, (1, 1, 1)), "N1")          # two.png: 68 + 32 = 100
    session.add_region(solid(1, 1, (1, 1, 1)), "N2")          # tie 100 / 100 -> one.png
    assert session.added_region_pages() == {"N1": "two.png", "N2": "one.png"}


def test_auto_page_uses_scaled_bounds():
    # two.png's real image is twice its declared size: C, D weigh 4x (272).
    session = load(pages={"one.png": page(16, 8, 10), "two.png": page(32, 16, 90)})
    session.add_region(solid(3, 3, (1, 1, 1)), "N")
    assert session.added_region_pages()["N"] == "one.png"     # 78 < 272


def test_rename_is_checked_across_pages_and_rename_only_page_keeps_pixels():
    from atlas_toolkit.engine.session import RegionNameError

    session = load()
    with pytest.raises(RegionNameError):
        session.rename_region("C", "A")
    session.rename_region("C", "Sea")
    session.apply_mod_image(solid(6, 5, (1, 2, 3)), ["A"])    # one.png touched, two.png rename-only
    text, pages = session.pack_pending()
    assert raw(pages["two.png"]) == raw(page(16, 8, 90))
    assert page_section(text, "two.png") == page_section(ATLAS, "two.png").replace("\nC\n", "\nSea\n")


def test_a_page_with_a_mod_and_a_rename_keeps_the_rename():
    session = load()
    session.apply_mod_image(solid(6, 5, (1, 2, 3)), ["A"])
    session.rename_region("B", "Bee")
    text, _ = session.pack_pending()
    assert sorted(names_on(text, "one.png")) == ["A", "Bee"]


def test_removing_a_pages_last_region_leaves_a_10x10_page():
    text_in = ATLAS.replace("two.png\nsize: 16,8\n", "two.png\nsize: 16,8\nfilter: Linear,Linear\n")
    session = load(text_in)
    session.remove_regions(["C", "D"])
    text, pages = session.pack_pending()
    two = next(p for p in AtlasDocument.parse(text).pages if p.filename == "two.png")
    assert two.size == [10, 10] and two.regions == [] and two.filter == ["Linear", "Linear"]
    assert raw(pages["two.png"]) == raw(Image.new("RGBA", (10, 10), (0, 0, 0, 0)))
    assert page_section(text, "one.png") == page_section(text_in, "one.png")


def test_removing_every_region_still_raises():
    from atlas_toolkit.engine.session import RemoveAllRegionsError

    session = load()
    with pytest.raises(RemoveAllRegionsError):
        session.remove_regions(["A", "B", "C", "D"])


def test_repeated_builds_never_change_the_pristine_document():
    # one.png is emptied and two.png is rename-only: both clone branches run.
    session = load()
    session.rename_region("C", "Sea")
    session.remove_regions(["A", "B"])
    before = session._document.serialize()
    first = session.pack_pending()
    session.set_nest_regions_enabled(True)
    session.pack_pending()
    session.set_nest_regions_enabled(False)
    again = session.pack_pending()
    assert session._document.serialize() == before
    assert first[0] == again[0]
    assert {k: raw(v) for k, v in first[1].items()} == {k: raw(v) for k, v in again[1].items()}


@pytest.mark.parametrize("replace_it", [False, True])
def test_add_then_remove_leaves_its_page_pristine(replace_it):
    """An added region that is removed again (even after a replace) nets
    out: its page is not repacked, only the page with a real edit is."""
    session = load()
    session.add_region(solid(5, 5, (7, 7, 7)), "X", page="two.png")
    if replace_it:
        session.apply_mod_image(solid(5, 5, (8, 8, 8)), ["X"])
    session.remove_regions(["X"])
    session.apply_mod_image(solid(6, 5, (1, 2, 3)), ["A"])
    text, pages = session.pack_pending()
    assert page_section(text, "two.png") == page_section(ATLAS, "two.png")
    assert raw(pages["two.png"]) == raw(page(16, 8, 90))
    assert raw(child(text, pages).extract_images(["A"])["A"]) == raw(solid(6, 5, (1, 2, 3)))
