"""Extended Python reference-oracle ground truth for Task 5 (Phase F partial).

PINNED ORACLE COMMIT: main @ 9655e3c (confirm with `git rev-parse --short main`;
atlas_toolkit/ is byte-identical between main and dev at time of writing, but
dev moves -- this script always diffs against the pinned SHA via
`git show <sha>:<path>`, never a moving branch ref).

Execs the REAL main-branch atlas_toolkit/core/document.py,
atlas_toolkit/core/region_ops.py, atlas_toolkit/atlas/repacker.py and
atlas_toolkit/atlas/modifier.py (stubbing only their internal
`atlas_toolkit.*` cross-imports, since all four are exec'd into one shared
namespace -- same pattern as gen-ground-truth.py already uses for
region_ops.py alone) against:
  - the two checked-in Deliverable-1 fixtures (test/browser/fixtures/), and
  - small hand-picked synthetic scenarios for merge (full-canvas /
    offset-padded / rotated-placement) and repack (single-page dedup,
    multi-page no-dedup), and
  - real-world spot-check data pulled from .workspaces/group_*/**/*.atlas
    (untracked, not part of the repo -- see NOTE below on multi-page).

Dumps decoded RGBA pixel arrays (not PNG bytes -- PIL vs Canvas PNG encoders
differ byte-for-byte even on pixel-identical images) plus atlas text, to
ground_truth_ops.json. The browser harness (verify-ops.mjs) then runs the
REAL browser Canvas 2D + AtlasModifier/AtlasDocument code in www/js/ against
the same inputs and asserts pixel/text parity.

NOTE on multi-page real-world data: the brief asks for "one or two
representative multi-page atlases with rotated regions" from
.workspaces/group_*/**/*.atlas. A scan of all 341 .atlas files found in this
environment at generation time (see task-5-report.md) found ZERO multi-page
atlases (every file has exactly one "*.png" page header) -- Blue Archive
sprite exports here are all single-page. Multi-page REPACK coverage does not
require multi-page INPUT (repack_multi_page takes any sprite pool and
distributes it across N new output pages), so the real-world multi-page-
repack spot check below takes a real single-page atlas's sprites and
redistributes them across 2 synthetic output pages -- still real, messy,
anti-aliased pixel data exercising the actual no-dedup code path, just not
sourced from a pre-existing multi-page atlas (none exist locally).

Run from the repo (needs Pillow + git):
  python3 test/browser/gen-ground-truth-ops.py
"""

import base64
import io
import json
import logging
import os
import subprocess
from pathlib import Path

from PIL import Image

logging.disable(logging.CRITICAL)  # silence the modules' own INFO logging

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
FIXTURES = os.path.join(HERE, "fixtures")
PINNED_SHA = "9655e3c"

actual_sha = subprocess.check_output(
    ["git", "-C", REPO, "rev-parse", "--short", "main"], text=True
).strip()
if actual_sha != PINNED_SHA:
    print(
        f"NOTE: main has moved to {actual_sha} since this script was written "
        f"(pinned {PINNED_SHA}); diffing against the pinned SHA regardless, "
        "per the brief."
    )


def show(path):
    return subprocess.check_output(
        ["git", "-C", REPO, "show", f"{PINNED_SHA}:{path}"], text=True
    )


# ─── Load the REAL main-branch modules into one shared namespace ──────────
ns = {}
ns.update(Image=Image)

doc_src = show("atlas_toolkit/core/document.py")
exec(compile(doc_src, "document.py", "exec"), ns)

ops_src = show("atlas_toolkit/core/region_ops.py")
ops_src = ops_src.replace(
    "from atlas_toolkit.core.document import Page, Region", ""
)
exec(compile(ops_src, "region_ops.py", "exec"), ns)

repacker_src = show("atlas_toolkit/atlas/repacker.py")
repacker_src = repacker_src.replace(
    "from atlas_toolkit.core.document import AtlasDocument, Page, Region", ""
).replace(
    "from atlas_toolkit.core.region_ops import extract_raw_sprite, round_up_to_multiple",
    "",
)
exec(compile(repacker_src, "repacker.py", "exec"), ns)

