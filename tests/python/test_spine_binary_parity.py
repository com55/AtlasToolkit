import json
import shutil
import subprocess
from pathlib import Path

import pytest

from skel_mesh_parser import parse_skeleton

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not found on PATH"
)

HERE = Path(__file__).parent
FIXTURES = HERE.parent / "fixtures"

CASES = {
    "synthetic_38": {"fixture": "synthetic-skeleton-38"},
    "synthetic_42": {"fixture": "synthetic-skeleton-42"},
}


def _run_js_reference(cases: dict) -> dict:
    result = subprocess.run(
        ["node", str(HERE / "gen_js_reference_spine_binary.mjs")],
        input=json.dumps(cases), capture_output=True, text=True, check=True,
        cwd=HERE.parent.parent,
    )
    return json.loads(result.stdout)


def _pythonic_keys(parsed: dict) -> dict:
    """The Python parser names Mesh's hull count `hull_length` (PEP 8); the JS
    parser calls it `hullLength`. Same value, so rename before comparing."""
    attachments = {}
    for name, info in parsed["attachments"].items():
        info = dict(info)
        if "hullLength" in info:
            info["hull_length"] = info.pop("hullLength")
        attachments[name] = info
    return {**parsed, "attachments": attachments}


def test_python_matches_real_js_vendored_parser():
    js_output = _run_js_reference(CASES)
    for name, spec in CASES.items():
        data = (FIXTURES / f"{spec['fixture']}.skel").read_bytes()
        py = parse_skeleton(data)
        assert py == _pythonic_keys(js_output[name]), f"parity mismatch for case '{name}'"
