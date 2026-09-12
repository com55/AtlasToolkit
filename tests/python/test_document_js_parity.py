import json
import shutil
import subprocess
from pathlib import Path

import pytest

from atlas_toolkit.document import AtlasDocument, png_names_for_save

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not found on PATH"
)

HERE = Path(__file__).parent
FIXTURES_DIR = HERE.parent / "browser" / "fixtures"

CASES = {
    "opaque_transparent": {
        "fn": "parse_serialize",
        "atlas_text": (FIXTURES_DIR / "opaque-transparent" / "fixture-opaque.atlas").read_text(),
    },
    "tie_rounding": {
        "fn": "parse_serialize",
        "atlas_text": (FIXTURES_DIR / "tie-rounding" / "fixture-tie.atlas").read_text(),
    },
    "multi_page_with_dedup": {
        "fn": "parse_serialize",
        "atlas_text": """page1.png
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
    },
    "rotated_and_offset": {
        "fn": "parse_serialize",
        "atlas_text": """page1.png
size: 64,64
head
bounds: 0, 10, 20, 20
rotate: 90
offsets: 1, 1, 25, 25
foot
bounds: 20, 10, 5, 5
rotate: 180
""",
    },
    "empty": {"fn": "parse_serialize", "atlas_text": ""},
    "with_updates_basic": {
        "fn": "with_updates",
        "atlas_text": "page1.png\nsize: 100,100\narmR\nbounds: 0, 0, 10, 10\n",
        "updated_regions": {"armR": [[5, 5, 20, 20], [1, 1, 25, 25], 90]},
        "page_size": [200, 200],
    },
    "from_rebuild_args_basic": {
        "fn": "from_rebuild_args",
        "page_info": {
            "page": "atlas2.png", "format": "RGBA8888",
            "filter": "Nearest, Nearest", "repeat": "none", "pma": False,
        },
        "new_size": [300, 300],
        "region_names": ["foo", "bar"],
        "region_data": {
            "foo": [
                [0, 0, 10, 10], None, 0,
                {
                    "index": 2, "split": [1, 2, 3, 4], "pad": [0, 0, 0, 0],
                    "extraPairs": [["origin", ["5", "5"]]], "atlasName": "foo",
                },
            ],
            "bar": [[10, 0, 15, 15], [0, 0, 15, 15], 0],
        },
    },
    "png_names_for_save_basic": {
        "fn": "png_names_for_save",
        "atlas_text": "hero_spr.png\nsize: 10,10\nr\nbounds: 0,0,1,1\n",
        "page_count": 1,
        "atlas_filename": "hero.atlas",
    },
}


def _run_js_reference(cases: dict) -> dict[str, str]:
    try:
        result = subprocess.run(
            ["node", str(HERE / "gen_js_reference.mjs")],
            input=json.dumps(cases),
            capture_output=True,
            text=True,
            check=True,
            cwd=HERE.parent.parent,  # repo root -- Node resolves the .mjs's own
            # relative import against its own file URL regardless of cwd, so this
            # isn't load-bearing for that; set for a predictable subprocess cwd.
        )
    except subprocess.CalledProcessError as e:
        # Surface real Node/JS-side stderr (a syntax error, a throw, a bad
        # import) instead of the bare, uninformative CalledProcessError --
        # found by Task 6's scrutinize review: the default failure here
        # forced a manual re-run of the node command to see what broke.
        pytest.fail(f"JS reference helper failed (exit {e.returncode}):\n{e.stderr}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(
            f"JS reference helper produced non-JSON stdout:\n{result.stdout!r}\n"
            f"stderr:\n{result.stderr}"
        )


def _run_python_side(spec: dict) -> object:
    fn = spec["fn"]
    if fn == "parse_serialize":
        return AtlasDocument.parse(spec["atlas_text"]).serialize()
    if fn == "with_updates":
        doc = AtlasDocument.parse(spec["atlas_text"])
        updated = doc.with_updates(spec["updated_regions"], spec.get("page_size"))
        return updated.serialize()
    if fn == "from_rebuild_args":
        doc = AtlasDocument.from_rebuild_args(
            spec["page_info"], spec["new_size"], spec["region_names"], spec["region_data"]
        )
        return doc.serialize()
    if fn == "png_names_for_save":
        return png_names_for_save(
            spec["atlas_text"], spec["page_count"], spec.get("atlas_filename", "atlas.atlas")
        )
    raise ValueError(f"unknown fn: {fn}")


def test_python_document_serialize_matches_real_js_atlas_document_js():
    js_output = _run_js_reference(CASES)
    for name, spec in CASES.items():
        py_output = _run_python_side(spec)
        assert py_output == js_output[name], f"parity mismatch for case '{name}'"


def test_known_accepted_divergence_short_size_line_nan_vs_zero():
    """NOT a bug -- makes an already-ruled-on, deliberate exception to the
    "byte-identical output" global constraint explicit and monitored,
    rather than silently absent from CASES above (which is exactly how
    Task 6's own scrutinize review found it: this harness's whole purpose
    is validating byte-identical output, and it excluded the one input
    class already known to violate that claim). Real JS's
    parseInt(undefined,10) is NaN, which propagates unguarded into a
    short "size:" line's second field; Python substitutes 0 instead (see
    atlas_toolkit/document.py's _parse_int docstring for the full,
    already-independently-reviewed ruling: unreachable via any real
    atlas file, and reproducing NaN would break type consistency for
    zero practical benefit -- not reversed here, just made visible)."""
    js_output = _run_js_reference(
        {"short_size": {"fn": "parse_serialize", "atlas_text": "page1.png\nsize: 100\n"}}
    )["short_size"]
    py_output = _run_python_side(
        {"fn": "parse_serialize", "atlas_text": "page1.png\nsize: 100\n"}
    )
    assert "size: 100,NaN" in js_output
    assert "size: 100,0" in py_output
    assert js_output != py_output  # confirms the divergence still exists exactly as documented
