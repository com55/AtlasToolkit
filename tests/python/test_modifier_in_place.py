"""AtlasModifier.patch: place adds into a page's free space (spec 2026-10-31 §5)."""
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


def test_a_modded_group_refuses_for_now():
    rgba = page_rgba(32, 16, [(0, 0, 16, 16), (16, 0, 16, 16)])
    modifier, raster, _ = setup(TWO_FULL, rgba)
    sprite = raster.adopt(Image.fromarray(pattern(16, 16, 9), "RGBA"))
    batch = {"type": "mod", "names": ["A"], "source": sprite, "is_full_canvas": True, "composite": False}
    assert run(modifier, [batch], sprites={"A": sprite}) is None


def test_patch_delegates_with_identical_bytes():
    rgba = page_rgba(64, 32, [(0, 0, 16, 16), (16, 0, 16, 16)])
    text = "p.png\nsize: 64,32\nA\n bounds: 0,0,16,16\nB\n bounds: 16,0,16,16\n"
    a, raster_a, f = setup(text, rgba)
    b, raster_b, _ = setup(text, rgba)
    one = a.patch(batches=[add(raster_a, f, "N", pattern(10, 10, 4))], mesh_lookup_fn=None, gap=2)
    two = run(b, [add(raster_b, f, "N", pattern(10, 10, 4))], gap=2)
    assert (pixels(raster_a, one) == pixels(raster_b, two)).all()
    assert one.document.serialize() == two.document.serialize()
