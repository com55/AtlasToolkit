"""pma pages with mesh features through the public API (spec 2026-10-26
§5.1-§5.4, §4 force-resize replacement, Review Focus 1-3, 5)."""
import warnings

import numpy as np
import pytest
from PIL import Image

from atlas_toolkit.api import SpineAtlas
from atlas_toolkit.packer import PackerOptions
from tests.python.pma_cases import (
    GLOW_IN, GLOW_OUT, PACKERS, fake_skeleton, mesh, premultiplied_pattern, with_glow,
)

# 48x16 pma page. 16x8 regions; G, N, K, K2 have left-half hulls, Plain has
# no mesh. K and K2 hold identical pixels but different meshes (dedup with
# a footprint union). O has offsets: an 8x8 sprite on a 12x12 canvas.
TEXT = (
    "page.png\nsize: 48,16\npma: true\n"
    "G\n bounds: 0,0,16,8\n"
    "N\n bounds: 16,0,16,8\n"
    "K\n bounds: 32,0,16,8\n"
    "K2\n bounds: 32,0,16,8\n"
    "Plain\n bounds: 0,8,16,8\n"
    "O\n bounds: 16,8,8,8\n offsets: 2,2,12,12\n"
)
MESHES = {
    "G": mesh(0, 0, 0.5, 1), "N": mesh(0, 0, 0.5, 1),
    "K": mesh(0, 0, 0.5, 1), "K2": mesh(0, 0, 0.25, 1),
    "O": mesh(0, 0, 0.5, 1),
}
IN, OUT = (2, 3), (12, 3)      # (x, y) inside / outside a left-half hull


def page_arr():
    arr = np.zeros((16, 48, 4), np.uint8)
    for i, (x0, y0) in enumerate([(0, 0), (16, 0), (32, 0), (0, 8)]):
        arr[y0:y0 + 8, x0:x0 + 16] = premultiplied_pattern(16, 8, i + 1)
        arr[y0 + IN[1], x0 + IN[0]] = GLOW_IN
        arr[y0 + OUT[1], x0 + OUT[0]] = GLOW_OUT
    arr[8:16, 16:24] = premultiplied_pattern(8, 8, 9)
    arr[8 + 2, 16 + 1] = GLOW_IN
    return arr


def load(monkeypatch, skeleton=True):
    image = Image.fromarray(page_arr(), "RGBA")
    if not skeleton:
        return SpineAtlas.load(TEXT, image)
    fake_skeleton(monkeypatch, MESHES)
    return SpineAtlas.load(TEXT, image, skeleton=b"x")


def px(image):
    return np.asarray(image.convert("RGBA")).astype(int)


def img(arr):
    return Image.fromarray(arr, "RGBA")


def test_mesh_crop_extract_keeps_inside_and_clears_outside_all_channels(monkeypatch):
    atlas = load(monkeypatch)
    g = px(atlas.extract("G", mesh_crop=True))
    assert g[IN[1], IN[0]].tolist() == list(GLOW_IN)
    assert g[OUT[1], OUT[0]].tolist() == [0, 0, 0, 0]
    assert not g[:, 8:].any()
    raw = px(atlas.extract("G"))
    assert raw[OUT[1], OUT[0]].tolist() == list(GLOW_OUT)    # no crop: rect owned


def test_region_with_offsets_keeps_glow_through_the_canvas_paste(monkeypatch):
    atlas = load(monkeypatch)
    o = px(atlas.extract("O"))                 # 12x12 canvas, sprite at left 2, top 2
    assert o.shape[:2] == (12, 12)
    assert o[2 + 2, 2 + 1].tolist() == list(GLOW_IN)
    cropped = px(atlas.extract("O", mesh_crop=True))
    assert not cropped[:, 6:].any()            # hull = left half of the 12-wide canvas


