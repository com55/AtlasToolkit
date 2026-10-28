"""alpha_mode on the SpineAtlas edit and extract methods (spec 2026-10-28-alpha-mode)."""
import pickle

import numpy as np
import pytest
from PIL import Image

from atlas_toolkit import AlphaMode, MixedAlphaGroup
from atlas_toolkit.api import AtlasError, SpineAtlas
from atlas_toolkit.packer import PackerOptions
from atlas_toolkit.raster.premultiplied import premultiply, unpremultiply
from tests.python.pma_cases import GLOW_IN, premultiplied_pattern, with_glow

PMA = "p.png\nsize: 16,8\npma: true\nA\n bounds: 0,0,8,8\nB\n bounds: 8,0,8,8\n"
STRAIGHT = "s.png\nsize: 16,8\nA\n bounds: 0,0,8,8\nB\n bounds: 8,0,8,8\n"
MIXED = (
    "pma.png\nsize: 8,8\npma: true\nP\n bounds: 0,0,8,8\n"
    "\nst.png\nsize: 8,8\nS\n bounds: 0,0,8,8\n"
)
NO_MESH = PackerOptions(mesh_aware=False)


def straight_pattern(width, height, seed):
    """Straight RGBA: every pixel differs by position and seed; alphas cycle
    through 0 (with colour), 1, partial and opaque values."""
    ys, xs = np.mgrid[0:height, 0:width]
    arr = np.zeros((height, width, 4), np.uint8)
    arr[..., 0] = (xs * 37 + seed * 7) % 256
    arr[..., 1] = (ys * 53 + seed * 5) % 256
    arr[..., 2] = (xs * 11 + ys * 17 + seed) % 256
    arr[..., 3] = np.array([255, 0, 128, 1, 254, 64, 200], np.uint8)[(xs + 3 * ys + seed) % 7]
    return arr


def img(arr):
    return Image.fromarray(np.ascontiguousarray(arr))


def arr(image):
    return np.asarray(image)


def zero_clear(straight):
    """What a straight repack stores: alpha-0 pixels become (0, 0, 0, 0)."""
    out = straight.copy()
    out[out[..., 3] == 0] = 0
    return out


def load(text):
    if text == PMA:
        return SpineAtlas.load(text, img(with_glow(premultiplied_pattern(16, 8, 1), 3, 2, GLOW_IN)))
    if text == STRAIGHT:
        return SpineAtlas.load(text, img(straight_pattern(16, 8, 1)))
    return SpineAtlas.load(text, {
        "pma.png": img(with_glow(premultiplied_pattern(8, 8, 1), 3, 2, GLOW_IN)),
        "st.png": img(straight_pattern(8, 8, 2)),
    })


def built(atlas, **kwargs):
    return atlas.build(packer_options=NO_MESH, **kwargs)


# -- same space is a no-op (spec §3.3, review I-1) ---------------------------

SAME_SPACE = [(PMA, "premultiplied"), (STRAIGHT, "straight")]


@pytest.mark.parametrize("text, mode", SAME_SPACE)
def test_same_space_extracts_equal_native(text, mode):
    atlas = load(text)
    assert arr(atlas.extract("A", alpha_mode=mode)).tobytes() == arr(atlas.extract("A")).tobytes()
    ours, native = atlas.extract_all(alpha_mode=mode), atlas.extract_all()
    assert {k: arr(v).tobytes() for k, v in ours.items()} == {k: arr(v).tobytes() for k, v in native.items()}
    assert (arr(atlas.extract_composite(["A", "B"], alpha_mode=mode)).tobytes()
            == arr(atlas.extract_composite(["A", "B"])).tobytes())


