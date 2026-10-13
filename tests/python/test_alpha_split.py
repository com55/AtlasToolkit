"""domain/alpha_split.py: exact semi-transparent alpha sharing across stacked layers."""
import numpy as np
from PIL import Image

from atlas_toolkit.domain.alpha_split import SPLIT, split_alphas

COLOURS = np.arange(256, dtype=np.uint8)


def _layer(alpha):
    arr = np.zeros((1, 256, 4), np.uint8)
    arr[0, :, 0] = arr[0, :, 1] = arr[0, :, 2] = COLOURS
    arr[0, :, 3] = alpha
    return Image.fromarray(arr, "RGBA")


def _over(bottom, top):
    return np.asarray(Image.alpha_composite(bottom, top)).astype(int)


def test_every_pair_is_exact_for_every_colour():
    assert sorted(SPLIT) == list(range(2, 255))
    for a, (t, b) in SPLIT.items():
        out = _over(_layer(b), _layer(t))
        assert np.all(out[0, :, 3] == a), a
        assert np.all(out[0, :, :3] == COLOURS[:, None]), a


def test_table_is_the_most_balanced_exact_pair():
    # Regenerate from Pillow (one composite per top alpha: rows = bottom
    # alpha, columns = colour); the literal table must match it.
    found = {}
    for t in range(1, 255):
        dst = np.zeros((255, 256, 4), np.uint8)
        dst[..., :3] = COLOURS[None, :, None]
        dst[..., 3] = np.arange(255, dtype=np.uint8)[:, None]
        src = dst.copy()
        src[..., 3] = t
        out = np.asarray(Image.alpha_composite(Image.fromarray(dst, "RGBA"), Image.fromarray(src, "RGBA"))).astype(int)
        for b in range(1, 255):
            row = out[b]
            a = int(row[0, 3])
            if a == 255 or not np.all(row[:, 3] == a) or not np.all(row[:, :3] == COLOURS[:, None]):
                continue
            best = found.get(a)
            if best is None or (abs(t - b), (t, b)) < (abs(best[0] - best[1]), best):
                found[a] = (t, b)
    assert found == SPLIT


def test_base_cases_transparent_canvas_and_alpha_zero_layer():
    clear = _layer(0)
    for a in range(0, 256):
        layer = _layer(a)
        on_clear = _over(clear, layer)
        assert np.all(on_clear[0, :, 3] == a)
        if a:
            assert np.all(on_clear[0, :, :3] == COLOURS[:, None])
        under_clear = _over(layer, clear)
        assert np.array_equal(under_clear, np.asarray(layer).astype(int))


def _composite(colour, layers):
    """Composite K single-pixel layers bottom-up onto a clear canvas, like get_preview_image."""
    canvas = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    for a in reversed(layers):
        canvas = Image.alpha_composite(canvas, Image.new("RGBA", (1, 1), (*colour, int(a))))
    return canvas.getpixel((0, 0))


def test_split_alphas_round_trips_for_one_two_and_three_holders():
    colour = (37, 180, 90)
    for a in (0, 1, 2, 3, 128, 200, 254, 255):
        for k in (1, 2, 3):
            alpha = np.full((1, 1), a, np.uint8)
            holders = np.ones((k, 1, 1), bool)
            layers = split_alphas(alpha, holders)[:, 0, 0]
            got = _composite(colour, layers)
            assert got[3] == a, (a, k, layers)
            if a:
                assert got[:3] == colour, (a, k, layers)


def test_split_alphas_non_holders_get_zero_and_alpha_one_stays_on_top():
    alpha = np.array([[1, 128]], np.uint8)
    holders = np.array([[[True, False]], [[True, True]], [[False, True]]])
    out = split_alphas(alpha, holders)
    assert out[:, 0, 0].tolist() == [1, 0, 0]          # alpha 1: top holder keeps it all
    assert out[0, 0, 1] == 0                           # layer 0 does not hold pixel 1
    t, b = SPLIT[128]
    assert out[1:, 0, 1].tolist() == [t, b]


def test_split_alphas_opaque_goes_to_every_holder():
    alpha = np.array([[255]], np.uint8)
    holders = np.array([[[True]], [[False]], [[True]]])
    assert split_alphas(alpha, holders)[:, 0, 0].tolist() == [255, 0, 255]
