"""
atlas_converter.py
convert LibGDX atlas format → Spine atlas format
"""

from __future__ import annotations

import re
from typing import Dict, Optional


# Keys that are old format at region level (not page)
_OLD_REGION_KEYS = {"xy", "size", "orig", "offset"}

# Region keys handled explicitly by flush_region (everything else is preserved)
_REGION_CORE_KEYS = {
    "index", "rotate", "bounds", "xy", "size", "offsets", "orig", "offset",
    "split", "pad",
}

# Keys that are page-level (don't touch)
_PAGE_KEYS = {"size", "format", "filter", "repeat", "pma"}


def _is_page_line(line: str) -> bool:
    """True if this line is a page name (ends with .png / .jpg / .webp etc.)"""
    stripped = line.strip()
    return bool(re.match(r'.+\.(png|jpg|jpeg|webp|bmp|gif)$', stripped, re.IGNORECASE))


def _parse_kv(line: str) -> Optional[tuple[str, list[str]]]:
    """
    Parse 'key: v1, v2, ...'
    Return (key_lower, [v1, v2, ...]) or None if not a kv line
    """
    if ':' not in line:
        return None
    key, _, rest = line.partition(':')
    key = key.strip().lower()
    values = [v.strip() for v in rest.split(',')]
    return key, values


def convert_atlas_to_new_format(atlas_text: str) -> str:
    """
    Convert atlas text from LibGDX format → Spine format

    Supports:
    -xy + size → bounds
    -orig + offset → offsets (offset default (0,0) if not present)
    -rotate true/false/90/270 Remains the same (new format already supported)
    -page-level key 'size' will not be touched.

    Return:
        Converted atlas text (or original text if conversion is not required)
    """
    lines = atlas_text.splitlines()
    result: list[str] = []

    in_page_header = False   # True = Reading key of page
    in_region = False        # True = Reading region key

    # Buffer accumulates kv of the current region (to include bounds/offsets)
    region_name_line: str = ""
    region_kv: Dict[str, list[str]] = {}
    region_extra_lines: list[str] = []   # Unknown lines (stored as normal output)

    def flush_region() -> None:
        """Flush region buffer → output in new format"""
        nonlocal in_region

        if not region_name_line:
            return

        result.append(region_name_line)

        # --- index ---
        if "index" in region_kv:
            result.append(f"  index: {region_kv['index'][0]}")

        # --- rotate ---
        rotate_val = region_kv.get("rotate", ["false"])[0].strip().lower()
        result.append(f"  rotate: {rotate_val}")

        # --- bounds (x, y, w, h) ---
        if "bounds" in region_kv:
            # If bounds already exists (spine format) use it
            b = region_kv["bounds"]
            result.append(f"  bounds: {b[0]}, {b[1]}, {b[2]}, {b[3]}")
        else:
            # LibGDX format: xy + size
            xy = region_kv.get("xy", ["0", "0"])
            sz = region_kv.get("size", ["0", "0"])
            x, y = xy[0], xy[1]
            w, h = sz[0], sz[1]
            result.append(f"  bounds: {x}, {y}, {w}, {h}")

        # --- offsets (off_x, off_y, orig_w, orig_h) ---
        if "offsets" in region_kv:
            # If offsets already exists (spine format) use it
            o = region_kv["offsets"]
            result.append(f"  offsets: {o[0]}, {o[1]}, {o[2]}, {o[3]}")
        elif "orig" in region_kv:
            # LibGDX format: orig + optional offset
            orig = region_kv["orig"]
            offset = region_kv.get("offset", ["0", "0"])
            off_x, off_y = offset[0], offset[1]
            orig_w, orig_h = orig[0], orig[1]
            result.append(f"  offsets: {off_x}, {off_y}, {orig_w}, {orig_h}")
        # If no pair → Do not output offsets (spine format default is packed = original)

        # --- split / pad ---
        for key in ("split", "pad"):
            if key in region_kv:
                vals = ", ".join(region_kv[key])
                result.append(f"  {key}: {vals}")

        # Unknown region keys → re-emit so they survive conversion
        for key, values in region_kv.items():
            if key not in _REGION_CORE_KEYS:
                result.append(f"  {key}: {', '.join(values)}")

        # Extra lines that we don't know
        result.extend(region_extra_lines)

        in_region = False

    # ---- Main parse loop ----
    for raw_line in lines:
        stripped = raw_line.strip()

        # Empty line
        if not stripped:
            if in_region:
                flush_region()
                region_name_line = ""
                region_kv = {}
                region_extra_lines = []
            in_page_header = False
            result.append(raw_line)
            continue

        # Page filename
        if _is_page_line(stripped):
            if in_region:
                flush_region()
                region_name_line = ""
                region_kv = {}
                region_extra_lines = []
            in_page_header = True
            in_region = False
            result.append(raw_line)
            continue

        # Key-value line
        parsed = _parse_kv(raw_line)
        if parsed:
            key, values = parsed

            if in_page_header:
                # Page-level kv → output directly without touching
                result.append(raw_line)
                # If key is not a page key → probably entered region
                if key not in _PAGE_KEYS:
                    in_page_header = False
                continue

            if in_region:
                # Store region kv to flush later
                region_kv[key] = values
            else:
                # kv without region name prefix (shouldn't happen but safety)
                result.append(raw_line)
            continue

        # No colon → New region name
        if in_region:
            # Flush old region first
            flush_region()
            region_name_line = ""
            region_kv = {}
            region_extra_lines = []

        in_page_header = False
        in_region = True
        region_name_line = raw_line
        region_kv = {}
        region_extra_lines = []

    # Flush last region (if file doesn't end with empty line)
    if in_region:
        flush_region()

    return "\n".join(result)


def is_old_format(atlas_text: str) -> bool:
    """
    Check if this atlas text is old format or not.
    (The key 'xy:', 'orig:', or 'offset:' exists)
    """
    for line in atlas_text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith(("xy:", "orig:", "offset:")):
            return True
    return False


def auto_convert_atlas(atlas_text: str) -> str:
    """
    Auto Convert — If it's old format, it will convert. If it's new format, return the original text.

    Return:
        atlas_text
    """
    if is_old_format(atlas_text):
        return convert_atlas_to_new_format(atlas_text)
    return atlas_text