modifier_src = show("atlas_toolkit/atlas/modifier.py")
modifier_src = (
    modifier_src.replace(
        "from atlas_toolkit.core.document import (\n    AtlasDocument,\n    Region,\n    UpdatedRegionData,\n)",
        "",
    )
    .replace(
        "from atlas_toolkit.atlas.repacker import repack_from_sprites, repack_single_page",
        "",
    )
    .replace(
        "from atlas_toolkit.core.region_ops import extract_raw_sprite, round_up_to_multiple",
        "",
    )
)
exec(compile(modifier_src, "modifier.py", "exec"), ns)

AtlasDocument = ns["AtlasDocument"]
Region = ns["Region"]
Page = ns["Page"]
extract_raw_sprite = ns["extract_raw_sprite"]
crop_and_rotate = ns["crop_and_rotate"]
extract_region_from_page = ns["extract_region_from_page"]
repack_single_page = ns["repack_single_page"]
repack_from_sprites = ns["repack_from_sprites"]
repack_multi_page = ns["repack_multi_page"]
AtlasModifier = ns["AtlasModifier"]

print(f"Loaded real main@{PINNED_SHA} document/region_ops/repacker/modifier into shared namespace")


# ─── Helpers ────────────────────────────────────────────────────────────────

def grid(img: "Image.Image"):
    """Compact grid encoding: base64 of raw row-major RGBA bytes (4 bytes/px),
    NOT a nested pixel array -- the nested-array JSON encoding of a handful of
    real-world sprites (up to ~280x190px each) bloated ground_truth_ops.json
    to 5+MB; raw bytes + base64 keeps it small and checked-in-friendly. See
    verify-ops.mjs's `decodeGrid` for the matching decoder."""
    img = img.convert("RGBA")
    return {"w": img.width, "h": img.height, "b64": base64.b64encode(img.tobytes()).decode("ascii")}


def parse_page_info(atlas_text):
    doc = AtlasDocument.parse(atlas_text)
    return doc.first_page_info(), doc.region_keys(), doc.regions_by_key()


cases = []
realworld_cases = []

# ═════════════════════════════════════════════════════════════════════════
# Deliverable 1 fixture round-trips through the REAL app-level Python calls
# (AtlasDocument.parse -> extract_region_from_page), to prove the checked-in
# fixtures actually exercise the real parse path, not just raw pixel math.
# ═════════════════════════════════════════════════════════════════════════

opaque_dir = os.path.join(FIXTURES, "opaque-transparent")
with open(os.path.join(opaque_dir, "fixture-opaque.atlas")) as f:
    opaque_atlas_text = f.read()
opaque_img = Image.open(os.path.join(opaque_dir, "fixture-opaque.png")).convert("RGBA")

_, _, opaque_regions = parse_page_info(opaque_atlas_text)

for name in ("plain", "withOffsets", "dupeA", "dupeB"):
    region = opaque_regions[name]
    out = extract_region_from_page(opaque_img, region, None)
    cases.append({
        "name": f"fixture-opaque: extract '{name}' via real AtlasDocument.parse path (exact, binary alpha)",
        "op": "extractRegionFromPage",
        "exact": True,
        "source": grid(opaque_img),
        "args": {
            "x": region.x, "y": region.y, "w": region.w, "h": region.h,
            "rotate": region.rotate,
            "offsets": list(region.offsets) if region.offsets else None,
            "scaleX": 1.0, "scaleY": 1.0,
        },
        "expected": grid(out),
    })

tie_dir = os.path.join(FIXTURES, "tie-rounding")
with open(os.path.join(tie_dir, "fixture-tie.atlas")) as f:
    tie_atlas_text = f.read()
