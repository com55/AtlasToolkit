"""LibGDX-atlas -> Spine-atlas text conversion -- port of
www/js/atlas-converter.js. That JS file is itself a port of a real prior
Python original (atlas_toolkit/atlas/converter.py on
origin/legacy/dev-python), but this port follows the CURRENT JS behavior,
not the legacy Python one -- they differ in _is_page_line's extension
matching (this port/JS: .png only; legacy Python: png/jpg/jpeg/webp/bmp/gif).
This project's JS `dev` branch is the migration oracle; do not "fix" this
back to the broader legacy behavior."""
from __future__ import annotations

import re

_PAGE_KEYS = {"size", "format", "filter", "repeat", "pma"}
_REGION_CORE_KEYS = {
    "index", "rotate", "bounds", "xy", "size", "offsets", "orig", "offset",
    "split", "pad",
}
# Mirrors JS's /^.+\.png$/i.test(line.trim()): JS regex `.` excludes line
# terminator characters, and `^...$` (no /m flag) requires the WHOLE
# trimmed string to match, with at least 1 char before the literal ".png".
# A naive `line.strip().lower().endswith(".png")` is NOT equivalent: it
# wrongly accepts a bare ".png" (no ".+" requirement) and wrongly accepts
# an embedded "\r" (Python's `.` matches \r by default, unlike JS's `.`)
# -- found and fixed after Task 3's scrutinize review caught it
# empirically (".png" alone, and "foo\rbar.png", both diverged from the
# real JS module; a whole region's parse outcome flipped on the
# ".png"-bare-line case, not just the boolean). This pattern excludes
# \n/\r (the two line-terminator characters that can actually appear in
# this tool's input); JS's `.` also excludes U+2028/U+2029, which this
# pattern does NOT exclude -- accepted gap, not fixed: those two code
# points cannot occur in atlas text as produced by any real Spine
# exporter or this tool's own writers, so the divergence is unreachable
# in practice. Use fullmatch(), not match()+separate end-anchor, to
# avoid `$`'s matches-before-trailing-newline quirk.
_PAGE_LINE_RE = re.compile(r"[^\n\r]+\.png", re.IGNORECASE)


def _is_page_line(line: str) -> bool:
    return bool(_PAGE_LINE_RE.fullmatch(line.strip()))


def _parse_kv(line: str) -> tuple[str, list[str]] | None:
    if ":" not in line:
        return None
    idx = line.index(":")
    key = line[:idx].strip().lower()
    values = [v.strip() for v in line[idx + 1:].split(",")]
    return key, values


def is_old_format(atlas_text: str) -> bool:
    """True if the key 'xy:', 'orig:', or 'offset:' exists anywhere."""
    for line in atlas_text.split("\n"):
        s = line.strip().lower()
        if s.startswith("xy:") or s.startswith("orig:") or s.startswith("offset:"):
            return True
    return False


def convert_atlas_to_new_format(atlas_text: str) -> str:
    """Convert atlas text from LibGDX format to Spine format: xy+size ->
    bounds, orig+offset -> offsets (offset defaults to (0,0) when absent),
    rotate true/false/90/270 passes through unchanged, page-level `size`
    is never touched."""
    lines = atlas_text.split("\n")
    result: list[str] = []

    in_page_header = False
    in_region = False
    region_name_line = ""
    region_kv: dict[str, list[str]] = {}
    region_extra_lines: list[str] = []

    def flush_region() -> None:
        nonlocal in_region
        if not region_name_line:
            return
        result.append(region_name_line)

        if "index" in region_kv:
            result.append(f"  index: {region_kv['index'][0]}")

        rotate_val = region_kv.get("rotate", ["false"])[0].strip().lower()
        result.append(f"  rotate: {rotate_val}")

        if "bounds" in region_kv:
            b = region_kv["bounds"]
            result.append(f"  bounds: {b[0]}, {b[1]}, {b[2]}, {b[3]}")
        else:
            xy = region_kv.get("xy", ["0", "0"])
            sz = region_kv.get("size", ["0", "0"])
            result.append(f"  bounds: {xy[0]}, {xy[1]}, {sz[0]}, {sz[1]}")

        if "offsets" in region_kv:
            o = region_kv["offsets"]
            result.append(f"  offsets: {o[0]}, {o[1]}, {o[2]}, {o[3]}")
        elif "orig" in region_kv:
            orig = region_kv["orig"]
            offset = region_kv.get("offset", ["0", "0"])
            result.append(f"  offsets: {offset[0]}, {offset[1]}, {orig[0]}, {orig[1]}")

        for key in ("split", "pad"):
            if key in region_kv:
                result.append(f"  {key}: {', '.join(region_kv[key])}")

        for key, values in region_kv.items():
            if key not in _REGION_CORE_KEYS:
                result.append(f"  {key}: {', '.join(values)}")

        result.extend(region_extra_lines)
        in_region = False

    for raw_line in lines:
        stripped = raw_line.strip()

        if not stripped:
            if in_region:
                flush_region()
                region_name_line, region_kv, region_extra_lines = "", {}, []
            in_page_header = False
            result.append(raw_line)
            continue

        if _is_page_line(stripped):
            if in_region:
                flush_region()
                region_name_line, region_kv, region_extra_lines = "", {}, []
            in_page_header = True
            in_region = False
            result.append(raw_line)
            continue

        parsed = _parse_kv(raw_line)
        if parsed:
            key, values = parsed
            if in_page_header:
                result.append(raw_line)
                if key not in _PAGE_KEYS:
                    in_page_header = False
                continue
            if in_region:
                region_kv[key] = values
            else:
                result.append(raw_line)
            continue

        # No colon -> new region name.
        if in_region:
            flush_region()
            region_name_line, region_kv, region_extra_lines = "", {}, []

        in_page_header = False
        in_region = True
        region_name_line = raw_line
        region_kv, region_extra_lines = {}, []

    if in_region:
        flush_region()

    return "\n".join(result)


def auto_convert_atlas(atlas_text: str) -> str:
    """If old format, convert. If new format, return the original text
    unchanged (not even reformatted)."""
    return convert_atlas_to_new_format(atlas_text) if is_old_format(atlas_text) else atlas_text
