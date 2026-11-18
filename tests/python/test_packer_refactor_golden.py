# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""The packer refactor's golden outputs (spec 2026-10-22 §7.1)."""
import importlib.util
import json
from dataclasses import replace as dataclasses_replace
from pathlib import Path

import pytest


def _load_golden():
    path = Path(__file__).with_name("gen_packer_refactor_golden.py")
    spec = importlib.util.spec_from_file_location("gen_packer_refactor_golden", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


golden = _load_golden()
EXPECTED = json.loads(golden.FIXTURE.read_text())


@pytest.mark.usefixtures("untrimmed_nest", "no_auto_bleed")
def test_session_level_generator_reproduces_the_fixture():
    cases = golden.build_cases()
    assert sorted(cases) == sorted(EXPECTED)
    for key, expected in EXPECTED.items():
        assert cases[key] == expected, key


def test_fixture_axes_are_live():
    golden.self_check(EXPECTED)


from atlas_toolkit.packer import PackOptions

# The fixture pins the old default combination -- no keep-layout, shared
# composite image, auto bleed off -- so the old bytes come back.
NEW_API = {
    "nest4": PackOptions(packer="rectangles", padding=4, mesh_aware=False),
    "nest4-mesh": PackOptions(packer="polygons", padding=4),
    "nest7": PackOptions(packer="rectangles", padding=7, mesh_aware=False),
    "nest7-mesh": PackOptions(packer="polygons", padding=7),
}


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("flag", sorted(NEW_API))
@pytest.mark.parametrize("layout", ["single", "multi"])
@pytest.mark.usefixtures("untrimmed_nest", "no_auto_bleed")
def test_new_api_reproduces_the_old_nest_goldens(layout, flag, split):
    options = dataclasses_replace(
        NEW_API[flag], try_keep_layout=False, share_composite_image=not split,
    )
    built = golden.edited_atlas(layout).build(options=options)
    assert golden.summarize(built.atlas_text, built.pages) == EXPECTED[golden.case_id(layout, flag, split)]
