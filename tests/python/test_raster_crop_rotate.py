# tests/python/test_raster_crop_rotate.py
import json
from pathlib import Path

import pytest

from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

FIXTURES = Path(__file__).parent / "fixtures"
TIE_PNG = Path(__file__).parent.parent / "browser" / "fixtures" / "tie-rounding" / "fixture-tie.png"
ORACLE_PATH = FIXTURES / "raster_js_oracle.json"

_needs_oracle = pytest.mark.skipif(
    not ORACLE_PATH.exists(),
    reason="raster_js_oracle.json not generated -- run tests/python/gen_js_reference_raster.mjs",
)
# Applied per-test below (final whole-branch review finding), not as a
# module-level pytestmark -- a module-level skip drops ALL tests in this
# file, including test_rotate_image_zero_degrees_is_identity_sized and
# test_rotate_image_swaps_dimensions_on_non_square_input, neither of which
# reads the oracle fixture at all. Losing the committed fixture (a bad
# merge, a worktree copy-in mistake -- this project's own CLAUDE.md
# documents exactly this class of gotcha for gitignored/untracked assets)
# would otherwise silently skip those two with exit code 0 instead of
# running them, directly contradicting this plan's own claim that "the
# dimension-swap check needs no oracle, so it always runs." Mirrors the
# per-test pattern test_raster_paste_and_pixels.py already uses correctly.


def _load_oracle():
    return json.loads(ORACLE_PATH.read_text())


def _decoded_rgba(backend, handle):
    """Flatten a PillowRasterBackend handle's pixels to the same
    [r,g,b,a, r,g,b,a, ...] row-major flat-int-list shape the JS oracle
    uses, for a direct list comparison."""
    w, h = backend.size(handle)
    img = backend._images[handle]
    flat = []
    for py in range(h):
        for px in range(w):
            flat.extend(img.getpixel((px, py)))
    return w, h, flat


@_needs_oracle
@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_crop_rotate_matches_js_oracle(rotate):
    oracle = _load_oracle()[f"crop_rotate_{rotate}"]
    backend = PillowRasterBackend()
    handle = backend.decode_png(TIE_PNG.read_bytes())
    result = backend.crop_rotate(handle, 0, 0, 21, 21, rotate)
    w, h, flat = _decoded_rgba(backend, result)
    assert (w, h) == (oracle["w"], oracle["h"])
    assert flat == oracle["rgba"]


@_needs_oracle
@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_crop_rotate_subrect_matches_js_oracle(rotate):
    """Non-square crop box (w=10, h=15) -- the whole-image cases above are
    always square (21x21), so they never exercise crop_rotate's isSwapped
    dimension-swap branch for a genuine w != h. This is the discriminating
    case: a regression that dropped the swap (crop_w, crop_h = (w, h) in
    both branches) would still pass every crop_rotate_{rotate} case above,
    but fails this one."""
    oracle = _load_oracle()[f"crop_rotate_subrect_{rotate}"]
    backend = PillowRasterBackend()
    handle = backend.decode_png(TIE_PNG.read_bytes())
    result = backend.crop_rotate(handle, 3, 2, 10, 15, rotate)
    w, h, flat = _decoded_rgba(backend, result)
    assert (w, h) == (oracle["w"], oracle["h"])
    assert flat == oracle["rgba"]


@_needs_oracle
@pytest.mark.parametrize(
    "degrees,oracle_key",
    [(90, "rotate_image_90"), (270, "rotate_image_270"), (180, "rotate_image_180")],
)
def test_rotate_image_matches_js_oracle(degrees, oracle_key):
    oracle = _load_oracle()[oracle_key]
    backend = PillowRasterBackend()
    handle = backend.decode_png(TIE_PNG.read_bytes())
    result = backend.rotate_image(handle, degrees)
    w, h, flat = _decoded_rgba(backend, result)
    assert (w, h) == (oracle["w"], oracle["h"])
    assert flat == oracle["rgba"]


def test_rotate_image_zero_degrees_is_identity_sized():
    backend = PillowRasterBackend()
    handle = backend.decode_png(TIE_PNG.read_bytes())
    result = backend.rotate_image(handle, 0)
    assert backend.size(result) == backend.size(handle)


def test_rotate_image_swaps_dimensions_on_non_square_input():
    """rotate_image's degrees=90/270 branch passes (h, w) -- swapped -- to
    crop_rotate, exactly like crop_rotate's own is_swapped branch. The
    fixture used by every other test in this file is square (21x21), so
    NONE of them can tell a correct swap apart from a regression that
    dropped it (both produce a 21x21 result either way) -- this is the
    identical class of gap scrutinize round 1 already found and fixed for
    crop_rotate (crop_rotate_subrect_*), but the fix was never applied to
    rotate_image specifically, and rotate_image is one of the two names
    later stages call by exact name (base.py). No oracle/pixel data is
    needed here -- crop_rotate's own pixel correctness is already verified
    above; this test only needs to confirm rotate_image requests the RIGHT
    SIZE crop, which a dropped swap gets wrong before any pixel is even
    compared: a correct swap on a 10x15 source rotated 90/270 must return
    15x10, not 10x15."""
    backend = PillowRasterBackend()
    handle = backend.decode_png(TIE_PNG.read_bytes())
    nonsquare = backend.crop_rotate(handle, 3, 2, 10, 15, 0)  # real 10x15 crop
    assert backend.size(nonsquare) == (10, 15)
    assert backend.size(backend.rotate_image(nonsquare, 90)) == (15, 10)
    assert backend.size(backend.rotate_image(nonsquare, 270)) == (15, 10)
    assert backend.size(backend.rotate_image(nonsquare, 180)) == (10, 15)