tie_img = Image.open(os.path.join(tie_dir, "fixture-tie.png")).convert("RGBA")
tie_page_info, _, tie_regions = parse_page_info(tie_atlas_text)
tie_region = tie_regions["tieRegion"]
# Declared page 14x14, real image 21x21 -> scale 1.5 (mirrors
# AtlasProcessor.loadImages' real-vs-declared-size scale detection).
scale_x = tie_img.width / 14
scale_y = tie_img.height / 14
assert (scale_x, scale_y) == (1.5, 1.5)
tie_out = extract_region_from_page(tie_img, tie_region, Page(filename="fixture-tie.png", scale_x=scale_x, scale_y=scale_y))
assert tie_out.size == (10, 10), (
    f"expected the .5-tie fixture to resolve to a 10x10 canvas (Python "
    f"round(7*1.5)==10); got {tie_out.size} -- fixture no longer discriminates!"
)
cases.append({
    "name": "fixture-tie: extract 'tieRegion' via real path, scale=1.5 forces roundHalfEven .5-tie "
            "(orig_w/orig_h 7*1.5=10.5 -> 10, even-floor tie; Math.round would give 11)",
    "op": "extractRegionFromPage",
    "exact": True,
    "source": grid(tie_img),
    "args": {
        "x": tie_region.x, "y": tie_region.y, "w": tie_region.w, "h": tie_region.h,
        "rotate": tie_region.rotate,
        "offsets": list(tie_region.offsets),
        "scaleX": scale_x, "scaleY": scale_y,
    },
    "expected": grid(tie_out),
})

print(f"Fixture round-trip cases: {len(cases)}")

# ═════════════════════════════════════════════════════════════════════════
# Plain extract: default-offsets case (region_ops coverage gap check --
# existing ground_truth.json only has offset-bearing extractRegionFromPage
# cases; this adds the offsets=None / no-padding path).
# ═════════════════════════════════════════════════════════════════════════

default_img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
for yy in range(10):
    for xx in range(10):
        a = 255 if (xx + yy) % 3 else 0
        default_img.putpixel((xx, yy), (xx * 25 % 256, yy * 25 % 256, 77, a) if a else (0, 0, 0, 0))
default_region = Region(name="d", atlas_name="d", page_filename="p.png", x=1, y=1, w=6, h=5, offsets=None, rotate=0)
default_out = extract_region_from_page(default_img, default_region, None)
cases.append({
    "name": "extractRegionFromPage: default offsets (offsets=None) returns the bare crop, no padding canvas",
    "op": "extractRegionFromPage",
    "exact": True,
    "source": grid(default_img),
    "args": {"x": 1, "y": 1, "w": 6, "h": 5, "rotate": 0, "offsets": None, "scaleX": 1.0, "scaleY": 1.0},
    "expected": grid(default_out),
})

print(f"Extract cases so far (fixtures + default-offsets): {len(cases)}")

# ═════════════════════════════════════════════════════════════════════════
# Merge scenarios: full-canvas / offset-padded / rotated-placement.
# All synthetic pixel content is binary-alpha (0/255) so exact-equality
# comparisons are safe even through canvas drawImage compositing (see
# gen-fixtures.py header comment / task-5-report.md for the premultiply
# rationale).
# ═════════════════════════════════════════════════════════════════════════

def checker(w, h, seed=0):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for yy in range(h):
        for xx in range(w):
            if (xx + yy + seed) % 3 == 0:
                img.putpixel((xx, yy), (0, 0, 0, 0))
            else:
                img.putpixel((xx, yy), ((xx * 17 + seed * 40) % 256, (yy * 23 + seed * 20) % 256, 60 + seed * 30, 255))
    return img


def mod_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


merge_cases = []

# --- Scenario B: full-canvas merge (mod dims == resolved canvas trivially,
#     because the selected region has no offsets) ---------------------------
base_b = checker(12, 8, seed=1)
mod_b = checker(10, 3, seed=2)
atlas_b = (
    "base.png\n"
    "size: 12, 8\n"
    "format: RGBA8888\n"
    "filter: Nearest, Nearest\n"
    "repeat: none\n"
    "base1\n"
    "bounds: 0, 0, 12, 8\n"
)
mB = AtlasModifier(atlas_b, Path("dummy.atlas"), base_b)
merged_b, text_b = mB.merge_mod_image(mod_bytes(mod_b), ["base1"])
merge_cases.append({
    "name": "mergeModImage: full-canvas merge (no-offsets region, mod trivially matches canvas, expect 'below' non-rotated)",
    "op": "mergeModImage",
    "exact": True,
    "atlasText": atlas_b,
    "baseImage": grid(base_b),
    "modImage": grid(mod_b),
    "selectedRegions": ["base1"],
    "expectedCanvas": grid(merged_b),
    "expectedAtlasText": text_b,
})

