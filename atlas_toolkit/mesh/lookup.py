"""Narrows a parsed skeleton's full attachment map down to just the
Mesh-type entries this feature can mask with -- port of
www/js/region-mesh-lookup.js's buildMeshLookup. Region/BoundingBox/
LinkedMesh/Path/Point/Clipping are dropped here -- callers treat a missing
lookup entry as "no mask data for this region," same as any other
unavailable case.

`parsed_skeleton`'s shape matches spine_binary's parse_skeleton() output
(Task 7): {"version": {...}, "attachments": {name: {"type": str,
"path": str, "uvs": list[float] | None, "triangles": list[int] | None}}}
-- a plain dict keyed by name, not a JS Map (Python dicts already preserve
insertion order, so no ordering behavior is lost in translation)."""
from __future__ import annotations

from typing import Any


def build_mesh_lookup(parsed_skeleton: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for name, info in parsed_skeleton["attachments"].items():
        if info["type"] == "Mesh":
            lookup[name] = {"uvs": info["uvs"], "triangles": info["triangles"]}
    return lookup
