"""AlphaMode parsing and the premultiply/unpremultiply helpers (spec 2026-10-28 §3.1-3.2)."""
import numpy as np
import pytest

from atlas_toolkit.alpha_mode import AlphaMode, parse_alpha_mode
from atlas_toolkit.raster.premultiplied import premultiply, unpremultiply

pytestmark = pytest.mark.filterwarnings("error::RuntimeWarning")


def px(*values):
    return np.array([values], np.uint8)


def every_non_additive_pixel():
    c, a = np.meshgrid(np.arange(256), np.arange(1, 256), indexing="ij")
    keep = c <= a
    c, a = c[keep], a[keep]
    return np.stack([c, c, c, a], axis=-1).astype(np.uint8)


def test_round_trip_is_exact_for_every_non_additive_pixel():
    pixels = every_non_additive_pixel()
    assert len(pixels) == 32895
    assert np.array_equal(premultiply(unpremultiply(pixels)), pixels)


def test_alpha_zero_becomes_transparent_black_both_ways():
    assert premultiply(px(50, 60, 70, 0)).tolist() == [[0, 0, 0, 0]]
    assert unpremultiply(px(90, 60, 30, 0)).tolist() == [[0, 0, 0, 0]]


def test_unpremultiply_clips_additive_colour():
    assert unpremultiply(px(200, 0, 0, 100)).tolist() == [[255, 0, 0, 100]]


def test_premultiply_rounds_to_nearest():
    assert premultiply(px(100, 50, 25, 128)).tolist() == [[50, 25, 13, 128]]


def test_unpremultiply_rounds_half_to_even():
    # 1 * 255 / 6 = 42.5
    assert unpremultiply(px(1, 1, 1, 6)).tolist() == [[42, 42, 42, 6]]


def test_helpers_keep_shape_and_do_not_touch_their_input():
    image = np.zeros((3, 2, 4), np.uint8)
    image[...] = (10, 20, 30, 40)
    before = image.copy()
    assert premultiply(image).shape == (3, 2, 4)
    assert unpremultiply(image).shape == (3, 2, 4)
    assert np.array_equal(image, before)


def test_parse_alpha_mode_accepts_none_enum_and_value():
    assert parse_alpha_mode(None) is None
    assert parse_alpha_mode(AlphaMode.STRAIGHT) is AlphaMode.STRAIGHT
    assert parse_alpha_mode("premultiplied") is AlphaMode.PREMULTIPLIED


@pytest.mark.parametrize("value", ["bogus", "Straight", ""])
def test_parse_alpha_mode_rejects_unknown_strings(value):
    with pytest.raises(ValueError, match="expected one of: straight, premultiplied"):
        parse_alpha_mode(value)


@pytest.mark.parametrize("value", [1, True, b"straight"])
def test_parse_alpha_mode_rejects_other_types(value):
    with pytest.raises(TypeError, match="alpha_mode must be an AlphaMode or str"):
        parse_alpha_mode(value)