# --- Scenario C: offset-padded merge (mod smaller than resolved canvas,
#     needs anchor-based padding; expect 'right' non-rotated on a tie) -----
base_c = checker(20, 20, seed=3)
mod_c = checker(25, 15, seed=4)
atlas_c = (
    "base.png\n"
    "size: 20, 20\n"
    "format: RGBA8888\n"
    "filter: Nearest, Nearest\n"
    "repeat: none\n"
    "spriteR\n"
    "bounds: 0, 0, 8, 8\n"
    "offsets: 5, 2, 40, 20\n"
)
mC = AtlasModifier(atlas_c, Path("dummy.atlas"), base_c)
merged_c, text_c = mC.merge_mod_image(mod_bytes(mod_c), ["spriteR"])
merge_cases.append({
    "name": "mergeModImage: offset-padded merge (mod 25x15 padded into 40x20 canvas at anchor (5,3) via Y-flip, then placed 'right')",
    "op": "mergeModImage",
    "exact": True,
    "atlasText": atlas_c,
    "baseImage": grid(base_c),
    "modImage": grid(mod_c),
    "selectedRegions": ["spriteR"],
    "expectedCanvas": grid(merged_c),
    "expectedAtlasText": text_c,
})

# --- Scenario D: rotated-placement merge (findBestPlacement picks a
#     rotated candidate: base 10x40, mod 30x5 -> 'right+rotated' uniquely
#     wins by area: 600 vs 1600/1350/700) --------------------------------
base_d = checker(10, 40, seed=5)
mod_d = checker(30, 5, seed=6)
atlas_d = (
    "base.png\n"
    "size: 10, 40\n"
    "format: RGBA8888\n"
    "filter: Nearest, Nearest\n"
    "repeat: none\n"
    "base1\n"
    "bounds: 0, 0, 5, 5\n"
)
mD = AtlasModifier(atlas_d, Path("dummy.atlas"), base_d)
merged_d, text_d = mD.merge_mod_image(mod_bytes(mod_d), ["base1"])
assert "rotate: true" in text_d.lower() or "rotate:true" in text_d.lower(), (
    f"expected scenario D to pick a rotated placement (rotate: true in output); "
    f"got:\n{text_d}"
)
merge_cases.append({
    "name": "mergeModImage: rotated-placement merge (base 10x40, mod 30x5 -> 'right+rotated' uniquely smallest area 600 vs 1600/1350/700)",
    "op": "mergeModImage",
    "exact": True,
    "atlasText": atlas_d,
    "baseImage": grid(base_d),
    "modImage": grid(mod_d),
    "selectedRegions": ["base1"],
    "expectedCanvas": grid(merged_d),
    "expectedAtlasText": text_d,
})

print(f"Merge scenario cases: {len(merge_cases)} (B=full-canvas, C=offset-padded, D=rotated-placement)")


# ═════════════════════════════════════════════════════════════════════════
# Repack scenarios: single-page dedup (fixture-opaque, has dupeA/dupeB) and
# multi-page no-dedup (same fixture, sprites spread across 2 pages).
# ═════════════════════════════════════════════════════════════════════════

repack_cases = []

# --- Scenario E: single-page repack, confirm dedup (dupeA/dupeB collapse) --
single_result = repack_single_page(opaque_img, opaque_atlas_text, deduplicate=True)
_, e_region_names, e_regions = parse_page_info(single_result.atlas_text)
dupeA_bounds = (e_regions["dupeA"].x, e_regions["dupeA"].y, e_regions["dupeA"].w, e_regions["dupeA"].h)
dupeB_bounds = (e_regions["dupeB"].x, e_regions["dupeB"].y, e_regions["dupeB"].w, e_regions["dupeB"].h)
assert dupeA_bounds == dupeB_bounds, (
    f"expected single-page repack to dedup dupeA/dupeB to identical bounds; "
    f"got dupeA={dupeA_bounds} dupeB={dupeB_bounds}"
)
repack_cases.append({
    "name": "repackSinglePage(fixture-opaque): dedup collapses pixel-identical dupeA/dupeB to one packed instance",
    "op": "repackSinglePage",
    "exact": True,
    "atlasText": opaque_atlas_text,
    "baseImage": grid(opaque_img),
    "expectedCanvas": grid(single_result.image),
    "expectedAtlasText": single_result.atlas_text,
    "assertDedupBoundsEqual": ["dupeA", "dupeB"],
})

