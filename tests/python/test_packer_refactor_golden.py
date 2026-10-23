"""The packer refactor's golden outputs (spec 2026-10-22 §7.1)."""
import importlib.util
import json
from pathlib import Path


def _load_golden():
    path = Path(__file__).with_name("gen_packer_refactor_golden.py")
    spec = importlib.util.spec_from_file_location("gen_packer_refactor_golden", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


golden = _load_golden()
EXPECTED = json.loads(golden.FIXTURE.read_text())


def test_session_level_generator_reproduces_the_fixture():
    cases = golden.build_cases()
    assert sorted(cases) == sorted(EXPECTED)
    for key, expected in EXPECTED.items():
        assert cases[key] == expected, key


def test_fixture_axes_are_live():
    golden.self_check(EXPECTED)


import pytest

from atlas_toolkit.packer import PackerOptions

NEW_API = {
    "nest4": ("rectangles", PackerOptions(padding=4, mesh_aware=False)),
    "nest4-mesh": ("polygons", PackerOptions(padding=4)),
    "nest7": ("rectangles", PackerOptions(padding=7, mesh_aware=False)),
    "nest7-mesh": ("polygons", PackerOptions(padding=7)),
}


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("flag", sorted(NEW_API))
@pytest.mark.parametrize("layout", ["single", "multi"])
def test_new_api_reproduces_the_old_nest_goldens(layout, flag, split):
    packer, options = NEW_API[flag]
    built = golden.edited_atlas(layout).build(packer=packer, packer_options=options, split_composite=split)
    assert golden.summarize(built.atlas_text, built.pages) == EXPECTED[golden.case_id(layout, flag, split)]
