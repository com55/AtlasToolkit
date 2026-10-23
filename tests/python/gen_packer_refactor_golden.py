"""Golden outputs for the build(packer=...) refactor.

Packs at the session layer -- the same setters + pack_pending the
pre-refactor SpineAtlas._pack used -- so it runs both before and after the
refactor; the fixture was written at the commit before the refactor.

    uv run python tests/python/gen_packer_refactor_golden.py          # write
    uv run python tests/python/gen_packer_refactor_golden.py --check  # compare
"""
from __future__ import annotations

import hashlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from PIL import Image

import atlas_toolkit.api as api
from atlas_toolkit.api import SpineAtlas

FIXTURE = Path(__file__).with_name("fixtures") / "packer_refactor_golden.json"


def _tri(uvs: list[float]) -> dict:
    return {"type": "Mesh", "uvs": uvs, "triangles": [0, 1, 2], "hull_length": 3}


UPPER_LEFT = [0, 0, 1, 0, 0, 1]
LOWER_RIGHT = [1, 0, 1, 1, 0, 1]
MESH = {
    "T1": _tri(UPPER_LEFT), "T2": _tri(LOWER_RIGHT),
    "T3": _tri(UPPER_LEFT), "T4": _tri(LOWER_RIGHT),
    "CA": _tri(UPPER_LEFT), "CC": _tri([0, 0, 1, 0, 1, 1]),
    "P1": _tri(UPPER_LEFT), "P2": _tri(LOWER_RIGHT),
}
PAGE_ONE = (
    "page.png\nsize: 256,128\n"
    "T1\n bounds: 0,0,40,40\n"
    "T2\n bounds: 40,0,40,40\n"
    "T3\n bounds: 80,0,40,40\n"
    "T4\n bounds: 120,0,40,40\n"
    "CA\n bounds: 0,40,30,20\n"
    "CB\n bounds: 30,40,20,20\n"
    "CC\n bounds: 50,40,24,16\n offsets: 4,2,32,20\n"
    "R\n bounds: 80,40,12,10\n"
    "Ren\n bounds: 100,40,10,10\n"
    "Gone\n bounds: 120,40,8,8\n"
)
PAGE_TWO = (
    "\npage2.png\nsize: 64,32\n"
    "P1\n bounds: 0,0,24,24\n"
    "P2\n bounds: 24,0,24,24\n"
)
LAYOUTS = {"single": PAGE_ONE, "multi": PAGE_ONE + PAGE_TWO}
# old flags: (nest, gap, mesh_aware)
FLAGS = {
    "shelf": (False, None, False),
    "shelf-mesh": (False, None, True),
    "nest4": (True, 4, False),
    "nest4-mesh": (True, 4, True),
    "nest7": (True, 7, False),
    "nest7-mesh": (True, 7, True),
}


def pattern(width: int, height: int, seed: int) -> Image.Image:
    """Every pixel encodes its own position (and the seed)."""
    ys, xs = np.mgrid[0:height, 0:width]
    rgba = np.zeros((height, width, 4), np.uint8)
    rgba[..., 0] = (xs * 5 + seed) % 256
    rgba[..., 1] = (ys * 7 + seed * 3) % 256
    rgba[..., 2] = (xs * 11 + ys * 13 + seed) % 256
    rgba[..., 3] = 255
    return Image.fromarray(rgba, "RGBA")


@contextmanager
def fake_skeleton():
    original = api._parse_skeleton
    api._parse_skeleton = lambda data: {"attachments": MESH}
    try:
        yield
    finally:
        api._parse_skeleton = original


