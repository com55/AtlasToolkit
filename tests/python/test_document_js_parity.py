import json
import shutil
import subprocess
from pathlib import Path

import pytest

from atlas_toolkit.document import AtlasDocument

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not found on PATH"
)

HERE = Path(__file__).parent
FIXTURES_DIR = HERE.parent / "browser" / "fixtures"

CASES = {
    "opaque_transparent": (FIXTURES_DIR / "opaque-transparent" / "fixture-opaque.atlas").read_text(),
    "tie_rounding": (FIXTURES_DIR / "tie-rounding" / "fixture-tie.atlas").read_text(),
    "multi_page_with_dedup": """page1.png
size: 100,100
armR
bounds: 0, 0, 10, 10
dagger
bounds: 30, 10, 4, 4
dagger
bounds: 34, 10, 4, 4

page2.png
size: 50,50
regionA
bounds: 0, 0, 5, 5
""",
    "rotated_and_offset": """page1.png
size: 64,64
head
bounds: 0, 10, 20, 20
rotate: 90
offsets: 1, 1, 25, 25
foot
bounds: 20, 10, 5, 5
rotate: 180
""",
    "empty": "",
}


def _run_js_reference() -> dict[str, str]:
    result = subprocess.run(
        ["node", str(HERE / "gen_js_reference.mjs")],
        input=json.dumps(CASES),
        capture_output=True,
        text=True,
        check=True,
        cwd=HERE.parent.parent,  # repo root -- Node resolves the .mjs's own
        # relative import against its own file URL regardless of cwd, so this
        # isn't load-bearing for that; set for a predictable subprocess cwd.
    )
    return json.loads(result.stdout)


def test_python_document_serialize_matches_real_js_atlas_document_js():
    js_output = _run_js_reference()
    for name, atlas_text in CASES.items():
        py_output = AtlasDocument.parse(atlas_text).serialize()
        assert py_output == js_output[name], f"parity mismatch for case '{name}'"
