"""Tests for engine/modifier.py's repack helpers: JS key ordering, dedup
hashing, raw-sprite extraction, and the pack-rotation seam. Parity against
the real JS runs through conftest.py's run_packing_oracle fixture."""
import io

import pytest
from PIL import Image

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.engine.modifier import (
    canvas_hash,
    extract_raw_sprite,
    js_object_key_order,
    region_meta,
    rotate_sprite_for_pack,
)
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

KEY_SETS = {
    "keys_mixed": ["10", "2", "a"],
    "keys_boundary": ["4294967294", "4294967295", "4294967296", "0", "b"],
    "keys_non_canonical": ["01", "00", "007", "-1", "1.5", " 2", "+2", "2e3", "3"],
    "keys_all_strings": ["arm", "leg", "arm#2"],
    "keys_all_indices": ["7", "3", "11", "0"],
    # Non-ASCII digits: str.isdigit() is True for all three, int() converts
    # two of them and raises on the third. JS treats every one as an
    # ordinary string key, so only "5" may sort to the front.
    "keys_unicode_digits": ["²", "٢", "߅", "5", "x"],
}

CASES = {case_id: ("jsKeyOrder", [names]) for case_id, names in KEY_SETS.items()}


@pytest.fixture(scope="module")
def js_results(run_packing_oracle):
    case_ids = list(CASES)
    return dict(zip(case_ids, run_packing_oracle([CASES[c] for c in case_ids]), strict=True))


@pytest.mark.parametrize("case_id", list(CASES))
def test_key_order_matches_real_js(case_id, js_results):
    assert js_object_key_order(KEY_SETS[case_id]) == js_results[case_id]


def test_integer_like_names_sort_before_string_names():
    # The exact divergence this helper exists for: text order 10, 2, a
    # must pack as 2, 10, a.
    assert js_object_key_order(["10", "2", "a"]) == ["2", "10", "a"]


def test_non_canonical_numeric_names_are_not_indices():
    assert js_object_key_order(["01", "3", "+2"]) == ["3", "01", "+2"]


def test_unicode_digit_names_are_not_indices_and_do_not_raise():
    # "²".isdigit() is True but int("²") raises ValueError; without the
    # isascii() guard this call crashes instead of returning an order.
    assert js_object_key_order(["²", "5", "٢"]) == ["5", "²", "٢"]


def _patterned(raster: PillowRasterBackend, w: int, h: int, tint: int):
    """Opaque image whose pixel (x, y) is (x, y, tint, 255), so every pixel
    identifies its own source position (requires w, h <= 256)."""
    data = bytes(v for y in range(h) for x in range(w) for v in (x, y, tint, 255))
    buf = io.BytesIO()
    Image.frombytes("RGBA", (w, h), data).save(buf, format="PNG")
    return raster.decode_png(buf.getvalue())


def test_canvas_hash_groups_identical_pixels_and_separates_different_ones():
    raster = PillowRasterBackend()
    first = _patterned(raster, 6, 4, 100)
    same = _patterned(raster, 6, 4, 100)
    different_tint = _patterned(raster, 6, 4, 101)
    different_shape = _patterned(raster, 4, 6, 100)
    assert canvas_hash(raster, first) == canvas_hash(raster, same)
    assert canvas_hash(raster, first) != canvas_hash(raster, different_tint)
    # Same byte COUNT, different dimensions: the JS hashes only RGBA bytes,
    # so this pair is a known pre-existing collision risk there. Pillow's
    # tobytes is row-major over the real width, so the byte streams differ
    # whenever the pixel content does -- pinned so a future change to
    # get_pixels cannot silently make them collide.
    assert canvas_hash(raster, first) != canvas_hash(raster, different_shape)


def _rgba_png(raster, data):
    buf = io.BytesIO()
    Image.frombytes("RGBA", (2, 1), data).save(buf, format="PNG")
    return raster.decode_png(buf.getvalue())


@pytest.mark.parametrize("masked", [False, True])
def test_canvas_hash_normalizes_only_hidden_rgb_without_mutation_or_allocation(masked):
    raster = PillowRasterBackend()
    first = bytes([90, 1, 2, 0, 10, 20, 30, 255])
    same_visible = bytes([3, 4, 5, 0, 10, 20, 30, 255])
    different_visible = bytes([3, 4, 5, 0, 11, 20, 30, 255])
    inputs = [first, same_visible, different_visible]
    if masked:
        inputs = [data[:3] + b"\xff" + data[4:] for data in inputs]
    handles = [_rgba_png(raster, data) for data in inputs]
    if masked:
        mask = _rgba_png(raster, bytes([255, 255, 255, 0, 255, 255, 255, 255]))
        for handle in handles:
            raster.composite_destination_in(handle, mask)
        raster.dispose(mask)
    before = [(raster.size(h), raster.get_pixels(h)) for h in handles]
    assert [pixels for _, pixels in before] == [first, same_visible, different_visible]
    allocations = raster._next_handle
    live = set(raster._images)
    hashes = [canvas_hash(raster, h) for h in handles]
    assert [(raster.size(h), raster.get_pixels(h)) for h in handles] == before
    assert raster._next_handle == allocations
    assert set(raster._images) == live
    assert hashes[0] == hashes[1]
    assert hashes[0] != hashes[2]