@pytest.mark.parametrize("force_resize", [False, True])
def test_mesh_crop_composite_masks_every_pma_image(monkeypatch, force_resize):
    atlas = load(monkeypatch)
    comp = px(atlas.extract_composite(["G", "N"], mesh_crop=True, force_resize=force_resize))
    assert not comp[:, 8:].any()               # both hulls are the left half
    if not force_resize:
        # G's additive pixel over N's at the same spot: premultiplied over
        # adds them (alpha 0 lets everything under it through).
        assert comp[IN[1], IN[0]].tolist() == [2 * c for c in GLOW_IN[:3]] + [0]


def test_mesh_crop_force_resize_masks_the_scaled_image(monkeypatch):
    # G (16x8) is stretched onto O's 12x12 frame: it must be masked AFTER the
    # premultiplied resize, so nothing lands right of either left-half hull.
    atlas = load(monkeypatch)
    comp = px(atlas.extract_composite(["G", "O"], mesh_crop=True, force_resize=True))
    assert comp.shape[:2] == (12, 12)
    assert comp[:, :6, 3].any()
    assert not comp[:, 6:].any()


@pytest.mark.parametrize("packer, options", PACKERS)
def test_packing_keeps_owned_glow_and_clears_the_rest(monkeypatch, packer, options):
    atlas = load(monkeypatch)
    atlas.replace({"N": img(with_glow(with_glow(premultiplied_pattern(16, 8, 7), *IN, GLOW_IN), *OUT, GLOW_OUT))})
    built = atlas.build(packer=packer, packer_options=options)
    for name in ("G", "N", "K"):
        cropped = px(built.extract(name, mesh_crop=True))
        assert cropped[IN[1], IN[0]].tolist() == list(GLOW_IN), name
        assert not cropped[:, 8:].any(), name
    plain = px(built.extract("Plain"))                       # no mesh: whole rect
    assert plain[OUT[1], OUT[0]].tolist() == list(GLOW_OUT)
    k2 = px(built.extract("K2", mesh_crop=True))             # dedup twin, narrower hull
    assert not k2[:, 4:].any()


@pytest.mark.parametrize("packer, options", PACKERS[:2])
def test_gate_guard_no_glow_outside_hull_on_the_sheet(monkeypatch, packer, options):
    # Spec §5.2a: shelf/rectangles never overlap, so the raw rect of every
    # mesh region must be (0,0,0,0) outside its hull -- pristine G and
    # modded N alike.
    atlas = load(monkeypatch)
    atlas.replace({"N": img(with_glow(premultiplied_pattern(16, 8, 7), *OUT, GLOW_OUT))})
    built = atlas.build(packer=packer, packer_options=options)
    for name in ("G", "N"):
        raw = px(built.extract(name))
        assert raw[OUT[1], OUT[0]].tolist() == [0, 0, 0, 0], name
        assert not raw[:, 8:].any(), name


def test_polygons_interlocks_and_neighbours_stay_untouched(monkeypatch):
    atlas = load(monkeypatch)
    atlas.replace({"N": img(premultiplied_pattern(16, 8, 7))})
    built = atlas.build(packer="polygons", packer_options=PackerOptions(padding=2))
    boxes = {}
    for r in built.regions:
        x, y, w, h = r.bounds
        if r.rotate in (90, 270):
            w, h = h, w
        boxes[r.name] = (x, y, x + w, y + h)
    names = list(boxes)
    overlaps = [
        (a, b) for i, a in enumerate(names) for b in names[i + 1:]
        if boxes[a][0] < boxes[b][2] and boxes[b][0] < boxes[a][2]
        and boxes[a][1] < boxes[b][3] and boxes[b][1] < boxes[a][3]
        and boxes[a] != boxes[b]
    ]
    assert overlaps, "fixture must make polygons overlap two bounding boxes"
    # Unedited neighbours read back exactly what they were before the
    # build: no glow from an overlapping bounding box was added onto them.
    source = load(monkeypatch)
    for name in ("G", "K", "K2"):
        assert np.array_equal(px(built.extract(name, mesh_crop=True)), px(source.extract(name, mesh_crop=True))), name


