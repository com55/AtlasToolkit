"""Shared fixtures for tests/python."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.fixture(scope="session")
def run_modifier_oracle():
    """Callable: list of (op, args) pairs -> list of results from the real
    www/js/atlas-modifier.js, via gen_js_reference_modifier.mjs in plain
    Node. Skips only the tests that request this fixture when node is
    absent -- never a whole file (see the pytest gotcha in CLAUDE.md)."""
    if shutil.which("node") is None:
        pytest.skip("node not found on PATH")

    def run(cases: list[tuple[str, list]]) -> list:
        result = subprocess.run(
            ["node", str(HERE / "gen_js_reference_modifier.mjs")],
            input=json.dumps([{"op": op, "args": args} for op, args in cases]),
            capture_output=True, text=True, check=True,
            cwd=HERE.parent.parent,
        )
        return json.loads(result.stdout)

    return run


@pytest.fixture(scope="session")
def run_packing_oracle():
    """Callable: list of (op, args) pairs -> list of results from the real
    www/js/repack-nest.js + www/js/atlas-modifier.js, via
    gen_js_reference_packing.mjs in plain Node. Skips only the tests that
    request this fixture when node is absent -- never a whole file (see the
    pytest gotcha in CLAUDE.md)."""
    if shutil.which("node") is None:
        pytest.skip("node not found on PATH")

    def run(cases: list[tuple[str, list]]) -> list:
        result = subprocess.run(
            ["node", str(HERE / "gen_js_reference_packing.mjs")],
            input=json.dumps([{"op": op, "args": args} for op, args in cases]),
            capture_output=True, text=True, check=True,
            cwd=HERE.parent.parent,
        )
        return json.loads(result.stdout)

    return run