@pytest.mark.parametrize("text, mode", SAME_SPACE)
def test_same_space_files_equal_native(text, mode, tmp_path):
    for sub, kwargs in (("with", {"alpha_mode": mode}), ("without", {})):
        atlas = load(text)
        atlas.extract_to("A", tmp_path / sub / "one.png", **kwargs)
        atlas.extract_to(["A", "B"], tmp_path / sub / "list", **kwargs)
        atlas.extract_all_to(tmp_path / sub / "all", **kwargs)
        atlas.extract_composite_to(["A", "B"], tmp_path / sub / "group.png", **kwargs)
    files = sorted(p.relative_to(tmp_path / "with") for p in (tmp_path / "with").rglob("*.png"))
    assert len(files) == 6
    for name in files:
        assert (tmp_path / "with" / name).read_bytes() == (tmp_path / "without" / name).read_bytes()


@pytest.mark.parametrize("text, mode", SAME_SPACE)
def test_same_space_inputs_equal_native(text, mode):
    image = img(straight_pattern(8, 8, 3) if text == STRAIGHT else premultiplied_pattern(8, 8, 3))
    pages = []
    for kwargs in ({"alpha_mode": mode}, {}):
        atlas = load(text)
        atlas.replace({"A": image}, **kwargs)
        atlas.replace_composite(["B"], image, **kwargs)
        atlas.add_region("N", image, **kwargs)
        pages.append({name: arr(page).tobytes() for name, page in built(atlas).pages.items()})
    assert pages[0] == pages[1]


# -- converting modes ---------------------------------------------------------

def test_straight_input_is_premultiplied_on_a_pma_page():
    atlas = load(PMA)
    straight = straight_pattern(8, 8, 3)
    atlas.replace({"A": img(straight)}, alpha_mode=AlphaMode.STRAIGHT)
    assert np.array_equal(arr(built(atlas).extract("A")), premultiply(straight))


def test_straight_extract_of_a_pma_page_is_unpremultiplied():
    atlas = load(PMA)
    assert np.array_equal(arr(atlas.extract("A", alpha_mode="straight")), unpremultiply(arr(atlas.extract("A"))))


def test_pma_straight_round_trip_loses_only_additive_pixels():
    atlas = load(PMA)
    native = arr(atlas.extract("A"))
    atlas.replace({"A": atlas.extract("A", alpha_mode="straight")}, alpha_mode="straight")
    back = arr(built(atlas).extract("A"))
    additive = (native[..., :3] > native[..., 3:]).any(axis=-1)
    assert additive[2, 3] and additive.sum() == 1  # the glow
    assert back[2, 3].tolist() == [0, 0, 0, 0]
    assert np.array_equal(back[~additive], native[~additive])


def test_premultiplied_input_is_unpremultiplied_on_a_straight_page():
    atlas = load(STRAIGHT)
    premultiplied = with_glow(premultiplied_pattern(8, 8, 3), 1, 1, GLOW_IN)
    atlas.replace({"A": img(premultiplied)}, alpha_mode="premultiplied")
    assert np.array_equal(arr(built(atlas).extract("A")), unpremultiply(premultiplied))


def test_premultiplied_extract_of_a_straight_page_is_premultiplied():
    atlas = load(STRAIGHT)
    assert np.array_equal(
        arr(atlas.extract("A", alpha_mode="premultiplied")), premultiply(arr(atlas.extract("A"))),
    )


def test_straight_page_premultiplied_round_trip_rules():
    atlas = load(STRAIGHT)
    native = arr(atlas.extract("A"))
    alpha = native[..., 3]
    assert (native[alpha == 0][:, :3] > 0).any()  # the fixture carries alpha-0 colour
    atlas.replace({"A": atlas.extract("A", alpha_mode="premultiplied")}, alpha_mode="premultiplied")
    back = arr(built(atlas).extract("A"))
    assert np.array_equal(back[..., 3], alpha)
    assert np.array_equal(back[alpha == 255], native[alpha == 255])
    assert not back[alpha == 0].any()
    partial = (alpha > 0) & (alpha < 255)
    error = np.abs(back[..., :3].astype(int) - native[..., :3])[partial]
    assert (error <= np.ceil(127.5 / alpha[partial] + 0.5)[:, None]).all()


