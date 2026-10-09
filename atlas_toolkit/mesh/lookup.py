"""Narrows a parsed skeleton's full attachment map down to just the
Mesh-type entries this feature can mask with -- port of
www/js/region-mesh-lookup.js's buildMeshLookup. Region/BoundingBox/
LinkedMesh/Path/Point/Clipping are dropped here -- callers treat a missing
lookup entry as "no mask data for this region," same as any other
unavailable case.

`parsed_skeleton`'s shape matches the skel-mesh-parser package's
parse_skeleton() output (Task 7, a separate sibling repo -- see this
project's Stage 2 plan for why): {"version": {...}, "attachments":
{name: {"type": str, "path": str, ...}}}, where non-Mesh entries omit
"uvs"/"triangles" entirely rather than setting them to None (confirmed
against the real shipped output during the Stage 2 final review) -- a
plain dict keyed by name, not a JS Map (Python dicts already preserve
insertion order, so no ordering behavior is lost in translation)."""
from __future__ import annotations

from typing import Any


def build_mesh_lookup(parsed_skeleton: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for name, info in parsed_skeleton["attachments"].items():
        if info["type"] == "Mesh":
            # hull_length: the first N UV pairs are the outline (hull layout).
            # Missing on older parser output -> None, same as JS undefined.
            lookup[name] = {
                "uvs": info["uvs"],
                "triangles": info["triangles"],
                "hull_length": info.get("hull_length"),
            }
    return lookup
