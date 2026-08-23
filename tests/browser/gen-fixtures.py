"""Generate the two REQUIRED checked-in synthetic fixtures for Task 5
(Deliverable 1 — see task-5-brief.md).

Both fixtures are hand-constructed, real `.atlas` text + real PNG(s) — not
synthetic in-memory-only data — so they can be loaded through the actual app
code paths (AtlasDocument.parse, AtlasProcessor, AtlasModifier, etc.), not
just unit-tested in isolation.

Run once to (re)generate the checked-in files:  python3 test/browser/gen-fixtures.py

1. fixtures/opaque-transparent/ — every pixel is either fully opaque (a=255)
   or fully transparent (a=0); transparent pixels store RGB=(0,0,0) so that
   the round-trip through a real <img> decode + Canvas premultiply/
   unpremultiply (which the opaque/transparent-only property makes a
   fixed-point: 0*alpha/alpha stays 0) can be asserted byte-for-byte, unlike
   an anti-aliased (semi-transparent) source PNG, where getImageData's
   internal un-premultiply would false-fail even on correct rotation/crop/
   paste logic. Contains 4 regions:
     - "plain"       10x10 @ (0,0), default offsets (no offsets: line)
     - "withOffsets" 8x6  @ (10,0), non-default offsets [2,2,16,10]
                     (exercises the Y-flip paste / whitespace-stripped path)
     - "dupeA"/"dupeB" two 6x6 regions at different bounds but with
       PIXEL-IDENTICAL content, for single-page repack MD5 dedup and
       multi-page repack no-dedup coverage.

2. fixtures/tie-rounding/ — deliberately forces `roundHalfEven`'s tie-
   breaking branch on an EVEN floor, where it disagrees with plain
   Math.round: offsets orig_w/orig_h = 7, scaled by the page's forced 1.5x
   scale factor (declared page size 14x14 vs real PNG 21x21) -> 7*1.5=10.5,
   floor(10.5)=10 is EVEN -> Python round()/roundHalfEven give 10, while
   Math.round (round-half-up) gives 11. A regression from roundHalfEven back
   to Math.round would therefore change the padded canvas SIZE (10x10 vs
   11x11), which the oracle's pixel/dimension comparison would catch. Empirically
   confirmed below (not assumed): python3 round(10.5) == 10; Node
   Math.round(10.5) == 11 (see assertions at the bottom of this script).
"""

import os
import subprocess
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")


def opaque(r, g, b):
    return (r % 256, g % 256, b % 256, 255)


TRANSPARENT = (0, 0, 0, 0)

# ─── Fixture 1: opaque/transparent-only ────────────────────────────────────

W, H = 32, 10
img = Image.new("RGBA", (W, H), TRANSPARENT)
px = img.load()

# "plain" region: 10x10 checkerboard @ (0,0), alpha strictly 0/255.
for y in range(10):
    for x in range(10):
        if (x + y) % 2 == 0:
            px[x, y] = opaque(20 + x * 20, 20 + y * 20, 140)
        else:
            px[x, y] = TRANSPARENT

# "withOffsets" region: 8x6 block @ (10,0), mostly opaque with a transparent
# border strip on the right column and bottom row (still binary alpha).
for y in range(6):
    for x in range(8):
        if x == 7 or y == 5:
            px[10 + x, y] = TRANSPARENT
        else:
            px[10 + x, y] = opaque(10 + x * 15, 200 - y * 10, 90)

# "dupeA" / "dupeB": identical 6x6 pattern painted at two different bounds
# boxes, so single-page repack's MD5 dedup collapses them to one packed
# instance, while multi-page repack (no dedup) keeps both.
DUPE_PATTERN = []
for y in range(6):
    row = []
    for x in range(6):
        if (x * 3 + y) % 4 == 0:
            row.append(TRANSPARENT)
        else:
            row.append(opaque(30 + x * 30, 60 + y * 25, 200))
    DUPE_PATTERN.append(row)

for base_x in (20, 26):
    for y in range(6):
        for x in range(6):
            px[base_x + x, y] = DUPE_PATTERN[y][x]

opaque_dir = os.path.join(FIXTURES, "opaque-transparent")
os.makedirs(opaque_dir, exist_ok=True)
img.save(os.path.join(opaque_dir, "fixture-opaque.png"))

atlas_text = """fixture-opaque.png
size: 32, 10
format: RGBA8888
filter: Nearest, Nearest
repeat: none
plain
bounds: 0, 0, 10, 10
withOffsets
bounds: 10, 0, 8, 6
offsets: 2, 2, 16, 10
dupeA
bounds: 20, 0, 6, 6
dupeB
bounds: 26, 0, 6, 6
"""
with open(os.path.join(opaque_dir, "fixture-opaque.atlas"), "w") as f:
    f.write(atlas_text)

# Sanity: confirm the fixture really is binary-alpha (no partial alpha).
alphas = {p[3] for p in img.getdata()}
assert alphas <= {0, 255}, f"fixture-opaque.png has non-binary alpha values: {alphas}"
# Confirm dupeA/dupeB pixel-identical (dedup precondition).
cropA = img.crop((20, 0, 26, 6))
cropB = img.crop((26, 0, 32, 6))
assert list(cropA.getdata()) == list(cropB.getdata()), "dupeA/dupeB are not pixel-identical"

print(f"Wrote {opaque_dir}: alphas={sorted(alphas)}, dupeA==dupeB confirmed")

# ─── Fixture 2: .5-rounding-tie ─────────────────────────────────────────────

TW, TH = 21, 21
timg = Image.new("RGBA", (TW, TH), TRANSPARENT)
tpx = timg.load()
for y in range(TH):
    for x in range(TW):
        if (x * 7 + y * 13) % 5 == 0:
            tpx[x, y] = TRANSPARENT
        else:
            tpx[x, y] = opaque(30 + x * 11, 30 + y * 17, 90)

tie_dir = os.path.join(FIXTURES, "tie-rounding")
os.makedirs(tie_dir, exist_ok=True)
timg.save(os.path.join(tie_dir, "fixture-tie.png"))

# Declared page size 14x14 vs real PNG 21x21 -> AtlasProcessor.loadImages
# computes scaleX = scaleY = 21/14 = 1.5 (see www/js/atlas-extracter.js).
tie_atlas_text = """fixture-tie.png
size: 14, 14
format: RGBA8888
filter: Nearest, Nearest
repeat: none
tieRegion
bounds: 0, 0, 6, 6
offsets: 0, 0, 7, 7
"""
with open(os.path.join(tie_dir, "fixture-tie.atlas"), "w") as f:
    f.write(tie_atlas_text)

talphas = {p[3] for p in timg.getdata()}
assert talphas <= {0, 255}, f"fixture-tie.png has non-binary alpha values: {talphas}"
print(f"Wrote {tie_dir}: alphas={sorted(talphas)}")

# ─── Verify (not assume) the .5 tie actually discriminates ─────────────────
py_round = round(7 * 1.5)
assert py_round == 10, f"expected Python round(7*1.5) == 10 (banker's, even floor), got {py_round}"

node_round = int(
    subprocess.check_output(["node", "-e", "console.log(Math.round(7*1.5))"], text=True).strip()
)
assert node_round == 11, f"expected JS Math.round(7*1.5) == 11 (round-half-up), got {node_round}"

print(
    f"Confirmed discriminating .5 tie: Python round(7*1.5)={py_round} "
    f"vs JS Math.round(7*1.5)={node_round} -> canvas size 10x10 vs 11x11 "
    "if roundHalfEven regressed to Math.round."
)
