"""Parity tests for engine/modifier.py's pure mod-canvas decision functions
against the real www/js/atlas-modifier.js (plain Node, via conftest.py's
run_modifier_oracle fixture). Inputs are mined from
tests/atlas-modifier.test.js, plus edge cases that file does not cover:
no-offsets / empty / mixed-null selections, both proportional-scale
branches, and the reachable non-full-canvas (padding) case."""
from dataclasses import asdict

import pytest

from atlas_toolkit.engine.modifier import (
    ModCanvas,
    canvas_size_match,
    pick_anchor_offsets,
    repack_offsets_for_region,
    resolve_mod_canvas,
    selected_share_canvas,
)

CASES = {
    "csm_2px_floor_exact": ("canvasSizeMatch", [102, 100, 100, 100]),
    "csm_2px_floor_over": ("canvasSizeMatch", [103, 100, 100, 100]),
    "csm_2pct_large_exact": ("canvasSizeMatch", [510, 500, 500, 500]),
    "csm_2pct_large_over": ("canvasSizeMatch", [511, 500, 500, 500]),
    "csm_nonpositive_canvas": ("canvasSizeMatch", [10, 10, 0, 10]),
    "csm_bankers_tie_within": ("canvasSizeMatch", [127, 125, 125, 125]),
    "csm_bankers_tie_over": ("canvasSizeMatch", [128, 125, 125, 125]),
    "ssc_same_size_pair": ("selectedShareCanvas", [[[0, 0, 100, 100], [5, 5, 100, 100]]]),
    "ssc_any_missing": ("selectedShareCanvas", [[[0, 0, 100, 100], None]]),
    "ssc_differing_sizes": ("selectedShareCanvas", [[[0, 0, 100, 100], [0, 0, 80, 80]]]),
    "ssc_single_region": ("selectedShareCanvas", [[[0, 0, 100, 100]]]),
    "ssc_empty": ("selectedShareCanvas", [[]]),
    "pao_min_sum": ("pickAnchorOffsets", [[[10, 10, 100, 100], [4, 4, 100, 100], [7, 2, 100, 100]]]),
    "pao_tie_broken_by_offx": ("pickAnchorOffsets", [[[6, 2, 200, 100], [4, 4, 200, 100]]]),
    "pao_tie_broken_by_offy": ("pickAnchorOffsets", [[[3, 5, 50, 50], [3, 3, 50, 50]]]),
    "rmc_canvas_size_disagreement": ("resolveModCanvas", [[[10, 10, 200, 100], [4, 4, 150, 80]], 60, 40]),
    "rmc_shared_canvas": ("resolveModCanvas", [[[10, 5, 100, 100], [4, 4, 100, 100]], 100, 100]),
    "rmc_single_cropped": ("resolveModCanvas", [[[10, 5, 100, 100]], 60, 40]),
    "rmc_no_offsets": ("resolveModCanvas", [[None], 60, 40]),
    "rmc_empty_selection": ("resolveModCanvas", [[], 60, 40]),
    "rmc_proportional_2x": ("resolveModCanvas", [[[0, 0, 100, 100]], 200, 200]),
    "rmc_near_unity_ratio": ("resolveModCanvas", [[[0, 0, 100, 100]], 103, 100]),
    "rmc_padding_reachable": ("resolveModCanvas", [[[10, 20, 200, 200]], 100, 108]),
    "rmc_mixed_null_loose_shared": ("resolveModCanvas", [[None, [3, 4, 80, 80]], 80, 80]),
    "ror_full_canvas": ("repackOffsetsForRegion", ["hero", ["hero"], [3, 4, 50, 60], 40, 40]),
    "ror_not_full_canvas": ("repackOffsetsForRegion", ["sword", ["hero"], [3, 4, 50, 60], 40, 40]),
    "ror_null_set": ("repackOffsetsForRegion", ["sword", None, [3, 4, 50, 60], 40, 40]),
    "ror_empty_set": ("repackOffsetsForRegion", ["sword", [], [1, 2, 9, 9], 40, 40]),
    "ror_null_pristine": ("repackOffsetsForRegion", ["sword", ["hero"], None, 40, 40]),
}


def _python_result(op: str, args: list):
    if op == "canvasSizeMatch":
        return canvas_size_match(*args)
    if op == "selectedShareCanvas":
        return selected_share_canvas(args[0])
    if op == "pickAnchorOffsets":
        return pick_anchor_offsets(args[0])
    if op == "resolveModCanvas":
        return asdict(resolve_mod_canvas(*args))
    if op == "repackOffsetsForRegion":
        name, full_names, pristine_offsets, packed_w, packed_h = args
        full = None if full_names is None else set(full_names)
        return repack_offsets_for_region(name, full, pristine_offsets, packed_w, packed_h)
    raise ValueError(f"unknown op: {op}")


@pytest.fixture(scope="module")
def js_results(run_modifier_oracle):
    case_ids = list(CASES)
    return dict(zip(case_ids, run_modifier_oracle([CASES[c] for c in case_ids])))


@pytest.mark.parametrize("case_id", list(CASES))
def test_matches_real_js(case_id, js_results):
    op, args = CASES[case_id]
    assert _python_result(op, args) == js_results[case_id]


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