# -- several pages in one call (review I-1) ---------------------------------

def test_replace_converts_each_entry_for_its_own_page():
    straight = straight_pattern(8, 8, 4)
    atlas = load(MIXED)
    atlas.replace({"P": img(straight), "S": img(straight)}, alpha_mode="straight")
    out = built(atlas)
    assert np.array_equal(arr(out.extract("P")), premultiply(straight))
    assert np.array_equal(arr(out.extract("S")), zero_clear(straight))

    premultiplied = premultiplied_pattern(8, 8, 4)
    atlas = load(MIXED)
    atlas.replace({"P": img(premultiplied), "S": img(premultiplied)}, alpha_mode="premultiplied")
    out = built(atlas)
    assert np.array_equal(arr(out.extract("P")), premultiplied)
    assert np.array_equal(arr(out.extract("S")), unpremultiply(premultiplied))


def test_extract_converts_each_image_for_its_own_page():
    atlas = load(MIXED)
    native = {name: arr(image) for name, image in atlas.extract(["P", "S"]).items()}
    for straight in (atlas.extract(["P", "S"], alpha_mode="straight"), atlas.extract_all(alpha_mode="straight")):
        assert np.array_equal(arr(straight["P"]), unpremultiply(native["P"]))
        assert np.array_equal(arr(straight["S"]), native["S"])
    for premultiplied in (atlas.extract(["P", "S"], alpha_mode="premultiplied"),
                          atlas.extract_all(alpha_mode="premultiplied")):
        assert np.array_equal(arr(premultiplied["P"]), native["P"])
        assert np.array_equal(arr(premultiplied["S"]), premultiply(native["S"]))


# -- add_region (spec §3.4, review I-2) --------------------------------------

LIGHT_PMA = "st.png\nsize: 16,8\nS\n bounds: 0,0,16,8\n\npma.png\nsize: 8,8\npma: true\nP\n bounds: 0,0,4,4\n"
LIGHT_STRAIGHT = "pma.png\nsize: 16,8\npma: true\nP\n bounds: 0,0,16,8\n\nst.png\nsize: 8,8\nS\n bounds: 0,0,4,4\n"


def two_pages(text):
    first, second = (line for line in text.splitlines() if line.endswith(".png"))
    return SpineAtlas.load(text, {first: Image.new("RGBA", (16, 8)), second: Image.new("RGBA", (8, 8))})


def test_add_region_converts_for_an_explicit_pma_page():
    straight = straight_pattern(4, 4, 5)
    atlas = two_pages(LIGHT_STRAIGHT)
    atlas.add_region("N", img(straight), page="pma.png", alpha_mode="straight")
    assert np.array_equal(arr(built(atlas).extract("N")), premultiply(straight))


def test_add_region_converts_for_the_page_it_picks():
    straight = straight_pattern(4, 4, 5)
    atlas = two_pages(LIGHT_PMA)
    atlas.add_region("N", img(straight), alpha_mode="straight")
    assert atlas.region("N").page == "pma.png"
    assert np.array_equal(arr(built(atlas).extract("N")), premultiply(straight))


def test_add_region_leaves_a_same_space_image_on_the_page_it_picks():
    straight = straight_pattern(4, 4, 5)
    atlas = two_pages(LIGHT_STRAIGHT)
    atlas.add_region("N", img(straight), alpha_mode="straight")
    assert atlas.region("N").page == "st.png"
    assert np.array_equal(arr(built(atlas).extract("N")), zero_clear(straight))


@pytest.mark.parametrize("page", ["nope.png", ["x"]])
def test_add_region_unknown_page_still_raises_value_error(page):
    atlas = two_pages(LIGHT_PMA)
    with pytest.raises(ValueError, match="unknown page"):
        atlas.add_region("N", img(straight_pattern(4, 4, 5)), page=page, alpha_mode="straight")
    assert not atlas.has_changes


