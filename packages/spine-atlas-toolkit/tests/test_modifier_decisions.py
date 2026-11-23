# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Pinned tests for engine/modifier.py's pure mod-canvas decision functions."""


from spine_atlas_toolkit.engine.modifier import (
    ModCanvas,
    canvas_size_match,
    resolve_mod_canvas,
)

def test_resolve_mod_canvas_padding_case_pinned():
    # Offsets canvas 200x200, mod 100x108: ratios 0.5/0.54 differ by < 0.05
    # -> proportional scale 0.52 -> 104x104. dw = 4 exceeds
    # max(2, round_half_even(104 * 0.02) = 2), so NOT full canvas -- the
    # padding branch is reachable (value confirmed against the real JS).
    assert resolve_mod_canvas([[10, 20, 200, 200]], 100, 108) == ModCanvas(104, 104, 200, 200, 10, 20, False)


def test_canvas_size_match_bankers_rounded_tolerance_pinned():
    # 125 * 0.02 = 2.5 -> round_half_even -> 2px tolerance (Math.round would give 3).
    assert canvas_size_match(127, 125, 125, 125) is True
    assert canvas_size_match(128, 125, 125, 125) is False
