"""AtlasDocument -- single seam for Spine atlas parse -> model -> canonical
serialize. Line-for-line port of www/js/atlas-document.js on the `dev`
branch (the migration oracle for this port). That JS file's own doc
comment references an older Python original on a different branch -- this
port supersedes that file, it does not extend it."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_PAGE_KNOWN_KEYS = {"size", "format", "filter", "repeat", "pma"}


@dataclass
class Region:
    """One sprite entry within a page."""
    name: str          # deduped internal key (e.g. "arm#2")
    atlas_name: str    # literal region name text for output
    page_filename: str
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    index: int = -1
    offsets: Optional[list[int]] = None  # [left, bottom, originalWidth, originalHeight]
    rotate: int = 0  # integer degrees: 0/90/180/270, not a bool
    split: Optional[list[int]] = None
    pad: Optional[list[int]] = None
    extra_pairs: list[tuple[str, list[str]]] = field(default_factory=list)

    @property
    def bounds(self) -> list[int]:
        """Packed pre-rotation bounds as [x, y, w, h]."""
        return [self.x, self.y, self.w, self.h]

    @property
    def page(self) -> str:
        """Page filename alias."""
        return self.page_filename

    def bounds_with_rotate(self) -> list[int]:
        return [self.x, self.y, self.w, self.h, self.rotate]

    def to_meta_dict(self) -> dict:
        """Metadata dict for repack/rebuild helpers."""
        return {
            "atlasName": self.atlas_name or self.name,
            "index": self.index,
            "split": self.split,
            "pad": self.pad,
            "extraPairs": self.extra_pairs,
        }

    def clone(self) -> "Region":
        """Shallow-value clone (deep-copies mutable list fields)."""
        return Region(
            name=self.name,
            atlas_name=self.atlas_name,
            page_filename=self.page_filename,
            x=self.x, y=self.y, w=self.w, h=self.h,
            index=self.index,
            offsets=list(self.offsets) if self.offsets else None,
            rotate=self.rotate,
            split=list(self.split) if self.split else None,
            pad=list(self.pad) if self.pad else None,
            extra_pairs=[(k, list(v)) for k, v in self.extra_pairs],
        )


@dataclass
class Page:
    """One texture page referenced by the atlas."""
    filename: str
    size: list[int] = field(default_factory=lambda: [0, 0])
    format: str = "RGBA8888"
    filter: list[str] = field(default_factory=lambda: ["Nearest", "Nearest"])
    repeat: str = "none"
    pma: bool = False
    # Runtime-only: set when the actually-loaded PNG's pixel size differs
    # from the atlas's declared `size:` line. Never serialized.
    scale_x: float = 1.0
    scale_y: float = 1.0
    extra_pairs: list[tuple[str, list[str]]] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)

    def clone(self) -> "Page":
        return Page(
            filename=self.filename,
            size=list(self.size),
            format=self.format,
            filter=list(self.filter),
            repeat=self.repeat,
            pma=self.pma,
            scale_x=self.scale_x,
            scale_y=self.scale_y,
            extra_pairs=[(k, list(v)) for k, v in self.extra_pairs],
            regions=[r.clone() for r in self.regions],
        )


def _parse_int(s: str, default: int = 0) -> int:
    """Lenient integer parse matching JS's parseInt(s, 10) for the COMMON
    case: consumes a leading optional sign + digit run and ignores
    anything after it. Returns `default` when no leading digits are
    found at all.

    KNOWN, ACCEPTED DIVERGENCE from JS (found by Task 4's scrutinize
    review, ruled on rather than "fixed" -- see revision note below):
    for genuinely non-numeric field text, JS's parseInt returns NaN,
    which then propagates unguarded into serialize() for every call site
    except `rotate` (the only branch with its own explicit
    `Number.isNaN(n) ? 0 : n` guard in the JS source) -- e.g.
    `bounds: abc, 0, 10, 10` serializes as `bounds: NaN, 0, 10, 10` in
    JS. This port substitutes `default` (0) instead, for every call
    site, not just rotate -- NOT byte-identical to JS on that specific
    malformed-input shape. Deliberately accepted, not fixed, because:
    (a) no real Spine/TexturePacker-exported or this-tool-written atlas
    file ever contains non-numeric field text, so the divergence is
    unreachable via any real caller; (b) reproducing NaN would require
    every Region/Page numeric field to accept int-or-float-NaN, breaking
    type consistency across this whole module for a case with zero
    practical benefit. Revisit if a future caller needs true
    byte-identical parity on adversarial/corrupted input (e.g. a
    fuzzer).

    This substitution's reach is wider than one printed token (found
    during re-review of the fix above): `size`'s `current_region.w == 0`
    overwrite guard and `_is_default_offsets`'s zero-check both branch on
    the substituted 0 rather than a real NaN, so a malformed input can
    silently flip which of TWO conflicting `size:`/`offsets:` lines wins,
    or whether an `offsets:` line gets omitted on serialize -- not just
    which number appears. Still unreachable via any real atlas file, same
    reasoning as above; noted here so a future reader doesn't assume the
    divergence is confined to a single value."""
    m = re.match(r"\s*([+-]?\d+)", s)
    return int(m.group(1)) if m else default


def _at(values: list[str], index: int) -> str:
    """Safe list index -- returns "" (which _parse_int's regex simply
    fails to match, so it falls through to `default`) instead of raising
    IndexError when a malformed atlas line has fewer values than a
    2-value field expects (e.g. "xy: 5" with no comma, or a bare
    "size: 100"). Found by Task 4's scrutinize review: the JS source's
    equivalent unguarded array index (`values[1]` on too-short an array)
    yields `undefined`, and `parseInt(undefined, 10)` is `NaN` -- JS
    never throws here. The original Python draft called `values[1]`
    directly and crashed with IndexError on exactly this malformed
    input, a genuinely NEW failure mode JS never had (not a faithfully-
    ported JS bug, a real Python-only regression) -- fixed by routing
    every 2-value field's second index through this helper. Only the
    SECOND index of a comma-split 2-value field needs this: `line[colon+1:]
    .split(",")` on any string, even one with no comma at all, always
    yields at least a 1-element list, so `values[0]` can never be out of
    range for these branches -- only `values[1]` can."""
    return values[index] if index < len(values) else ""


class AtlasDocument:
    def __init__(self, pages: Optional[list[Page]] = None):
        self.pages: list[Page] = pages if pages is not None else []

    @staticmethod
    def parse(text: str) -> "AtlasDocument":
        """Ported directly from AtlasDocument.parse in atlas-document.js.
        Notable specifics preserved:
         - A line ending in exactly ".png" (case-sensitive) starts a new
           Page.
         - Region-name de-duplication (#2, #3, ...) is recomputed fresh
           on every parse and is order-dependent.
         - Malformed/short values for a known REGION key fall through to
           extra_pairs under that key name (if/elif chain ending in a
           catch-all else). Malformed values for a known PAGE key are
           instead silently dropped (explicit "not in known-keys"
           whitelist check, no catch-all). This asymmetry is intentional,
           preserved from the JS source, not "fixed" here."""
        lines = [line.strip() for line in re.split(r"\r\n|\r|\n", text)]

        pages: list[Page] = []
        region_name_counts: dict[str, int] = {}
        current_page: Optional[Page] = None
        current_region: Optional[Region] = None

        def unique_region_key(atlas_name: str) -> str:
            next_n = region_name_counts.get(atlas_name, 0) + 1
            region_name_counts[atlas_name] = next_n
            return atlas_name if next_n == 1 else f"{atlas_name}#{next_n}"

        for line in lines:
            if not line:
                continue

            if line.endswith(".png"):
                current_page = Page(filename=line)
                pages.append(current_page)
                current_region = None
                continue

            colon_idx = line.find(":")
            if colon_idx != -1:
                key = line[:colon_idx].strip().lower()
                values = [v.strip() for v in line[colon_idx + 1:].split(",")]

                if current_region is not None:
                    if key == "bounds" and len(values) >= 4:
                        current_region.x = _parse_int(values[0])
                        current_region.y = _parse_int(values[1])
                        current_region.w = _parse_int(values[2])
                        current_region.h = _parse_int(values[3])
                    elif key == "xy":
                        current_region.x = _parse_int(values[0])
                        current_region.y = _parse_int(_at(values, 1))
                    elif key == "size" and current_region.w == 0:
                        current_region.w = _parse_int(values[0])
                        current_region.h = _parse_int(_at(values, 1))
                    elif key == "rotate":
                        val = (values[0] or "").lower()
                        if val == "true":
                            current_region.rotate = 90
                        elif val == "false":
                            current_region.rotate = 0
                        else:
                            current_region.rotate = _parse_int(val, default=0)
                    elif key == "offsets" and len(values) >= 4:
                        current_region.offsets = [_parse_int(v) for v in values[:4]]
                    elif key == "index":
                        current_region.index = _parse_int(values[0])
                    elif key == "split" and len(values) >= 4:
                        current_region.split = [_parse_int(v) for v in values]
                    elif key == "pad" and len(values) >= 4:
                        current_region.pad = [_parse_int(v) for v in values]
                    else:
                        current_region.extra_pairs.append((key, values))
                elif current_page is not None:
                    if key == "size":
                        current_page.size = [_parse_int(values[0]), _parse_int(_at(values, 1))]
                    elif key == "format":
                        current_page.format = values[0]
                    elif key == "filter" and len(values) >= 2:
                        current_page.filter = [values[0], values[1]]
                    elif key == "repeat":
                        current_page.repeat = values[0]
                    elif key == "pma":
                        current_page.pma = str(values[0]).strip().lower() == "true"
                    elif key not in _PAGE_KNOWN_KEYS:
                        current_page.extra_pairs.append((key, values))
                continue

            if current_page is None:
                continue

            region_key = unique_region_key(line)
            current_region = Region(name=region_key, atlas_name=line, page_filename=current_page.filename)
            current_page.regions.append(current_region)

        return AtlasDocument(pages)

    def page_filenames(self) -> list[str]:
        return [p.filename for p in self.pages]

    def region_keys(self) -> list[str]:
        keys = []
        for page in self.pages:
            for region in page.regions:
                keys.append(region.name)
        return keys

    def regions_by_key(self) -> dict[str, Region]:
        out: dict[str, Region] = {}
        for page in self.pages:
            for region in page.regions:
                out[region.name] = region
        return out

    def first_page_info(self) -> dict:
        if not self.pages:
            return {}
        p = self.pages[0]
        return {
            "page": p.filename,
            "size": f"{p.size[0]},{p.size[1]}",
            "format": p.format,
            "filter": f"{p.filter[0]}, {p.filter[1]}",
            "repeat": p.repeat,
            "pma": bool(p.pma),
        }

    def serialize(self) -> str:
        """Canonicalizing serializer (not byte-preserving) -- see
        atlas-document.js's serialize/serializeRegion for the
        authoritative default-detection rules, reproduced here:
         - Omit format: when it equals the default (RGBA8888).
         - Omit filter: when it equals the default (Nearest, Nearest).
         - Omit repeat: when it equals the default (none).
         - Omit pma: entirely unless true (never emit "pma: false").
         - Omit region index: when it's -1.
         - Omit the region rotate: line entirely when rotate==0; emit 90
           as "true", 180/270 as their literal numbers.
         - Omit offsets: whenever offsets are absent OR equal to
           (0,0,w,h).
         - Pages after the first are separated by a blank line; no other
           blank-line logic."""
        lines: list[str] = []
        for page_idx, page in enumerate(self.pages):
            if page_idx > 0:
                lines.append("")
            lines.append(page.filename)
            lines.append(f"size: {page.size[0]},{page.size[1]}")
            if not _is_default_page_format(page.format):
                lines.append(f"format: {page.format}")
            if not _is_default_page_filter(page.filter):
                lines.append(f"filter: {page.filter[0]}, {page.filter[1]}")
            if not _is_default_page_repeat(page.repeat):
                lines.append(f"repeat: {page.repeat}")
            if page.pma:
                lines.append("pma: true")
            for key, values in page.extra_pairs:
                lines.append(f"{key}: {', '.join(values)}")
            for region in page.regions:
                lines.extend(_serialize_region(region))
        return "\n".join(lines)

    def with_updates(
        self,
        updated_regions: dict[str, tuple[list[int], Optional[list[int]], int]],
        page_size: Optional[list[int]] = None,
    ) -> "AtlasDocument":
        """Return a new AtlasDocument with the given regions'
        bounds/offsets/rotate replaced (and optionally every page's
        declared size replaced), leaving everything else preserved.
        `updated_regions` maps region name -> (bounds[4], offsets|None,
        rotate). Unmatched regions/pages are reused by reference rather
        than deep-cloned -- safe because callers treat the result as a
        fresh snapshot to serialize, not as something to mutate further.
        Mirrors atlas-document.js's withUpdates. ASSUMES each tuple is
        well-formed (bounds has exactly 4 elements) -- same ruling as
        `from_rebuild_args` below, see that docstring for the full
        reachability argument; the only current producer of these tuples
        is always well-formed."""
        new_pages = []
        for page in self.pages:
            new_size = page_size if page_size is not None else page.size
            new_regions = []
            for region in page.regions:
                if region.name in updated_regions:
                    bounds, offsets, rotate_val = updated_regions[region.name]
                    new_regions.append(Region(
                        name=region.name,
                        atlas_name=region.atlas_name,
                        page_filename=region.page_filename,
                        x=bounds[0], y=bounds[1], w=bounds[2], h=bounds[3],
                        index=region.index,
                        offsets=offsets,
                        rotate=rotate_val,
                        split=region.split,
                        pad=region.pad,
                        extra_pairs=region.extra_pairs,
                    ))
                else:
                    new_regions.append(region)
            new_pages.append(Page(
                filename=page.filename,
                size=new_size,
                format=page.format,
                filter=page.filter,
                repeat=page.repeat,
                pma=page.pma,
                scale_x=page.scale_x,
                scale_y=page.scale_y,
                extra_pairs=page.extra_pairs,
                regions=new_regions,
            ))
        return AtlasDocument(new_pages)

    @staticmethod
    def from_rebuild_args(
        page_info: dict,
        new_size: list[int],
        region_names: list[str],
        region_data: dict[str, tuple],
    ) -> "AtlasDocument":
        """Build a full fresh single-page AtlasDocument from scratch
        (used after a complete repack). `page_info` may contain
        'page'/'format'/'filter'/'repeat'/'pma'/'extra_pairs' keys; any ABSENT or None
        key falls back to the same default atlas-document.js's
        fromRebuildArgs uses -- a PRESENT-but-falsy value (e.g. "") is
        used as-is, matching JS's getOr(obj,key,def) exactly (only
        undefined triggers the fallback), not Python's `or` idiom (falls
        back on any falsy value) -- see `_get_or` below, added after
        Task 5's scrutinize review found `page_info.get(k) or default`
        diverges from JS on a present-but-empty string, reachable via
        real (if malformed) atlas text: a bare "format:" line with
        nothing after the colon parses to Page.format == "", and
        AtlasDocument.first_page_info() forwards that shape straight
        into this function's input contract.

        `region_data` maps region name -> (bounds[4], offsets|None,
        rotate, meta?) where meta may carry {atlasName, index, split,
        pad, extraPairs}. ASSUMES each entry is well-formed (bounds has
        exactly 4 elements, meta -- when present -- is a dict whose
        extraPairs entries are 2-tuples) -- this port does not
        independently guard against a malformed region_data shape (a
        short tuple, a non-dict meta) the way parse() must guard against
        malformed raw atlas TEXT (see Task 4's `_at` helper). The current
        metadata producers, Region.to_meta_dict() and AtlasModifier._pack_and_emit()
        (using JS-shaped region_meta/effective-model metadata), emit well-formed
        dictionaries. parse() produces always-2-tuple extra_pairs from its own
        always->=1-element split result; the modifier and wired JS caller build
        4-element bounds. Revisit this assumption if a new caller's
        well-formedness guarantee is weaker than these producers'."""
        page = Page(
            filename=str(_get_or(page_info, "page", "atlas.png")),
            size=new_size,
            format=str(_get_or(page_info, "format", "RGBA8888")),
            filter=_parse_filter(page_info.get("filter")),
            repeat=str(_get_or(page_info, "repeat", "none")),
            pma=bool(page_info.get("pma")),
            extra_pairs=[
                (str(key), [str(value) for value in values])
                for key, values in (page_info.get("extra_pairs") or [])
            ],
        )

        regions = []
        for name in region_names:
            if name not in region_data:
                continue
            entry = region_data[name]
            bounds = entry[0]
            offsets = entry[1]
            rotate_val = entry[2]
            meta = entry[3] if len(entry) > 3 and entry[3] else {}

            raw_index = meta.get("index")
            index = int(raw_index) if _js_is_integer(raw_index) else -1
            split = list(meta["split"]) if isinstance(meta.get("split"), list) else None
            pad = list(meta["pad"]) if isinstance(meta.get("pad"), list) else None
            raw_extra = meta.get("extraPairs")
            extra_pairs = (
                [(str(p[0]), [str(v) for v in (p[1] or [])]) for p in raw_extra if p and p[0]]
                if isinstance(raw_extra, list)
                else []
            )

            regions.append(Region(
                name=name,
                atlas_name=str(meta.get("atlasName") or meta.get("name") or name),
                page_filename=page.filename,
                x=bounds[0], y=bounds[1], w=bounds[2], h=bounds[3],
                offsets=offsets,
                rotate=rotate_val,
                index=index,
                split=split,
                pad=pad,
                extra_pairs=extra_pairs,
            ))
        page.regions = regions
        return AtlasDocument([page])


def _is_default_offsets(offsets: Optional[list[int]], bounds: list[int]) -> bool:
    if not offsets:
        return True
    return (
        offsets[0] == 0 and offsets[1] == 0
        and offsets[2] == bounds[2] and offsets[3] == bounds[3]
    )


def _is_default_page_format(fmt: str) -> bool:
    return str(fmt or "").upper() == "RGBA8888"


def _is_default_page_filter(flt: list[str]) -> bool:
    return f"{flt[0]},{flt[1]}".replace(" ", "").lower() == "nearest,nearest"


def _is_default_page_repeat(repeat: str) -> bool:
    return str(repeat or "").lower() == "none"


def _format_rotate(rotate_val: int) -> Optional[str]:
    if rotate_val == 90:
        return "true"
    if rotate_val == 180:
        return "180"
    if rotate_val == 270:
        return "270"
    return None


def _serialize_region(region: Region) -> list[str]:
    lines = [region.atlas_name]
    if region.index != -1:
        lines.append(f"  index: {region.index}")
    rotate_str = _format_rotate(region.rotate)
    if rotate_str:
        lines.append(f"  rotate: {rotate_str}")
    lines.append(f"  bounds: {region.x}, {region.y}, {region.w}, {region.h}")
    bounds = [region.x, region.y, region.w, region.h]
    if region.offsets and not _is_default_offsets(region.offsets, bounds):
        o = region.offsets
        lines.append(f"  offsets: {o[0]}, {o[1]}, {o[2]}, {o[3]}")
    if region.split and len(region.split) >= 4:
        lines.append(f"  split: {', '.join(str(v) for v in region.split)}")
    if region.pad and len(region.pad) >= 4:
        lines.append(f"  pad: {', '.join(str(v) for v in region.pad)}")
    for key, values in region.extra_pairs:
        lines.append(f"  {key}: {', '.join(values)}")
    return lines


def _get_or(d: dict, key: str, default):
    """Mirrors JS's getOr(obj, key, def) (atlas-document.js:437-442)
    exactly: returns d[key] only when the key is present AND its value
    is not None (JS: not undefined) -- NOT Python's `d.get(key) or
    default` idiom, which also falls back on a present-but-falsy value
    (empty string, 0, False). Added after Task 5's scrutinize review
    found the `or`-based version diverges from JS on a present empty
    string, reachable via real (if malformed) atlas text -- see
    from_rebuild_args's docstring for the exact repro."""
    if key in d and d[key] is not None:
        return d[key]
    return default


def _js_is_integer(value) -> bool:
    """Mirrors JS's Number.isInteger(value): true for a real integer OR
    a float with no fractional part (e.g. 2.0), false for anything else
    -- CRITICALLY, false for a bool, unlike Python's own `isinstance(x,
    int)` (bool is an int subclass in Python, so `isinstance(True, int)`
    is True; JS has no such conflation, `Number.isInteger(true)` is
    False). Added after Task 5's scrutinize review found the original
    `isinstance(meta.get("index"), int)` check silently accepted a bool
    `index` value and serialized it as the literal text "True"/"False"
    -- not valid Spine atlas syntax, real output corruption, not just a
    reachability-gated edge case. Callers must still `int(value)` the
    result themselves when this returns True, to coerce e.g. `2.0` to
    `2` rather than leaving a stray float in an int-typed field."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return value.is_integer()
    return False


def _parse_filter(value) -> list[str]:
    if isinstance(value, list) and len(value) >= 2:
        return [str(value[0]), str(value[1])]
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
        if len(parts) >= 2:
            return [parts[0], parts[1]]
    return ["Nearest", "Nearest"]


def png_names_for_save(
    atlas_text: str, page_count: int, atlas_filename: str = "atlas.atlas"
) -> list[str]:
    """PNG filenames to write next to a saved atlas. Always taken from
    the atlas text's page lines -- not the .atlas stem ("hero.atlas" must
    not become "hero.png" when the page is "hero_spr.png").

    `page_count` is guarded against non-numeric input (fixed after the
    final whole-branch review found `int(page_count)` crashes where real
    JS's `Number(x) || 0` degrades to 0) -- matches this module's
    established crash-vs-silent-divergence standard (see `_at`/`_get_or`).
    Does NOT reproduce JS's float-loop-bound quirk exactly (JS's
    `for(i=0;i<count;i++)` with count=3.7 runs 4 iterations since
    `3<3.7`; Python's `range(int(3.7))` runs 3) -- no real caller passes
    a non-integer page_count, so this narrower gap is accepted, same
    class of acceptance as `_parse_int`'s NaN/0 divergence."""
    names = AtlasDocument.parse(atlas_text or "").page_filenames()
    stem = re.sub(r"\.[^.]+$", "", str(atlas_filename or "atlas")) or "atlas"
    try:
        count = int(page_count) if page_count else 0
    except (TypeError, ValueError):
        count = 0
    out = []
    for i in range(count):
        out.append(names[i] if i < len(names) else (f"{stem}.png" if count == 1 else f"page{i}.png"))
    return out