# -- composites (spec §3.5) ---------------------------------------------------

def test_all_pma_group_converts_once_and_round_trips():
    straight = straight_pattern(8, 8, 6)
    atlas = load(PMA)
    atlas.replace_composite(["A", "B"], img(straight), alpha_mode="straight")
    out = built(atlas, split_composite=True)
    assert np.array_equal(arr(out.extract_composite(["A", "B"])), premultiply(straight))
    assert np.array_equal(
        arr(out.extract_composite(["A", "B"], alpha_mode="straight")), unpremultiply(premultiply(straight)),
    )


def test_all_straight_group_converts_once():
    premultiplied = premultiplied_pattern(8, 8, 6)
    atlas = load(STRAIGHT)
    native = arr(atlas.extract_composite(["A"]))
    assert np.array_equal(arr(atlas.extract_composite(["A"], alpha_mode="premultiplied")), premultiply(native))
    atlas.replace_composite(["A", "B"], img(premultiplied), alpha_mode="premultiplied")
    out = built(atlas)
    assert np.array_equal(arr(out.extract("A")), unpremultiply(premultiplied))
    assert np.array_equal(arr(out.extract("B")), unpremultiply(premultiplied))


def test_empty_group_with_alpha_mode_records_nothing():
    atlas = load(PMA)
    atlas.replace_composite([], img(straight_pattern(8, 8, 6)), alpha_mode="straight")
    assert not atlas.has_changes


def test_mixed_group_with_alpha_mode_raises_before_anything(recwarn):
    atlas = load(MIXED)
    with pytest.raises(MixedAlphaGroup, match="'P', 'S' mix pma and non-pma pages") as caught:
        atlas.replace_composite(["P", "S"], img(straight_pattern(8, 8, 6)), alpha_mode="straight")
    assert isinstance(caught.value, AtlasError) and isinstance(caught.value, ValueError)
    with pytest.raises(MixedAlphaGroup):
        atlas.extract_composite(["P", "S"], alpha_mode="premultiplied")
    assert not atlas.has_changes
    assert not [w for w in recwarn if issubclass(w.category, UserWarning)]


def test_mixed_alpha_group_pickles_with_its_names():
    error = pickle.loads(pickle.dumps(MixedAlphaGroup(["P", "S"])))
    assert error.names == ["P", "S"]
    assert "'P', 'S' mix pma and non-pma pages" in str(error)


# -- files (review M-6) --------------------------------------------------------

def test_files_carry_the_conversion(tmp_path):
    atlas = load(PMA)
    atlas.extract_to("A", tmp_path / "one.png", alpha_mode="straight")
    atlas.extract_to(["A", "B"], tmp_path / "list", alpha_mode="straight")
    atlas.extract_all_to(tmp_path / "all", alpha_mode="straight")
    atlas.extract_composite_to(["A", "B"], tmp_path / "group.png", alpha_mode="straight")
    want = {name: arr(image) for name, image in atlas.extract(["A", "B"], alpha_mode="straight").items()}
    group = arr(atlas.extract_composite(["A", "B"], alpha_mode="straight"))
    read = lambda path: np.asarray(Image.open(path))  # noqa: E731
    assert np.array_equal(read(tmp_path / "one.png"), want["A"])
    for folder in ("list", "all"):
        for name in ("A", "B"):
            assert np.array_equal(read(tmp_path / folder / f"{name}.png"), want[name])
    assert np.array_equal(read(tmp_path / "group.png"), group)
    assert not np.array_equal(want["A"], arr(atlas.extract("A")))  # the conversion is visible


# -- validation (review I-3) ---------------------------------------------------

