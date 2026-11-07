"""AtlasModifier.place_in_place: keep a page's layout when its edits fit (spec 2026-11-05)."""
import numpy as np
import pytest
from PIL import Image

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.engine.modifier import AtlasModifier
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

UPPER_LEFT = {"uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2]}
LOWER_RIGHT = {"uvs": [1, 0, 1, 1, 0, 1], "triangles": [0, 1, 2]}


def pattern(w, h, seed=1):
    ys, xs = np.mgrid[0:h, 0:w]
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[..., 0] = (xs * 5 + seed) % 256
    rgba[..., 1] = (ys * 7 + seed * 3) % 256
    rgba[..., 2] = (xs * 11 + ys * 13 + seed) % 256
    rgba[..., 3] = 255
    return rgba


def page_rgba(w, h, rects, seed=1):
    """Opaque position pattern inside each (x, y, w, h) rect, (0,0,0,0) elsewhere."""
    rgba = pattern(w, h, seed)
    inside = np.zeros((h, w), bool)
    for x, y, rw, rh in rects:
        inside[y:y + rh, x:x + rw] = True
    rgba[~inside] = 0
    return rgba


def setup(text, rgba):
    raster = PillowRasterBackend()
    document = AtlasDocument.parse(text)
    filename = document.pages[0].filename
    modifier = AtlasModifier(document, raster.adopt(Image.fromarray(rgba, "RGBA")), raster, filename)
    return modifier, raster, filename


def add(raster, filename, name, rgba, key=None):
    return {
        "type": "add", "internal_key": key or name, "atlas_name": name,
        "source": raster.adopt(Image.fromarray(rgba, "RGBA")), "page": filename,
    }


def pixels(raster, result):
    w, h = raster.size(result.image)
    return np.frombuffer(raster.get_pixels(result.image), np.uint8).reshape(h, w, 4)


def regions(result):
    return {r.name: (r.x, r.y, r.w, r.h, r.rotate) for r in result.document.pages[0].regions}


def remove(key):
    return {"type": "remove", "target_key": key}


def run(modifier, batches, mesh=None, gap=1, sprites=None, pieces=None):
    return modifier.place_in_place(
        batches=batches, modded_sprites=sprites or {}, pieces=pieces or {},
        mesh_lookup_fn=(mesh.get if mesh is not None else None), gap=gap,
    )


TWO_FULL = "p.png\nsize: 32,16\nA\n bounds: 0,0,16,16\nB\n bounds: 16,0,16,16\n"


def test_remove_vacates_and_keeps_the_rest():
    rgba = page_rgba(32, 16, [(0, 0, 16, 16), (16, 0, 16, 16)])
    modifier, raster, _ = setup(TWO_FULL, rgba)
    result = run(modifier, [remove("B")])
    out = pixels(raster, result)
    assert out.shape == (16, 32, 4)
    assert (out[:, 16:] == 0).all()
    assert (out[:, :16] == rgba[:, :16]).all()
    assert regions(result) == {"A": (0, 0, 16, 16, 0)}


def test_add_reuses_vacated_space_mesh_off():
    # Spec I-1: the stray term is read after vacating, so B's old pixels are free.
    rgba = page_rgba(32, 16, [(0, 0, 16, 16), (16, 0, 16, 16)])
    modifier, raster, filename = setup(TWO_FULL, rgba)
    result = run(modifier, [remove("B"), add(raster, filename, "N", pattern(6, 6, 3))])
    x, y, w, h, rotate = regions(result)["N"]
    assert x >= 16 and raster.size(result.image) == (32, 16)


def test_no_add_keeps_a_size_that_is_not_a_multiple_of_4():
    text = "p.png\nsize: 30,18\nA\n bounds: 0,0,10,10\nB\n bounds: 12,0,10,10\n"
    modifier, raster, _ = setup(text, page_rgba(30, 18, [(0, 0, 10, 10), (12, 0, 10, 10)]))
    assert raster.size(run(modifier, [remove("B")]).image) == (30, 18)


def test_removing_one_alias_keeps_the_shared_pixels():
    text = "p.png\nsize: 32,16\nA\n bounds: 0,0,16,16\nA2\n bounds: 0,0,16,16\nB\n bounds: 16,0,16,16\n"
    rgba = page_rgba(32, 16, [(0, 0, 16, 16), (16, 0, 16, 16)])
    modifier, raster, _ = setup(text, rgba)
    out = pixels(raster, run(modifier, [remove("A2")]))
    assert (out == rgba).all()


def test_vacate_keeps_a_neighbours_hull_inside_the_removed_rect():
    # A's rect overlaps B's; B is hulled (lower-right) and survives: its hull pixels stay.
    text = "p.png\nsize: 24,16\nA\n bounds: 0,0,16,16\nB\n bounds: 8,0,16,16\n"
    rgba = page_rgba(24, 16, [(0, 0, 24, 16)])
    modifier, raster, _ = setup(text, rgba)
    out = pixels(raster, run(modifier, [remove("A")], mesh={"B": LOWER_RIGHT}))
    from atlas_toolkit.engine.modifier import mesh_mask_grid
    b_hull = mesh_mask_grid(raster, LOWER_RIGHT, None, 16, 16)  # B's hull over its own rect
    region = out[:, 8:24]
    assert (region[b_hull & (np.arange(16)[None, :] < 8)][:, 3] > 0).all()   # inside A's old rect, on B's hull
    assert (out[:, :8, 3] == 0).all()                                           # A's rect outside B's rect: cleared



def test_patch_delegates_with_identical_bytes():
    rgba = page_rgba(64, 32, [(0, 0, 16, 16), (16, 0, 16, 16)])
    text = "p.png\nsize: 64,32\nA\n bounds: 0,0,16,16\nB\n bounds: 16,0,16,16\n"
    a, raster_a, f = setup(text, rgba)
    b, raster_b, _ = setup(text, rgba)
    one = a.patch(batches=[add(raster_a, f, "N", pattern(10, 10, 4))], mesh_lookup_fn=None, gap=2)
    two = run(b, [add(raster_b, f, "N", pattern(10, 10, 4))], gap=2)
    assert (pixels(raster_a, one) == pixels(raster_b, two)).all()
    assert one.document.serialize() == two.document.serialize()


def test_each_group_is_masked_by_its_own_hull():
    text = "p.png\nsize: 32,16\nA\n bounds: 0,0,16,16\nB\n bounds: 16,0,16,16\n"
    modifier, raster, _ = setup(text, page_rgba(32, 16, [(0, 0, 32, 16)]))
    sprite = raster.adopt(Image.fromarray(pattern(16, 16, 9), "RGBA"))
    batch = {"type": "mod", "names": ["A", "B"], "source": sprite, "is_full_canvas": True, "composite": True}
    mesh = {"A": UPPER_LEFT, "B": LOWER_RIGHT}
    out = pixels(raster, run(modifier, [batch], mesh=mesh, sprites={"A": sprite, "B": sprite}))
    from atlas_toolkit.engine.modifier import mesh_mask_grid
    a_hull = mesh_mask_grid(raster, UPPER_LEFT, None, 16, 16)
    assert (out[:, :16][~a_hull][:, 3] == 0).all()     # nothing of B's hull inside A's rect
    assert (out[:, :16][a_hull][:, 3] == 255).all()


def test_removing_a_meshed_alias_keeps_the_whole_group_rect():
    # The surviving group's rect is not vacated, even outside its own hull.
    text = "p.png\nsize: 32,16\nA\n bounds: 0,0,16,16\nA2\n bounds: 0,0,16,16\nB\n bounds: 16,0,16,16\n"
    rgba = page_rgba(32, 16, [(0, 0, 16, 16), (16, 0, 16, 16)])
    modifier, raster, _ = setup(text, rgba)
    out = pixels(raster, run(modifier, [remove("A2")], mesh={"A": UPPER_LEFT, "A2": LOWER_RIGHT, "B": LOWER_RIGHT}))
    assert (out == rgba).all()


@pytest.mark.parametrize("rotate", [90, 180, 270])
def test_remove_clears_a_rotated_stored_rect(rotate):
    text = f"p.png\nsize: 32,16\nA\n bounds: 0,0,16,16\nB\n bounds: 16,0,12,16\n rotate: {rotate}\n"
    sw, sh = (16, 12) if rotate in (90, 270) else (12, 16)
    rgba = page_rgba(32, 16, [(0, 0, 32, 16)])
    modifier, raster, _ = setup(text, rgba)
    out = pixels(raster, run(modifier, [remove("B")]))
    expected = rgba.copy()
    expected[0:sh, 16:16 + sw] = 0
    assert (out == expected).all()