@pytest.mark.parametrize("packer, options", PACKERS)
def test_untouched_region_keeps_its_glow_with_mesh(monkeypatch, packer, options):
    atlas = load(monkeypatch)
    atlas.replace({"N": img(premultiplied_pattern(16, 8, 7))})
    before = px(atlas.extract("G", mesh_crop=True))
    built = atlas.build(packer=packer, packer_options=options)
    assert np.array_equal(px(built.extract("G", mesh_crop=True)), before)


@pytest.mark.parametrize("packer", ["shelf", "rectangles"])
def test_untouched_region_keeps_its_whole_rect_without_mesh(monkeypatch, packer):
    atlas = load(monkeypatch, skeleton=False)
    atlas.replace({"N": img(premultiplied_pattern(16, 8, 7))})
    before = px(atlas.extract("G"))
    built = atlas.build(packer=packer)
    assert np.array_equal(px(built.extract("G")), before)


PADDED = "p.png\nsize: 200,200\npma: true\nhero\n  bounds: 0, 0, 100, 108\n  offsets: 10, 20, 200, 200\n"


def test_padded_mod_keeps_its_glow():
    # Spec §5 test 4: a 100x108 mod on the 200x200 offsets canvas resolves to
    # a 104x104 canvas, so prepare_mod_image pads it (pasted at (5, -14)).
    # The glow at mod (10, 20) lands at canvas (15, 6), i.e. (25, 82) in the
    # 200x200 extract (left 10, top 200 - 20 - 104 = 76).
    page = np.zeros((200, 200, 4), np.uint8)
    page[:108, :100] = premultiplied_pattern(100, 108, 3)
    atlas = SpineAtlas.load(PADDED, img(page))
    atlas.replace({"hero": img(with_glow(premultiplied_pattern(100, 108, 6), 10, 20, GLOW_IN))})
    built = atlas.build()
    assert tuple(built.regions[0].bounds[2:]) == (104, 104)
    hero = px(built.extract("hero"))
    assert hero[82, 25].tolist() == list(GLOW_IN)


def _exact_pattern(w, h):
    """Premultiplies exactly: RGB multiples of 15, alpha in {0, 51, 85, 170, 255}."""
    ys, xs = np.mgrid[0:h, 0:w]
    arr = np.zeros((h, w, 4), np.uint8)
    arr[..., 0] = (xs * 17) % 256 // 15 * 15
    arr[..., 1] = (ys * 17) % 256 // 15 * 15
    arr[..., 2] = ((xs * 7 + ys * 13) % 256) // 15 * 15
    arr[..., 3] = np.array([255, 85, 0, 51, 255, 0, 170], np.uint8)[(xs + 2 * ys) % 7]
    return arr


def _premultiply(arr):
    out = arr.astype(np.uint16)
    out[..., :3] = out[..., :3] * out[..., 3:4] // 255
    return out.astype(np.uint8)


SIZES = (
    "page.png\nsize: 16,16\n"
    "M\n bounds: 0,0,16,16\n"
    "L\n bounds: 0,0,8,8\n"
)


def test_pma_force_resize_has_no_bright_halo():
    # Spec §4 (c): the input premultiplies exactly, so on opaque output
    # pixels the only difference from the straight path is rounding (<= 1).
    # Without overshoot normalisation this measured 21.
    arr = _exact_pattern(16, 16)
    straight = SpineAtlas.load(SIZES, Image.fromarray(arr, "RGBA"))
    pma = SpineAtlas.load(SIZES.replace("16,16\n", "16,16\npma: true\n", 1), Image.fromarray(_premultiply(arr), "RGBA"))
    want = _premultiply(np.asarray(straight.extract_composite(["M", "L"], force_resize=True))).astype(int)
    got = px(pma.extract_composite(["M", "L"], force_resize=True))
    opaque = got[..., 3] == 255
    assert opaque.any()
    assert np.max(np.abs(got[opaque] - want[opaque])) <= 1