ENTRY_POINTS = {
    "replace": lambda a, p, m: a.replace({"A": img(straight_pattern(8, 8, 7))}, alpha_mode=m),
    "replace_unknown_name": lambda a, p, m: a.replace({"nope": img(straight_pattern(8, 8, 7))}, alpha_mode=m),
    "replace_missing_image": lambda a, p, m: a.replace({"A": p / "missing.png"}, alpha_mode=m),
    "replace_not_a_mapping": lambda a, p, m: a.replace([("A", img(straight_pattern(8, 8, 7)))], alpha_mode=m),
    "replace_composite": lambda a, p, m: a.replace_composite(["A", "B"], img(straight_pattern(8, 8, 7)), alpha_mode=m),
    "replace_composite_unknown_name": lambda a, p, m: a.replace_composite(["nope"], img(straight_pattern(8, 8, 7)), alpha_mode=m),
    "replace_composite_missing_image": lambda a, p, m: a.replace_composite(["A", "B"], p / "missing.png", alpha_mode=m),
    "add_region": lambda a, p, m: a.add_region("N", img(straight_pattern(8, 8, 7)), alpha_mode=m),
    "add_region_missing_image": lambda a, p, m: a.add_region("N", p / "missing.png", alpha_mode=m),
    "extract": lambda a, p, m: a.extract("A", alpha_mode=m),
    "extract_unknown_name": lambda a, p, m: a.extract("nope", alpha_mode=m),
    "extract_none": lambda a, p, m: a.extract(None, alpha_mode=m),
    "extract_all": lambda a, p, m: a.extract_all(alpha_mode=m),
    "extract_composite": lambda a, p, m: a.extract_composite(["A", "B"], alpha_mode=m),
    "extract_composite_unknown_name": lambda a, p, m: a.extract_composite(["nope"], alpha_mode=m),
    "extract_composite_empty": lambda a, p, m: a.extract_composite([], alpha_mode=m),
    "extract_to_str": lambda a, p, m: a.extract_to("A", p / "one.png", alpha_mode=m),
    "extract_to_list": lambda a, p, m: a.extract_to(["A", "B"], p / "list", alpha_mode=m),
    "extract_all_to": lambda a, p, m: a.extract_all_to(p / "all", alpha_mode=m),
    "extract_composite_to": lambda a, p, m: a.extract_composite_to(["A", "B"], p / "group.png", alpha_mode=m),
    "extract_composite_to_unknown_name": lambda a, p, m: a.extract_composite_to(["nope"], p / "group.png", alpha_mode=m),
}


@pytest.mark.parametrize("entry", list(ENTRY_POINTS))
@pytest.mark.parametrize("value, error", [("bogus", ValueError), ("Straight", ValueError), (1, TypeError), (True, TypeError)])
def test_every_entry_point_rejects_a_bad_alpha_mode(entry, value, error, tmp_path):
    atlas = load(PMA)
    with pytest.raises(error, match="alpha_mode"):
        ENTRY_POINTS[entry](atlas, tmp_path, value)
    assert not atlas.has_changes
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("write", [
    lambda a, p: a.extract_to(["a/b", "a*b"], p / "clash", alpha_mode="bogus"),
    lambda a, p: a.extract_all_to(p / "clash", alpha_mode="bogus"),
], ids=["extract_to", "extract_all_to"])
def test_bad_alpha_mode_is_reported_before_a_file_name_clash(write, tmp_path):
    text = "p.png\nsize: 16,8\na/b\n bounds: 0,0,8,8\na*b\n bounds: 8,0,8,8\n"
    atlas = SpineAtlas.load(text, Image.new("RGBA", (16, 8)))
    with pytest.raises(ValueError, match="alpha_mode"):
        write(atlas, tmp_path)


def test_a_mode_string_means_the_enum_member():
    # Unparsed, "premultiplied" is not AlphaMode.PREMULTIPLIED by identity and
    # would convert a pma page; parsed, it is the same-space no-op.
    atlas = load(PMA)
    native = arr(atlas.extract("A")).tobytes()
    assert arr(atlas.extract("A", alpha_mode="premultiplied")).tobytes() == native
    assert arr(atlas.extract("A", alpha_mode=AlphaMode.PREMULTIPLIED)).tobytes() == native