def test_canvas_hash_preserves_alpha_one_and_its_rgb():
    raster = PillowRasterBackend()
    inputs = [
        bytes([90, 1, 2, 0, 10, 20, 30, 255]),
        bytes([90, 1, 2, 1, 10, 20, 30, 255]),
        bytes([91, 1, 2, 1, 10, 20, 30, 255]),
    ]
    assert len({canvas_hash(raster, _rgba_png(raster, data)) for data in inputs}) == 3


def test_extract_raw_sprite_undoes_a_stored_rotation():
    raster = PillowRasterBackend()
    page = _patterned(raster, 40, 40, 200)
    text = "p.png\nsize: 40,40\nr\n  rotate: true\n  bounds: 4, 6, 8, 5"
    region = region_meta(AtlasDocument.parse(text).pages[0].regions[0])
    sprite = extract_raw_sprite(raster, page, region)
    # bounds are stored pre-rotation, so the extracted sprite is w x h ...
    assert raster.size(sprite) == (8, 5)
    # ... and matches the rotation seam exactly.
    assert raster.get_pixels(sprite) == raster.get_pixels(
        raster.crop_rotate(page, 4, 6, 8, 5, 90)
    )


@pytest.mark.parametrize("degrees", [0, 45, 360])
def test_rotate_sprite_for_pack_passes_through_unrotated_degrees(degrees):
    raster = PillowRasterBackend()
    sprite = _patterned(raster, 8, 5, 50)
    # The same handle, never a clone: group_names_by_sprite_identity groups
    # by handle value.
    assert rotate_sprite_for_pack(raster, sprite, degrees) == sprite


@pytest.mark.parametrize(
    ("pack_degrees", "stored_degrees"), [(90, 270), (180, 180), (270, 90)]
)
def test_rotate_sprite_for_pack_inverts_the_degree(pack_degrees, stored_degrees):
    # Dimensions alone cannot catch a 90/270 swap (both swap w and h), so
    # this compares PIXELS against the rotation seam and asserts the
    # opposite degree genuinely differs.
    raster = PillowRasterBackend()
    sprite = _patterned(raster, 8, 5, 60)
    w, h = raster.size(sprite)
    crop_w, crop_h = (h, w) if stored_degrees in (90, 270) else (w, h)
    expected = raster.crop_rotate(sprite, 0, 0, crop_w, crop_h, stored_degrees)
    result = rotate_sprite_for_pack(raster, sprite, pack_degrees)
    assert raster.get_pixels(result) == raster.get_pixels(expected)
    if pack_degrees != 180:
        wrong = raster.crop_rotate(sprite, 0, 0, crop_w, crop_h, pack_degrees)
        assert raster.get_pixels(result) != raster.get_pixels(wrong)


def test_rotate_sprite_for_pack_matches_real_js_dimensions(run_packing_oracle):
    results = run_packing_oracle([("rotateSpriteForPack", [8, 5, deg]) for deg in (0, 90, 180, 270)])
    raster = PillowRasterBackend()
    for degrees, js in zip((0, 90, 180, 270), results, strict=True):
        sprite = _patterned(raster, 8, 5, 70)
        rotated = rotate_sprite_for_pack(raster, sprite, degrees)
        assert raster.size(rotated) == (js["w"], js["h"])
        assert (rotated == sprite) is js["same_object"]


def test_region_meta_carries_the_js_shape():
    # The WHOLE key set is pinned, not a subset. `page` is the load-bearing
    # one -- JS _parseScoped filters by it (atlas-modifier.js:701), so a
    # dropped or renamed `page` silently changes which regions a page-scoped
    # repack includes, and a partial assertion would still pass (Task 3
    # scrutinize finding). Values below were read off the real function, not
    # predicted: `rotate: true` parses to 90, and an absent `pad` is None.
    text = (
        "p.png\nsize: 40,40\narm\n  rotate: true\n  index: 3\n"
        "  bounds: 1, 2, 8, 5\n  offsets: 1, 1, 10, 10\n  split: 1, 2, 3, 4"
    )
    meta = region_meta(AtlasDocument.parse(text).pages[0].regions[0])
    assert set(meta) == {
        "name", "atlasName", "page", "index", "bounds",
        "offsets", "rotate", "split", "pad", "extraPairs",
    }
    assert meta["name"] == "arm"
    assert meta["atlasName"] == "arm"
    assert meta["page"] == "p.png"
    assert meta["index"] == 3
    assert meta["bounds"] == [1, 2, 8, 5]
    assert meta["offsets"] == [1, 1, 10, 10]
    assert meta["rotate"] == 90
    assert meta["split"] == [1, 2, 3, 4]
    assert meta["pad"] is None
    assert meta["extraPairs"] == []
