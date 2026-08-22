"""Shelf-pack repack and atlas text emission for single- and multi-page atlases."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PIL import Image

from atlas_toolkit.core.document import AtlasDocument, Page, Region
from atlas_toolkit.core.region_ops import extract_raw_sprite, round_up_to_multiple

log = logging.getLogger(__name__)

Placement = Tuple[str, int, int, int, int, bool]


@dataclass
class RepackSingleResult:
    image: Image.Image
    atlas_text: str


def shelf_pack(
    items: List[Tuple[str, int, int]],
) -> Tuple[int, int, List[Placement]]:
    """Pack rectangles using shelf next-fit with strip-width optimisation."""
    import math

    if not items:
        return 0, 0, []

    def _pack_with_width(
        rects: List[Tuple[str, int, int]],
        strip_w: int,
        allow_rotate: bool,
    ) -> Tuple[int, int, List[Placement]]:
        sorted_rects = sorted(rects, key=lambda r: max(r[1], r[2]), reverse=True)

        placements: List[Placement] = []
        shelf_y = 0
        shelf_h = 0
        cursor_x = 0
        used_w = 0

        for name, w, h in sorted_rects:
            pw, ph, rotated = w, h, False

            if allow_rotate:
                if shelf_h > 0:
                    waste_a = max(0, h - shelf_h) if h <= shelf_h else h - shelf_h
                    waste_b = max(0, w - shelf_h) if w <= shelf_h else w - shelf_h
                    if waste_b < waste_a:
                        pw, ph, rotated = h, w, True
                elif h > w:
                    pw, ph, rotated = h, w, True

            if cursor_x + pw > strip_w and cursor_x > 0:
                shelf_y += shelf_h
                cursor_x = 0
                shelf_h = 0

            placements.append((name, cursor_x, shelf_y, pw, ph, rotated))
            cursor_x += pw
            used_w = max(used_w, cursor_x)
            shelf_h = max(shelf_h, ph)

        return used_w, shelf_y + shelf_h, placements

    max_single = max(max(w, h) for _, w, h in items)
    total_w = sum(max(w, h) for _, w, h in items)
    total_area = sum(w * h for _, w, h in items)
    sqrt_area = int(math.isqrt(total_area))

    candidates: set[int] = {
        max_single,
        total_w,
        max(max_single, sqrt_area),
        max(max_single, int(sqrt_area * 0.8)),
        max(max_single, int(sqrt_area * 1.2)),
        max(max_single, int(sqrt_area * 1.5)),
        max(max_single, int(sqrt_area * 2.0)),
    }
    for mult in range(1, 6):
        candidates.add(max_single * mult)

    best_result: Optional[Tuple[int, int, List[Placement]]] = None
    best_area = float("inf")

    for strip_w in sorted(candidates):
        for allow_rot in (False, True):
            cw, ch, placements = _pack_with_width(items, strip_w, allow_rot)
            area = cw * ch
            if area < best_area:
                best_area = area
                best_result = (cw, ch, placements)

    assert best_result is not None
    cw, ch, placements = best_result
    cw, ch = round_up_to_multiple(cw), round_up_to_multiple(ch)
    log.info(
        "Shelf pack: best %sx%s = %s px² (tried %s widths × 2 rotate modes)",
        cw,
        ch,
        f"{cw * ch:,}",
        len(candidates),
    )
    return cw, ch, placements


def _deduplicate_sprites(
    sprites: Dict[str, Image.Image],
    region_names: List[str],
) -> Dict[str, str]:
    """Map each region name to a canonical name with identical pixels."""
    hash_to_canonical: Dict[str, str] = {}
    canonical_map: Dict[str, str] = {}

    for name in region_names:
        if name not in sprites:
            continue
        pixel_hash = hashlib.md5(
            sprites[name].tobytes(), usedforsecurity=False
        ).hexdigest()

        if pixel_hash in hash_to_canonical:
            canonical = hash_to_canonical[pixel_hash]
            canonical_map[name] = canonical
            log.info("  dedup: '%s' == '%s'", name, canonical)
        else:
            hash_to_canonical[pixel_hash] = name
            canonical_map[name] = name

    return canonical_map


def _paste_packed_sprites(
    unique_names: List[str],
    sprites: Dict[str, Image.Image],
    placement_map: Dict[str, Tuple[int, int, int, int, bool]],
    canvas_w: int,
    canvas_h: int,
) -> Image.Image:
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    for name in unique_names:
        px, py, _pw, _ph, rotated = placement_map[name]
        sprite = sprites[name]
        if rotated:
            sprite = sprite.transpose(Image.Transpose.ROTATE_90)
        canvas.paste(sprite, (px, py))
    return canvas


def repack_single_page(
    merged_image: Image.Image,
    atlas_text: str,
    *,
    deduplicate: bool = True,
) -> RepackSingleResult:
    """Repack all regions on one page; optionally deduplicate identical sprites."""
    page_info, region_names, regions = _parse_atlas(atlas_text)

    sprites: Dict[str, Image.Image] = {
        name: extract_raw_sprite(merged_image, region)
        for name, region in regions.items()
    }
    return repack_from_sprites(
        sprites, atlas_text, page_info=page_info, deduplicate=deduplicate
    )


def repack_from_sprites(
    sprites: Dict[str, Image.Image],
    atlas_text: str,
    *,
    page_info: Optional[Dict[str, object]] = None,
    deduplicate: bool = True,
    full_canvas_regions: Optional[set[str]] = None,
) -> RepackSingleResult:
    """Shelf-pack *sprites* and emit updated single-page atlas text."""
    if page_info is None:
        page_info, region_names, regions = _parse_atlas(atlas_text)
    else:
        _, region_names, regions = _parse_atlas(atlas_text)

    log.info("Repack: packing %s sprites", len(sprites))

    if deduplicate:
        canonical_map = _deduplicate_sprites(sprites, region_names)
        unique_names = list({canonical_map[n] for n in canonical_map})
        log.info(
            "Repack: %s regions → %s unique",
            len(sprites),
            len(unique_names),
        )
    else:
        canonical_map = {n: n for n in region_names if n in sprites}
        unique_names = [n for n in region_names if n in sprites]

    pack_items = [(n, sprites[n].width, sprites[n].height) for n in unique_names]
    canvas_w, canvas_h, placements = shelf_pack(pack_items)
    placement_map = {p[0]: (p[1], p[2], p[3], p[4], p[5]) for p in placements}

    canvas = _paste_packed_sprites(
        unique_names, sprites, placement_map, canvas_w, canvas_h
    )

    region_data: Dict[str, tuple] = {}
    full_canvas = full_canvas_regions or set()
    for name in region_names:
        if name not in canonical_map:
            continue
        canonical = canonical_map[name]
        px, py, _pw, _ph, rotated = placement_map[canonical]
        rotate_val = 90 if rotated else 0
        orig_sprite = sprites[name]
        bounds = (px, py, orig_sprite.width, orig_sprite.height)
        info = regions[name]
        if name in full_canvas:
            offsets: Optional[Tuple[int, int, int, int]] = (
                0,
                0,
                orig_sprite.width,
                orig_sprite.height,
            )
        else:
            offsets = info.offsets
        region_data[name] = (
            bounds,
            offsets,
            rotate_val,
            info.to_meta_dict(),
        )

    new_atlas_text = AtlasDocument.from_rebuild_args(
        page_info or _parse_atlas(atlas_text)[0],
        (canvas_w, canvas_h),
        region_names,
        region_data,
    ).serialize()
    return RepackSingleResult(image=canvas, atlas_text=new_atlas_text)


def _parse_atlas(atlas_text: str):
    doc = AtlasDocument.parse(atlas_text)
    return doc.first_page_info(), doc.region_keys(), doc.regions_by_key()


def repack_multi_page(
    all_sprites: Dict[str, Image.Image],
    num_pages: int,
    page_infos: List[Dict[str, object]],
    region_metas: Dict[str, Dict[str, object]],
    *,
    full_canvas_regions: Optional[set[str]] = None,
) -> Tuple[List[Image.Image], str]:
    """Repack sprites across multiple pages; emit canonical atlas via AtlasDocument."""
    full_canvas = full_canvas_regions or set()
    sprite_names = list(all_sprites.keys())
    if not sprite_names or num_pages == 0:
        return [], ""

    ordered = sorted(
        sprite_names,
        key=lambda n: all_sprites[n].width * all_sprites[n].height,
        reverse=True,
    )
    groups: List[Dict[str, object]] = [
        {"names": [], "area": 0} for _ in range(num_pages)
    ]
    for name in ordered:
        s = all_sprites[name]
        g = min(groups, key=lambda gr: gr["area"])  # type: ignore[index]
        g["names"].append(name)  # type: ignore[attr-defined]
        g["area"] += s.width * s.height  # type: ignore[operator]

    result_pages: List[Image.Image] = []
    doc_pages: List[Page] = []

    for i in range(num_pages):
        names: List[str] = groups[i]["names"]  # type: ignore[assignment]
        pi = page_infos[i] if i < len(page_infos) else page_infos[0]
        page_filename = str(pi.get("page", f"page{i}.png"))

        if not names:
            result_pages.append(Image.new("RGBA", (1, 1), (0, 0, 0, 0)))
            doc_pages.append(
                Page(
                    filename=page_filename,
                    size=(1, 1),
                    format=str(pi.get("format", "RGBA8888")),
                    filter=_page_filter(pi.get("filter")),
                    repeat=str(pi.get("repeat", "none")),
                    pma=bool(pi.get("pma")),
                )
            )
            continue

        items = [(n, all_sprites[n].width, all_sprites[n].height) for n in names]
        canvas_w, canvas_h, placements = shelf_pack(items)
        placement_map = {p[0]: p for p in placements}

        sprites_on_page = {n: all_sprites[n] for n in names}
        pm_for_paste = {
            n: (placement_map[n][1], placement_map[n][2], placement_map[n][3], placement_map[n][4], placement_map[n][5])
            for n in names
            if n in placement_map
        }
        canvas = _paste_packed_sprites(
            names, sprites_on_page, pm_for_paste, canvas_w, canvas_h
        )
        result_pages.append(canvas)

        page_regions: List[Region] = []
        for name in names:
            placement = placement_map.get(name)
            if not placement:
                continue
            _, px, py, _pw, _ph, rotated = placement
            sprite = all_sprites[name]
            meta = region_metas.get(name, {})
            rotate_val = 90 if rotated else 0
            offsets: Optional[Tuple[int, int, int, int]] = None
            if name in full_canvas:
                offsets = (0, 0, sprite.width, sprite.height)
            page_regions.append(
                Region(
                    name=name,
                    atlas_name=str(meta.get("atlas_name") or name),
                    page_filename=page_filename,
                    x=px,
                    y=py,
                    w=sprite.width,
                    h=sprite.height,
                    rotate=rotate_val,
                    offsets=offsets,
                    index=int(meta["index"]) if isinstance(meta.get("index"), int) else -1,
                    split=list(meta["split"]) if isinstance(meta.get("split"), (list, tuple)) else None,
                    pad=list(meta["pad"]) if isinstance(meta.get("pad"), (list, tuple)) else None,
                    extra_pairs=_extra_pairs_from_meta(meta),
                )
            )

        doc_pages.append(
            Page(
                filename=page_filename,
                size=(canvas_w, canvas_h),
                format=str(pi.get("format", "RGBA8888")),
                filter=_page_filter(pi.get("filter")),
                repeat=str(pi.get("repeat", "none")),
                pma=bool(pi.get("pma")),
                regions=page_regions,
            )
        )

    atlas_text = AtlasDocument(pages=doc_pages).serialize()
    return result_pages, atlas_text


def _page_filter(value: object) -> Tuple[str, str]:
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
        if len(parts) >= 2:
            return parts[0], parts[1]
    if isinstance(value, tuple) and len(value) >= 2:
        return str(value[0]), str(value[1])
    return "Nearest", "Nearest"


def _extra_pairs_from_meta(meta: Dict[str, object]) -> List[Tuple[str, List[str]]]:
    pairs = meta.get("extra_pairs")
    if not isinstance(pairs, (list, tuple)):
        return []
    out: List[Tuple[str, List[str]]] = []
    for pair in pairs:
        if not pair or not pair[0]:
            continue
        key = str(pair[0])
        vals = [str(v) for v in (pair[1] if len(pair) > 1 else [])]
        out.append((key, vals))
    return out
