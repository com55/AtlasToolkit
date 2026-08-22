"""
AtlasDocument — single seam for Spine atlas parse → model → canonical serialize.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

# Region updates: name → (bounds, offsets, rotate)
UpdatedRegionData = Dict[
    str,
    Tuple[Tuple[int, int, int, int], Optional[Tuple[int, int, int, int]], int],
]

_PAGE_KNOWN_KEYS = frozenset({"size", "format", "filter", "repeat", "pma"})


@dataclass
class Region:
    """One sprite entry within a page."""

    name: str  # region key (e.g. "arm#2")
    atlas_name: str
    page_filename: str
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    index: int = -1
    offsets: Optional[Tuple[int, int, int, int]] = None
    rotate: int = 0
    split: Optional[List[int]] = None
    pad: Optional[List[int]] = None
    extra_pairs: List[Tuple[str, List[str]]] = field(default_factory=list)

    @property
    def bounds(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)

    @property
    def page(self) -> str:
        """Page filename alias (same as page_filename)."""
        return self.page_filename

    def bounds_with_rotate(self) -> List[int]:
        return [self.x, self.y, self.w, self.h, self.rotate]

    def to_meta_dict(self) -> Dict[str, object]:
        """Metadata dict for repack / rebuild helpers."""
        return {
            "atlas_name": self.atlas_name or self.name,
            "index": self.index,
            "split": self.split,
            "pad": self.pad,
            "extra_pairs": self.extra_pairs,
        }


@dataclass
class Page:
    """One texture page referenced by the atlas."""

    filename: str
    size: Tuple[int, int] = (0, 0)
    format: str = "RGBA8888"
    filter: Tuple[str, str] = ("Nearest", "Nearest")
    repeat: str = "none"
    pma: bool = False
    scale_x: float = 1.0
    scale_y: float = 1.0
    extra_pairs: List[Tuple[str, List[str]]] = field(default_factory=list)
    regions: List[Region] = field(default_factory=list)


@dataclass
class AtlasDocument:
    pages: List[Page] = field(default_factory=list)

    @classmethod
    def parse(cls, text: str) -> AtlasDocument:
        lines = [line.strip() for line in text.splitlines()]
        iterator = iter(lines)

        pages: List[Page] = []
        page_map: Dict[str, Page] = {}
        region_name_counts: Dict[str, int] = {}

        current_page: Optional[Page] = None
        current_region: Optional[Region] = None

        def unique_region_key(atlas_name: str) -> str:
            nxt = region_name_counts.get(atlas_name, 0) + 1
            region_name_counts[atlas_name] = nxt
            return atlas_name if nxt == 1 else f"{atlas_name}#{nxt}"

        while True:
            try:
                line = next(iterator)
            except StopIteration:
                break

            if not line:
                continue

            if line.endswith(".png"):
                current_page = Page(filename=line)
                pages.append(current_page)
                page_map[line] = current_page
                current_region = None
                continue

            if ":" in line:
                key, value_str = line.split(":", 1)
                key = key.strip().lower()
                values = [v.strip() for v in value_str.split(",")]

                if current_region is not None:
                    if key == "bounds" and len(values) >= 4:
                        current_region.x = int(values[0])
                        current_region.y = int(values[1])
                        current_region.w = int(values[2])
                        current_region.h = int(values[3])
                    elif key == "xy":
                        current_region.x = int(values[0])
                        current_region.y = int(values[1])
                    elif key == "size" and current_region.w == 0:
                        current_region.w = int(values[0])
                        current_region.h = int(values[1])
                    elif key == "rotate":
                        val = values[0].lower()
                        if val == "true":
                            current_region.rotate = 90
                        elif val == "false":
                            current_region.rotate = 0
                        else:
                            try:
                                current_region.rotate = int(val)
                            except ValueError:
                                current_region.rotate = 0
                    elif key == "offsets" and len(values) >= 4:
                        current_region.offsets = tuple(map(int, values[:4]))  # type: ignore[assignment]
                    elif key == "index":
                        current_region.index = int(values[0])
                    elif key == "split" and len(values) >= 4:
                        current_region.split = [int(v) for v in values]
                    elif key == "pad" and len(values) >= 4:
                        current_region.pad = [int(v) for v in values]
                    else:
                        current_region.extra_pairs.append((key, list(values)))
                elif current_page is not None:
                    if key == "size":
                        current_page.size = (int(values[0]), int(values[1]))
                    elif key == "format":
                        current_page.format = values[0]
                    elif key == "filter" and len(values) >= 2:
                        current_page.filter = (values[0], values[1])
                    elif key == "repeat":
                        current_page.repeat = values[0]
                    elif key == "pma":
                        current_page.pma = str(values[0]).strip().lower() == "true"
                    elif key not in _PAGE_KNOWN_KEYS:
                        current_page.extra_pairs.append((key, list(values)))
                continue

            if current_page is None:
                continue

            region_key = unique_region_key(line)
            current_region = Region(
                name=region_key,
                atlas_name=line,
                page_filename=current_page.filename,
            )
            current_page.regions.append(current_region)

        return cls(pages=pages)

    def page_filenames(self) -> List[str]:
        return [p.filename for p in self.pages]

    def region_keys(self) -> List[str]:
        keys: List[str] = []
        for page in self.pages:
            for region in page.regions:
                keys.append(region.name)
        return keys

    def regions_by_key(self) -> Dict[str, Region]:
        out: Dict[str, Region] = {}
        for page in self.pages:
            for region in page.regions:
                out[region.name] = region
        return out

    def first_page_info(self) -> Dict[str, object]:
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

    def with_updates(
        self,
        updated_regions: UpdatedRegionData,
        page_size: Optional[Tuple[int, int]] = None,
    ) -> AtlasDocument:
        """Return a copy with region bounds/offsets/rotate and optional page size applied."""
        new_pages: List[Page] = []
        for page in self.pages:
            new_size = page_size if page_size is not None else page.size
            new_regions: List[Region] = []
            for region in page.regions:
                r = region
                if region.name in updated_regions:
                    bounds, offsets, rotate_val = updated_regions[region.name]
                    r = replace(
                        region,
                        x=bounds[0],
                        y=bounds[1],
                        w=bounds[2],
                        h=bounds[3],
                        offsets=offsets,
                        rotate=rotate_val,
                    )
                new_regions.append(r)
            new_pages.append(
                replace(page, size=new_size, regions=new_regions)
            )
        return AtlasDocument(pages=new_pages)

    @classmethod
    def from_rebuild_args(
        cls,
        page_info: Dict[str, object],
        new_size: Tuple[int, int],
        region_names: List[str],
        region_data: Dict[str, tuple],
    ) -> AtlasDocument:
        page = Page(
            filename=str(page_info.get("page", "atlas.png")),
            size=new_size,
            format=str(page_info.get("format", "RGBA8888")),
            filter=_parse_filter(page_info.get("filter")),
            repeat=str(page_info.get("repeat", "none")),
            pma=bool(page_info.get("pma")),
        )
        regions: List[Region] = []
        for name in region_names:
            if name not in region_data:
                continue
            entry = region_data[name]
            bounds, offsets, rotate_val = entry[0], entry[1], entry[2]
            meta: Dict[str, object] = entry[3] if len(entry) > 3 and entry[3] else {}
            regions.append(
                Region(
                    name=name,
                    atlas_name=str(meta.get("atlas_name") or meta.get("name") or name),
                    page_filename=page.filename,
                    x=bounds[0],
                    y=bounds[1],
                    w=bounds[2],
                    h=bounds[3],
                    offsets=offsets,
                    rotate=rotate_val,
                    index=int(meta["index"]) if isinstance(meta.get("index"), int) else -1,
                    split=list(meta["split"]) if isinstance(meta.get("split"), (list, tuple)) else None,
                    pad=list(meta["pad"]) if isinstance(meta.get("pad"), (list, tuple)) else None,
                    extra_pairs=[
                        (str(p[0]), [str(v) for v in p[1]])
                        for p in meta.get("extra_pairs", [])
                        if p and p[0]
                    ]
                    if isinstance(meta.get("extra_pairs"), (list, tuple))
                    else [],
                )
            )
        page.regions = regions
        return cls(pages=[page])

    def serialize(self) -> str:
        lines: List[str] = []
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
                lines.append(f"{key}: " + ", ".join(str(v) for v in values))

            for region in page.regions:
                lines.extend(_serialize_region(region))

        return "\n".join(lines)


def _parse_filter(value: object) -> Tuple[str, str]:
    if isinstance(value, tuple) and len(value) >= 2:
        return str(value[0]), str(value[1])
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
        if len(parts) >= 2:
            return parts[0], parts[1]
    return "Nearest", "Nearest"


def _format_rotate(rotate_val: int) -> Optional[str]:
    if rotate_val == 90:
        return "true"
    if rotate_val == 180:
        return "180"
    if rotate_val == 270:
        return "270"
    return None


def _is_default_offsets(
    offsets: Optional[Tuple[int, int, int, int]],
    bounds: Tuple[int, int, int, int],
) -> bool:
    if not offsets:
        return True
    return (
        offsets[0] == 0
        and offsets[1] == 0
        and offsets[2] == bounds[2]
        and offsets[3] == bounds[3]
    )


def _is_default_page_format(fmt: object) -> bool:
    return str(fmt or "").upper() == "RGBA8888"


def _is_default_page_filter(flt: object) -> bool:
    if isinstance(flt, tuple):
        return f"{flt[0]},{flt[1]}".replace(" ", "").lower() == "nearest,nearest"
    return "".join(str(flt or "").split()).lower() == "nearest,nearest"


def _is_default_page_repeat(repeat: object) -> bool:
    return str(repeat or "").lower() == "none"


def _serialize_region(region: Region) -> List[str]:
    lines: List[str] = [region.atlas_name]
    if region.index != -1:
        lines.append(f"  index: {region.index}")
    rotate_str = _format_rotate(region.rotate)
    if rotate_str:
        lines.append(f"  rotate: {rotate_str}")
    lines.append(
        f"  bounds: {region.x}, {region.y}, {region.w}, {region.h}"
    )
    bounds = (region.x, region.y, region.w, region.h)
    if region.offsets and not _is_default_offsets(region.offsets, bounds):
        o = region.offsets
        lines.append(f"  offsets: {o[0]}, {o[1]}, {o[2]}, {o[3]}")
    if region.split and len(region.split) >= 4:
        lines.append("  split: " + ", ".join(str(v) for v in region.split))
    if region.pad and len(region.pad) >= 4:
        lines.append("  pad: " + ", ".join(str(v) for v in region.pad))
    for key, values in region.extra_pairs:
        lines.append(f"  {key}: " + ", ".join(str(v) for v in values))
    return lines
