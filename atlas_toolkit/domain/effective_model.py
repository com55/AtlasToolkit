"""Pure fold of pristine region metadata + an ordered structural/pixel batch
list into the "effective" model every consumer (packer, sidebar list,
modified-highlight) reads instead of the pristine atlas alone -- port of
www/js/effective-region-model.js's deriveEffectiveModel. See spec (rev 7) §2.3.

Deliberately shape-agnostic on `region_meta`'s values: this function only
ever reads/writes the `atlasName` key on regions it touches and passes every
other key through untouched (never reads position/size fields) -- both an
unscaled listing-only map and a scaled packing-facing map are valid inputs.
See this repo's Stage 2 plan header for why `mod_batches` items are plain
dicts, not classes, and why region-meta dict VALUES stay JS-shaped
(camelCase), unlike this function's own snake_case parameters/return keys."""
from __future__ import annotations

from typing import Any


def derive_effective_model(
    region_meta: dict[str, dict[str, Any]],
    mod_batches: list[dict[str, Any]],
) -> dict[str, Any]:
    region_names = list(region_meta.keys())  # stable order: pristine parse order
    regions: dict[str, dict[str, Any]] = dict(region_meta)
    labels: dict[str, str] = {}
    modified_keys: set[str] = set()
    removed: set[str] = set()

    for batch in mod_batches or []:
        batch_type = batch.get("type")
        if batch_type == "mod":
            for name in batch["names"]:
                modified_keys.add(name)
        elif batch_type == "add":
            internal_key = batch["internal_key"]
            atlas_name = batch["atlas_name"]
            if internal_key not in region_names:
                region_names.append(internal_key)
            regions[internal_key] = {
                "atlasName": atlas_name,
                "offsets": None,
                "index": -1,
                "split": None,
                "pad": None,
                "extraPairs": [],
            }
            labels[internal_key] = atlas_name
            modified_keys.add(internal_key)
        elif batch_type == "remove":
            removed.add(batch["target_key"])
        elif batch_type == "rename":
            target_key = batch["target_key"]
            new_atlas_name = batch["new_atlas_name"]
            existing = regions.get(target_key)
            if existing is not None:
                regions[target_key] = {**existing, "atlasName": new_atlas_name}
            labels[target_key] = new_atlas_name
            modified_keys.add(target_key)
        # unknown/missing batch types are silently ignored, matching the JS
        # switch statement's `default: break`

    final_names = [k for k in region_names if k not in removed]
    final_regions = {k: regions[k] for k in final_names}
    for k in removed:
        labels.pop(k, None)
        modified_keys.discard(k)

    return {
        "region_names": final_names,
        "regions": final_regions,
        "labels": labels,
        "modified_keys": modified_keys,
    }