# --- Scenario F: multi-page repack, confirm NO dedup (dupeA/dupeB both
#     appear as distinct placements even though pixel-identical) ----------
sprites_f = {
    name: extract_raw_sprite(opaque_img, e_regions_src)
    for name, e_regions_src in parse_page_info(opaque_atlas_text)[2].items()
}
page_infos_f = [
    {"page": "page0.png", "format": "RGBA8888", "filter": "Nearest,Nearest", "repeat": "none", "pma": False},
    {"page": "page1.png", "format": "RGBA8888", "filter": "Nearest,Nearest", "repeat": "none", "pma": False},
]
region_metas_f = {
    name: r.to_meta_dict() for name, r in parse_page_info(opaque_atlas_text)[2].items()
}
multi_pages, multi_text = repack_multi_page(sprites_f, 2, page_infos_f, region_metas_f)
_, f_region_names, f_regions = parse_page_info(multi_text)
assert "dupeA" in f_regions and "dupeB" in f_regions, "expected both dupeA and dupeB present after multi-page repack (no dedup)"
repack_cases.append({
    "name": "repackMultiPage(fixture-opaque sprites, 2 pages): NO dedup -- dupeA/dupeB both present as separate placements",
    "op": "repackMultiPage",
    "exact": True,
    "spriteNames": list(sprites_f.keys()),
    "sprites": {n: grid(s) for n, s in sprites_f.items()},
    "numPages": 2,
    "pageInfos": page_infos_f,
    "regionMetas": {
        n: {
            "atlasName": m["atlas_name"], "index": m["index"], "split": m["split"], "pad": m["pad"],
            "extraPairs": [{"key": k, "values": v} for k, v in (m.get("extra_pairs") or [])],
        }
        for n, m in region_metas_f.items()
    },
    "expectedPages": [grid(p) for p in multi_pages],
    "expectedAtlasText": multi_text,
})

print(f"Repack scenario cases: {len(repack_cases)} (E=single-page dedup, F=multi-page no-dedup)")


# ═════════════════════════════════════════════════════════════════════════
# Real-world spot-checks (messy/anti-aliased pixel data): extract + a
# multi-page-repack redistribution, tolerance-compared (not exact -- see
# module docstring's premultiply-alpha rationale).
# ═════════════════════════════════════════════════════════════════════════

# .workspaces is untracked local data living at the TOP-LEVEL AtlasToolkit
# checkout, not inside this git-worktree (worktrees only contain tracked
# files) -- check both locations so this script works whether run from a
# worktree or a plain checkout.
WORKSPACES_CANDIDATES = [
    os.path.join(REPO, ".workspaces"),
    os.path.abspath(os.path.join(REPO, "..", "..", ".workspaces")),
]


def find_real_atlas(rel_path):
    for base in WORKSPACES_CANDIDATES:
        p = os.path.join(base, rel_path)
        if os.path.exists(p):
            return p
    return None


rw_targets = [
    ("group_017/maki/maki_spr.atlas", "group_017/maki/maki_spr.png", "rotated regions, legacy xy:/size:/orig:/offset: format"),
    ("group_024/np0229/NP0229_spr.atlas", "group_024/np0229/NP0229_spr.png", "offset (whitespace-stripped) regions, scale:1.01"),
]

rw_loaded = []
for atlas_rel, png_rel, desc in rw_targets:
    atlas_path = find_real_atlas(atlas_rel)
    png_path = find_real_atlas(png_rel)
    if not atlas_path or not png_path:
        print(f"SKIP real-world target {atlas_rel}: not found in this environment")
        continue
    with open(atlas_path) as f:
        text = f.read()
    img = Image.open(png_path).convert("RGBA")
    rw_loaded.append((atlas_rel, desc, text, img))

