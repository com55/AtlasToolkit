from atlas_toolkit.raster.pillow_backend import PillowRasterBackend


def test_paste_alpha_composites_overlapping_semi_transparent_regions():
    """The discriminating case: dest has an opaque red pixel at (0,0); src
    is a 1x1 fully-covering canvas with a 50%-alpha blue pixel, pasted at
    (0,0) so it overlaps dest exactly. A raw overwrite (Image.paste(src,
    box) with no mask) would leave dest as pure blue with alpha=128 --
    silently dropping the red entirely. Correct Porter-Duff "source-over"
    must blend: out_a = src_a + dst_a*(1-src_a) = 128 + 255*(127/255) = 255
    (fully opaque result, dst was already opaque), out_rgb weighted toward
    src by src_a/out_a. This is exactly the failure mode a non-overlapping,
    fully-opaque-sprites test would never catch (CLAUDE.md's documented
    alpha===0-discriminator trap, same class of bug)."""
    backend = PillowRasterBackend()
    dest = backend.create(1, 1)
    backend._images[dest].putpixel((0, 0), (255, 0, 0, 255))  # opaque red
    src = backend.create(1, 1)
    backend._images[src].putpixel((0, 0), (0, 0, 255, 128))  # 50% blue

    backend.paste(dest, src, 0, 0)

    r, g, b, a = backend._images[dest].getpixel((0, 0))
    assert a == 255, "opaque destination stays opaque after compositing over it"
    assert r > 100, f"red must still show through a 50%-alpha overlay, got r={r}"
    assert b > 100, f"blue from src must show, got b={b}"
    assert r != 255 or b != 0, "must not be an unmodified pure-red no-op"
    assert not (r == 0 and b == 255), "must not be a raw overwrite (pure src blue, red dropped)"


def test_paste_onto_transparent_ground_places_src_unchanged():
    backend = PillowRasterBackend()
    dest = backend.create(3, 3)
    src = backend.create(1, 1)
    backend._images[src].putpixel((0, 0), (10, 20, 30, 200))

    backend.paste(dest, src, 1, 1)

    assert backend._images[dest].getpixel((1, 1)) == (10, 20, 30, 200)
    assert backend._images[dest].getpixel((0, 0)) == (0, 0, 0, 0)


def test_paste_fully_opaque_src_replaces_dest_entirely():
    """Porter-Duff "over" with src_a=1 reduces algebraically to
    result=src (top alpha=1 means full replace, dest contributes nothing) --
    the degenerate case the overlap test above doesn't cover on its own
    (that one uses a 50%-alpha src specifically to catch the overwrite-vs-
    blend distinction; this one confirms full opacity still gives the
    exact src color, not some blended remainder)."""
    backend = PillowRasterBackend()
    dest = backend.create(1, 1)
    backend._images[dest].putpixel((0, 0), (255, 0, 0, 255))
    src = backend.create(1, 1)
    backend._images[src].putpixel((0, 0), (0, 255, 0, 255))

    backend.paste(dest, src, 0, 0)

    assert backend._images[dest].getpixel((0, 0)) == (0, 255, 0, 255)


def test_paste_fully_transparent_src_is_a_true_no_op():
    """Porter-Duff "over" with src_a=0 reduces algebraically to result=dest
    exactly -- dest must be byte-identical to before the paste call."""
    backend = PillowRasterBackend()
    dest = backend.create(1, 1)
    backend._images[dest].putpixel((0, 0), (10, 20, 30, 200))
    src = backend.create(1, 1)
    backend._images[src].putpixel((0, 0), (255, 255, 255, 0))

    backend.paste(dest, src, 0, 0)

    assert backend._images[dest].getpixel((0, 0)) == (10, 20, 30, 200)


def test_paste_clips_when_box_extends_past_dest_bounds():
    """Mirrors Canvas drawImage's silent clipping when the destination
    position would place part of src off-canvas -- Pillow's paste()
    documented behavior (verified via Context7 against Pillow 12.1.0's
    release notes) already allows the box to extend beyond the target
    image's boundaries without raising."""
    backend = PillowRasterBackend()
    dest = backend.create(2, 2)
    src = backend.create(2, 2)
    for px in range(2):
        for py in range(2):
            backend._images[src].putpixel((px, py), (5, 5, 5, 255))

    backend.paste(dest, src, 1, 1)  # half of src falls outside dest

    assert backend._images[dest].getpixel((1, 1)) == (5, 5, 5, 255)
    assert backend.size(dest) == (2, 2)  # dest itself never resizes


def test_get_pixels_returns_row_major_rgba_bytes():
    backend = PillowRasterBackend()
    handle = backend.create(2, 1)
    backend._images[handle].putpixel((0, 0), (1, 2, 3, 4))
    backend._images[handle].putpixel((1, 0), (5, 6, 7, 8))

    data = backend.get_pixels(handle)

    assert data == bytes([1, 2, 3, 4, 5, 6, 7, 8])


def test_read_alpha_mask_thresholds_at_zero():
    """Matches repack-nest.js's thresholdAlpha exactly: alpha > 0 -> True,
    including partially-transparent pixels (alpha=1 is still True) --
    not a >= 128 or similar midpoint threshold."""
    backend = PillowRasterBackend()
    handle = backend.create(3, 1)
    backend._images[handle].putpixel((0, 0), (0, 0, 0, 0))
    backend._images[handle].putpixel((1, 0), (0, 0, 0, 1))
    backend._images[handle].putpixel((2, 0), (0, 0, 0, 255))

    mask = backend.read_alpha_mask(handle)

    assert mask == [False, True, True]
