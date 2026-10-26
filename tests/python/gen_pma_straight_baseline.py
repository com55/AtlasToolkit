"""Straight-page outputs pinned at 54b9ace for the pma mesh-aware work
(spec 2026-10-26 §5.8): the straight page of a mixed multi-page atlas, a
mixed force_resize composite, and the straight piece of a mixed
cross-page split under rect ownership. Regenerate ONLY from 54b9ace.

    uv run python tests/python/gen_pma_straight_baseline.py          # write
    uv run python tests/python/gen_pma_straight_baseline.py --check  # compare
"""
from __future__ import annotations

import hashlib
import json
import sys
import warnings
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from PIL import Image

import atlas_toolkit.api as api
from atlas_toolkit.api import SpineAtlas
from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.packer import PackerOptions

FIXTURE = Path(__file__).with_name("fixtures") / "pma_straight_baseline.json"
QUAD = [0, 1, 2, 0, 2, 3]


def _mesh(u0, v0, u1, v1):
    return {"type": "Mesh", "uvs": [u0, v0, u1, v0, u1, v1, u0, v1], "triangles": QUAD, "hull_length": 4}


MESHES = {name: _mesh(0, 0, 0.5, 1) for name in ("G", "N", "S1", "S2")}
MULTI = (
    "pma.png\nsize: 32,8\npma: true\n"
    "G\n bounds: 0,0,16,8\n"
    "N\n bounds: 16,0,16,8\n"
    "\nst.png\nsize: 32,8\n"
    "S1\n bounds: 0,0,16,8\n"
    "S2\n bounds: 16,0,16,8\n"
)
# Same 16x8 canvas on both pages, no mesh: rect ownership.
CROSS = (
    "pma.png\nsize: 16,8\npma: true\n"
    "P\n bounds: 0,0,16,8\n"
    "\nst.png\nsize: 16,8\n"
    "S\n bounds: 0,0,16,8\n"
)
# Different canvas sizes so force_resize scales one of them.
SCALED = (
    "pma.png\nsize: 16,8\npma: true\n"
    "P\n bounds: 0,0,8,4\n"
    "\nst.png\nsize: 16,8\n"
    "S\n bounds: 0,0,12,6\n"
)


def pattern(width, height, seed, pma):
    ys, xs = np.mgrid[0:height, 0:width]
    arr = np.zeros((height, width, 4), np.uint8)
    arr[..., 0] = (xs * 13 + seed * 7) % 256
    arr[..., 1] = (ys * 29 + seed * 3) % 256
    arr[..., 2] = (xs * 5 + ys * 11 + seed) % 256
    arr[..., 3] = np.array([255, 200, 128, 64, 255, 30], np.uint8)[(xs + 3 * ys + seed) % 6]
    if pma:
        arr[..., :3] = np.minimum(arr[..., :3], arr[..., 3:4])
    return Image.fromarray(arr, "RGBA")


@contextmanager
def fake_skeleton():
    original = api._parse_skeleton
    api._parse_skeleton = lambda data: {"attachments": MESHES}
    try:
        yield
    finally:
        api._parse_skeleton = original


def digest(image: Image.Image) -> list:
    return [*image.size, hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest()]


def page_section(text: str, filename: str) -> str:
    doc = AtlasDocument.parse(text)
    return AtlasDocument([p for p in doc.pages if p.filename == filename]).serialize()


def multi_straight_page(packer: str, options: PackerOptions, skeleton: bool) -> dict:
    pages = {"pma.png": pattern(32, 8, 1, True), "st.png": pattern(32, 8, 2, False)}
    if skeleton:
        with fake_skeleton():
            atlas = SpineAtlas.load(MULTI, pages, skeleton=b"x")
    else:
        atlas = SpineAtlas.load(MULTI, pages)
    atlas.replace({"G": pattern(16, 8, 3, True), "S1": pattern(16, 8, 4, False)})
    built = atlas.build(packer=packer, packer_options=options)
    return {"text": page_section(built.atlas_text, "st.png"), "page": digest(built.pages["st.png"])}


def mixed_force_resize() -> dict:
    atlas = SpineAtlas.load(SCALED, {"pma.png": pattern(16, 8, 5, True), "st.png": pattern(16, 8, 6, False)})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return {"composite": digest(atlas.extract_composite(["P", "S"], force_resize=True))}


def mixed_split_rect() -> dict:
    atlas = SpineAtlas.load(CROSS, {"pma.png": pattern(16, 8, 7, True), "st.png": pattern(16, 8, 8, False)})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        atlas.replace_composite(["P", "S"], pattern(16, 8, 9, False))
    built = atlas.build(split_composite=True, packer="shelf", packer_options=PackerOptions(mesh_aware=False))
    return {"text": page_section(built.atlas_text, "st.png"), "page": digest(built.pages["st.png"])}


CASES = {
    "multi_shelf_mesh": lambda: multi_straight_page("shelf", PackerOptions(mesh_aware=True), True),
    "multi_rectangles_mesh": lambda: multi_straight_page("rectangles", PackerOptions(mesh_aware=True), True),
    "multi_polygons_mesh": lambda: multi_straight_page("polygons", PackerOptions(padding=2), True),
    "multi_shelf_rect": lambda: multi_straight_page("shelf", PackerOptions(mesh_aware=False), False),
    "mixed_force_resize": mixed_force_resize,
    "mixed_split_rect": mixed_split_rect,
}


def generate() -> dict:
    return {name: case() for name, case in CASES.items()}


if __name__ == "__main__":
    data = generate()
    if "--check" in sys.argv:
        want = json.loads(FIXTURE.read_text())
        bad = [name for name in data if data[name] != want.get(name)]
        print("differ:", bad or "none")
        sys.exit(1 if bad else 0)
    FIXTURE.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    print(f"wrote {FIXTURE} ({len(data)} cases)")