def edited_atlas(layout: str, skeleton: bool = True) -> SpineAtlas:
    """Every edit on its own regions, all on page.png (spec §7.1): a later
    replace, a remove or an added key inside the composite group would make
    the split axis inert."""
    pages = {"page.png": pattern(256, 128, 1)}
    if layout == "multi":
        pages["page2.png"] = pattern(64, 32, 2)
    if skeleton:
        with fake_skeleton():
            atlas = SpineAtlas.load(LAYOUTS[layout], pages, skeleton=b"x")
    else:
        atlas = SpineAtlas.load(LAYOUTS[layout], pages)
    atlas.replace({"R": pattern(12, 10, 9)})
    atlas.add_region("N", pattern(14, 9, 5), page="page.png")
    atlas.rename_region("Ren", "Ren2")
    atlas.remove_region("Gone")
    # CA has mesh, CB has none, CC has offsets.
    atlas.replace_composite(["CA", "CB", "CC"], pattern(40, 30, 3))
    return atlas


def session_pack(atlas: SpineAtlas, nest: bool, gap: int | None, mesh_aware: bool, split: bool,
                 **extras) -> tuple[str, dict]:
    """Exactly what the pre-refactor SpineAtlas._pack did. `extras` are the
    refactor's new pack_pending keywords (none exist before it)."""
    session = atlas._session
    try:
        session.set_nest_regions_enabled(nest)
        if nest:
            session.set_nest_gap_distance(gap)
        session.set_mesh_aware_repack_enabled(mesh_aware)
        return session.pack_pending(split_composite=split, **extras)
    finally:
        session.set_nest_regions_enabled(False)
        session.set_nest_gap_distance(4)
        session.set_mesh_aware_repack_enabled(False)


def summarize(text: str, pages: dict) -> dict:
    built = SpineAtlas.load(text, pages)
    return {
        "text": text,
        "regions": {r.name: [*r.bounds, r.rotate] for r in built.regions},
        "pages": {
            name: [*image.size, hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest()]
            for name, image in built.pages.items()
        },
    }


def case_id(layout: str, flag: str, split: bool) -> str:
    return f"{layout}/{flag}/{'split' if split else 'nosplit'}"


def build_cases() -> dict[str, dict]:
    cases = {}
    for layout in LAYOUTS:
        for flag, (nest, gap, mesh_aware) in FLAGS.items():
            for split in (False, True):
                atlas = edited_atlas(layout)
                cases[case_id(layout, flag, split)] = summarize(*session_pack(atlas, nest, gap, mesh_aware, split))
    return cases


def partition(case: dict) -> list[list[str]]:
    """Regions sharing one packed rect: the dedup groups."""
    groups: dict[tuple, list[str]] = {}
    for name, (x, y, w, h, _rotate) in case["regions"].items():
        groups.setdefault((x, y, w, h), []).append(name)
    return sorted(sorted(group) for group in groups.values())


def self_check(cases: dict[str, dict]) -> None:
    """Refuse a fixture whose axes would check nothing (spec §7.1)."""
    footprint_visible = False
    for layout in LAYOUTS:
        for gap in (4, 7):
            for split in (False, True):
                mesh = cases[case_id(layout, f"nest{gap}-mesh", split)]
                rect = cases[case_id(layout, f"nest{gap}", split)]
                moved = any(mesh["regions"][n][:2] != rect["regions"][n][:2] for n in mesh["regions"])
                if moved and partition(mesh) == partition(rect):
                    footprint_visible = True
        for flag in FLAGS:
            off = cases[case_id(layout, flag, False)]
            on = cases[case_id(layout, flag, True)]
            assert off["pages"] != on["pages"], f"split axis inert for {layout}/{flag}"
        for split in (False, True):
            plain = cases[case_id(layout, "shelf", split)]
            masked = cases[case_id(layout, "shelf-mesh", split)]
            assert plain["pages"] != masked["pages"], f"shelf mesh axis inert for {layout}"
    assert footprint_visible, "no equal-partition case where the mesh footprint moves a region"


def main(argv: list[str]) -> int:
    cases = build_cases()
    self_check(cases)
    if "--check" in argv:
        expected = json.loads(FIXTURE.read_text())
        bad = [key for key in expected if cases.get(key) != expected[key]]
        print("mismatch:", bad) if bad else print(f"ok: {len(expected)} cases")
        return 1 if bad else 0
    FIXTURE.write_text(json.dumps(cases, indent=1, sort_keys=True) + "\n")
    print(f"wrote {len(cases)} cases to {FIXTURE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
