# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Straight outputs stay byte-identical to 54b9ace (spec 2026-10-26 §1 goal
4, §5.8). The fixture was generated at 54b9ace; never regenerate it from
this branch."""
import json

import pytest

from tests.python.gen_pma_straight_baseline import CASES, FIXTURE

WANT = json.loads(FIXTURE.read_text())


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.usefixtures("untrimmed_nest", "no_auto_bleed")
def test_straight_output_matches_54b9ace(name):
    assert CASES[name]() == WANT[name]
