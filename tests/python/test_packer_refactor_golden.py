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
