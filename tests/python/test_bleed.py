# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""domain/bleed.py: Spine's packer Bleed (libGDX ColorBleedEffect semantics)."""
import numpy as np
import pytest

from atlas_toolkit.domain.bleed import color_bleed


def page(w, h):
    return np.zeros((h, w, 4), np.uint8)


def test_one_ring_per_iteration_alpha_untouched():
    rgba = page(7, 1)
    rgba[0, 0] = (100, 50, 10, 255)
    out = color_bleed(rgba, iterations=2)
    assert out[0, 1].tolist() == [100, 50, 10, 0]
    assert out[0, 2].tolist() == [100, 50, 10, 0]  # fed by the pixel bled in pass 1
    assert out[0, 3].tolist() == [0, 0, 0, 0]       # third ring stays as it was
    assert (out[..., 3] == rgba[..., 3]).all()
    assert rgba[0, 1].tolist() == [0, 0, 0, 0]       # input not modified


def test_unweighted_integer_mean_of_eight_neighbours():
    rgba = page(3, 3)
    rgba[0, 0] = (10, 0, 0, 1)      # a faint pixel counts at full strength
    rgba[0, 2] = (21, 0, 0, 255)
    rgba[2, 1] = (200, 9, 0, 255)
    out = color_bleed(rgba, iterations=1)
    assert out[1, 1].tolist() == [(10 + 21 + 200) // 3, 9 // 3, 0, 0]
    assert out[0, 1].tolist() == [(10 + 21) // 2, 0, 0, 0]  # (2,1) is not a neighbour of (0,1)


def test_a_pixel_bled_this_pass_is_not_a_source_in_the_same_pass():
    rgba = page(3, 1)
    rgba[0, 0] = (90, 0, 0, 255)
    rgba[0, 2] = (30, 0, 0, 255)
    out = color_bleed(rgba, iterations=1)
    assert out[0, 1, 0] == 60
    rgba = page(4, 1)
    rgba[0, 0] = (90, 0, 0, 255)
    out = color_bleed(rgba, iterations=1)
    assert out[0, 1, 0] == 90 and out[0, 2, 0] == 0


def test_opaque_pixels_and_fully_blank_pages_are_unchanged():
    rgba = np.random.default_rng(1).integers(0, 256, (5, 6, 4), dtype=np.uint8)
    rgba[..., 3] = np.maximum(rgba[..., 3], 1)
    assert (color_bleed(rgba) == rgba).all()
    blank = page(4, 4)
    assert (color_bleed(blank) == blank).all()


def test_stale_rgb_under_alpha_zero_is_replaced():
    rgba = page(2, 1)
    rgba[0, 0] = (7, 7, 7, 255)
    rgba[0, 1] = (250, 250, 250, 0)
    assert color_bleed(rgba)[0, 1].tolist() == [7, 7, 7, 0]


@pytest.mark.parametrize("iterations", [0, -1])
def test_no_iterations_changes_nothing(iterations):
    rgba = page(2, 1)
    rgba[0, 0] = (7, 7, 7, 255)
    assert (color_bleed(rgba, iterations=iterations) == rgba).all()
