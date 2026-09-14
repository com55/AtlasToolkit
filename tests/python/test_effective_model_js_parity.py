import json
import shutil
import subprocess
from pathlib import Path

import pytest

from atlas_toolkit.domain.effective_model import derive_effective_model

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not found on PATH"
)

HERE = Path(__file__).parent

CASES = {
    "no_batches": {
        "region_meta": {
            "arm": {"atlasName": "arm", "offsets": [1, 2, 10, 10], "index": -1, "split": None, "pad": None, "extraPairs": []},
            "leg": {"atlasName": "leg", "offsets": None, "index": -1, "split": None, "pad": None, "extraPairs": []},
        },
        "mod_batches": [],
    },
    "mixed_add_rename_remove": {
        "region_meta": {
            "arm": {"atlasName": "arm", "offsets": None, "index": -1, "split": None, "pad": None, "extraPairs": []},
            "leg": {"atlasName": "leg", "offsets": None, "index": -1, "split": None, "pad": None, "extraPairs": []},
            "torso": {"atlasName": "torso", "offsets": None, "index": -1, "split": None, "pad": None, "extraPairs": []},
        },
        "mod_batches": [
            {"type": "mod", "names": ["torso"], "source": "src"},
            {"type": "add", "internal_key": "helmet", "atlas_name": "helmet", "source_canvas": None},
            {"type": "rename", "target_key": "helmet", "new_atlas_name": "iron_helmet"},
            {"type": "remove", "target_key": "leg"},
            {"type": "rename", "target_key": "arm", "new_atlas_name": "forearm"},
        ],
    },
    "add_then_remove_same_region": {
        "region_meta": {
            "arm": {"atlasName": "arm", "offsets": None, "index": -1, "split": None, "pad": None, "extraPairs": []},
        },
        "mod_batches": [
            {"type": "add", "internal_key": "helmet", "atlas_name": "helmet", "source_canvas": None},
            {"type": "remove", "target_key": "helmet"},
        ],
    },
}


def _run_js_reference(cases: dict) -> dict:
    result = subprocess.run(
        ["node", str(HERE / "gen_js_reference_effective_model.mjs")],
        input=json.dumps(cases), capture_output=True, text=True, check=True,
        cwd=HERE.parent.parent,
    )
    return json.loads(result.stdout)


def test_python_matches_real_js_effective_region_model_js():
    js_output = _run_js_reference(CASES)
    for name, spec in CASES.items():
        py = derive_effective_model(spec["region_meta"], spec["mod_batches"])
        py_comparable = {
            "region_names": py["region_names"],
            "regions": py["regions"],
            "labels": py["labels"],
            "modified_keys": sorted(py["modified_keys"]),
        }
        assert py_comparable == js_output[name], f"parity mismatch for case '{name}'"
