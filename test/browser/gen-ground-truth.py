"""Regenerate PIL ground-truth fixtures for the rotation verification.

Execs the ACTUAL main-branch region_ops.py (crop_and_rotate /
extract_region_from_page) against small asymmetric fixtures and dumps each
case's source grid + expected output pixels to ground_truth.json. The browser
harness (verify-rotation.mjs) then runs the JS Canvas 2D implementation in
core-region-ops.js on the same source grids and asserts pixel-identical output
— PIL is the normative reference the whole port targets, so a pixel match is
correctness by definition (and distinct-per-pixel colours make a mirror
impossible to mistake for a rotation).

Run from the repo (needs Pillow + git):  python3 test/browser/gen-ground-truth.py
"""

import json
import os
import subprocess
import types
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

# Load the real main-branch region_ops functions, stubbing the project import.
src = subprocess.check_output(
    ["git", "-C", REPO, "show", "main:atlas_toolkit/core/region_ops.py"],
    text=True,
)
src = src.replace("from atlas_toolkit.core.document import Page, Region", "")


class Region:
    def __init__(self, x, y, w, h, rotate=0, offsets=None):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.rotate = rotate
        self.offsets = offsets

    @property
    def bounds(self):
        return (self.x, self.y, self.w, self.h)


class Page:
    def __init__(self, scale_x=1.0, scale_y=1.0):
        self.scale_x = scale_x
        self.scale_y = scale_y


mod = types.ModuleType("region_ops")
mod.__dict__.update(Image=Image, Region=Region, Page=Page, Optional=None, TYPE_CHECKING=False)
exec(compile(src, "region_ops.py", "exec"), mod.__dict__)
crop_and_rotate = mod.__dict__["crop_and_rotate"]
extract_region_from_page = mod.__dict__["extract_region_from_page"]


def make_img(w, h):
    img = Image.new("RGBA", (w, h))
    for row in range(h):
        for col in range(w):
            img.putpixel((col, row), (20 + col * 30, 20 + row * 30, 128, 255))
    return img


def grid(img):
    return {"w": img.width, "h": img.height, "pixels": [list(p) for p in img.getdata()]}


cases = []

W, H = 4, 2
for rotate in (0, 90, 180, 270):
    src_w = H if rotate in (90, 270) else W
    src_h = W if rotate in (90, 270) else H
    s = make_img(src_w, src_h)
    out = crop_and_rotate(s, 0, 0, W, H, rotate)
    cases.append({
        "name": f"cropAndRotate rotate={rotate} (footprint {src_w}x{src_h} -> {out.width}x{out.height})",
        "op": "cropAndRotate",
        "source": grid(s),
        "args": {"x": 0, "y": 0, "w": W, "h": H, "rotate": rotate},
        "expected": grid(out),
    })

page_img = make_img(15, 15)
region = Region(x=2, y=2, w=4, h=2, rotate=0, offsets=[1, 1, 7, 5])
out = extract_region_from_page(page_img, region, Page(1.5, 1.5))
cases.append({
    "name": "extractRegionFromPage scale=1.5 + offsets (exercises roundHalfEven ties 10.5->10, 7.5->8)",
    "op": "extractRegionFromPage",
    "source": grid(page_img),
    "args": {"x": 2, "y": 2, "w": 4, "h": 2, "rotate": 0, "offsets": [1, 1, 7, 5], "scaleX": 1.5, "scaleY": 1.5},
    "expected": grid(out),
})

page_img2 = make_img(12, 12)
region2 = Region(x=1, y=1, w=4, h=2, rotate=90, offsets=[2, 1, 8, 5])
out2 = extract_region_from_page(page_img2, region2, None)
cases.append({
    "name": "extractRegionFromPage rotate=90 + offsets (no scale)",
    "op": "extractRegionFromPage",
    "source": grid(page_img2),
    "args": {"x": 1, "y": 1, "w": 4, "h": 2, "rotate": 90, "offsets": [2, 1, 8, 5], "scaleX": 1.0, "scaleY": 1.0},
    "expected": grid(out2),
})

with open(os.path.join(HERE, "ground_truth.json"), "w") as f:
    json.dump({"cases": cases}, f)

print(f"Wrote {len(cases)} cases to ground_truth.json")
