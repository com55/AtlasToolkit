# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""File paths for extracted regions."""
import re

_UNSAFE = re.compile(r"[^\w.\- ]")
_RESERVED = re.compile(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?")


def _segment(part: str) -> str:
    """One safe path segment: unsafe characters become '_', no trailing dot
    or space, no Windows device name."""
    part = _UNSAFE.sub("_", part).rstrip(". ") or "_"
    return "_" + part if _RESERVED.fullmatch(part) else part


def export_paths(names: list[str]) -> list[str]:
    """A distinct relative PNG path per region name, in order.

    '/' in a name makes folders, as Spine's texture packer names regions
    after the subfolders their images came from: head/face -> head/face.png.
    A path already taken, ignoring case as Windows and macOS do, gets
    ' (2)', ' (3)', ... A name whose folder would land on an earlier file is
    flattened into one file name instead."""
    files: set[str] = set()
    folders: set[str] = set()
    paths = []
    for name in names:
        parts = [_segment(part) for part in name.split("/") if part] or ["_"]
        prefixes = ["/".join(parts[:i]).casefold() for i in range(1, len(parts))]
        if any(prefix in files for prefix in prefixes):
            parts = [_segment("_".join(parts))]
            prefixes = []
        stem = "/".join(parts)
        path, count = f"{stem}.png", 1
        while path.casefold() in files or path.casefold() in folders:
            count += 1
            path = f"{stem} ({count}).png"
        files.add(path.casefold())
        folders.update(prefixes)
        paths.append(path)
    return paths