for atlas_rel, desc, text, img in rw_loaded:
    page_info, rnames, regions = parse_page_info(text)
    # Mirror AtlasProcessor.loadImages EXACTLY: if the declared page size
    # doesn't match the real PNG's actual dimensions, scaleX/scaleY are
    # derived from real/declared -- and every real extract MUST go through
    # that scale, or (a) out-of-declared-bounds regions crop to nothing but
    # transparent pixels (a hollow, no-op spot-check that passes without
    # verifying anything), and (b) in-bounds regions get compared against the
    # WRONG (unscaled) coordinates, silently testing a code path production
    # never actually takes for this atlas. NP0229 declares size:2048,2048
    # but its real PNG is 1024x1024 -- this is exactly that case; confirmed
    # empirically during harness development (see task-5-report.md) that 2 of
    # its 4 originally-unscaled extract cases were fully alpha=0 (hollow).
    declared_w, declared_h = (int(v) for v in page_info.get("size", "0,0").split(","))
    if declared_w and declared_h and (img.width != declared_w or img.height != declared_h):
        scale_x = img.width / declared_w
        scale_y = img.height / declared_h
    else:
        scale_x = scale_y = 1.0
    page_obj = Page(filename=page_info.get("page", ""), scale_x=scale_x, scale_y=scale_y)

    picked = rnames[: min(4, len(rnames))]
    for name in picked:
        region = regions[name]
        out = extract_region_from_page(img, region, page_obj)
        realworld_cases.append({
            "name": f"real-world spot-check [{atlas_rel}] extract '{name}' ({desc}, scaleX={scale_x:.4f} scaleY={scale_y:.4f})",
            "op": "extractRegionFromPage",
            "exact": False,
            "tolerance": 1,
            "atlasText": text,
            "regionName": name,
            "scaleX": scale_x,
            "scaleY": scale_y,
            "expected": grid(out),
        })

# Real-world multi-page-repack spot check: take one real atlas's sprites,
# redistribute across 2 pages, tolerance-compare (natural anti-aliased
# pixels -> canvas drawImage/compositing can shift RGB by ~1 under partial
# alpha even with correct logic, per Deliverable-2's stated tolerance rule).
if rw_loaded:
    atlas_rel, desc, text, img = rw_loaded[0]
    _, rnames, regions = parse_page_info(text)
    picked = rnames[: min(8, len(rnames))]
    rw_sprites = {n: extract_raw_sprite(img, regions[n]) for n in picked}
    rw_page_infos = [
        {"page": "rwpage0.png", "format": "RGBA8888", "filter": "Nearest,Nearest", "repeat": "none", "pma": False},
        {"page": "rwpage1.png", "format": "RGBA8888", "filter": "Nearest,Nearest", "repeat": "none", "pma": False},
    ]
    rw_metas = {n: regions[n].to_meta_dict() for n in picked}
    rw_pages, rw_text = repack_multi_page(rw_sprites, 2, rw_page_infos, rw_metas)
    realworld_cases.append({
        "name": f"real-world spot-check [{atlas_rel}] multi-page repack of {len(picked)} real sprites across 2 pages (no dedup, anti-aliased pixels)",
        "op": "repackMultiPage",
        "exact": False,
        "tolerance": 1,
        "spriteNames": list(rw_sprites.keys()),
        "sprites": {n: grid(s) for n, s in rw_sprites.items()},
        "numPages": 2,
        "pageInfos": rw_page_infos,
        "regionMetas": {
            n: {
                "atlasName": m["atlas_name"], "index": m["index"], "split": m["split"], "pad": m["pad"],
                "extraPairs": [{"key": k, "values": v} for k, v in (m.get("extra_pairs") or [])],
            }
            for n, m in rw_metas.items()
        },
        "expectedPages": [grid(p) for p in rw_pages],
        "expectedAtlasText": rw_text,
    })

print(f"Real-world spot-check cases: {len(realworld_cases)} (atlases found: {[t[0] for t in rw_loaded]})")

# ═════════════════════════════════════════════════════════════════════════

out = {
    "pinnedSha": PINNED_SHA,
    "extractCases": cases,
    "mergeCases": merge_cases,
    "repackCases": repack_cases,
    "realworldCases": realworld_cases,
}
with open(os.path.join(HERE, "ground_truth_ops.json"), "w") as f:
    json.dump(out, f)

total = len(cases) + len(merge_cases) + len(repack_cases) + len(realworld_cases)
print(f"Wrote ground_truth_ops.json: {total} total cases "
      f"({len(cases)} extract, {len(merge_cases)} merge, {len(repack_cases)} repack, {len(realworld_cases)} real-world)")
