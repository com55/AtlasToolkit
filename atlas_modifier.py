"""
Atlas Mod Merger Module

Merges modified mod images back into the original atlas PNG,
expanding the canvas (right or below, with optional 90° rotation)
and updating region bounds in the atlas file.
The placement strategy that yields the smallest total pixel area is chosen.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, NamedTuple, Optional, Tuple

from PIL import Image

if TYPE_CHECKING:
    pass

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


class RegionInfo(NamedTuple):
    """Information about a region parsed from atlas file."""
    name: str
    bounds: Tuple[int, int, int, int]  # x, y, w, h
    offsets: Optional[Tuple[int, int, int, int]]  # off_x, off_y, orig_w, orig_h
    rotate: int  # 0, 90, 180, 270


def parse_atlas(atlas_text: str) -> Tuple[Dict[str, str], List[str], Dict[str, RegionInfo]]:
    """
    Parse atlas file and return page info, order of regions, and region data.

    Args:
        atlas_text: Content of the atlas file.

    Returns:
        Tuple containing:
        - page_info: dict of page metadata
        - region_names: list of names in order
        - regions: dict mapping name to RegionInfo
    """
    page_info: Dict[str, str] = {}
    region_names: List[str] = []
    regions: Dict[str, RegionInfo] = {}

    current_region: Optional[str] = None
    current_bounds: Optional[Tuple[int, int, int, int]] = None
    current_offsets: Optional[Tuple[int, int, int, int]] = None
    current_rotate: int = 0
    page_name: Optional[str] = None

    lines = atlas_text.splitlines()

    for line in lines:
        line = line.strip()

        if not line:
            if current_region and current_bounds:
                regions[current_region] = RegionInfo(
                    name=current_region,
                    bounds=current_bounds,
                    offsets=current_offsets,
                    rotate=current_rotate,
                )
            current_region = None
            current_bounds = None
            current_offsets = None
            current_rotate = 0
            continue

        if line.endswith(".png"):
            page_name = line
            page_info["page"] = page_name
            continue

        if ":" in line and page_name and not region_names:
            key, value = line.split(":", 1)
            page_info[key.strip()] = value.strip()
            continue

        if ":" not in line:
            if current_region and current_bounds:
                regions[current_region] = RegionInfo(
                    name=current_region,
                    bounds=current_bounds,
                    offsets=current_offsets,
                    rotate=current_rotate,
                )
            current_region = line
            region_names.append(line)
            current_bounds = None
            current_offsets = None
            current_rotate = 0
            continue

        if ":" in line and current_region:
            key, value = line.split(":", 1)
            key = key.strip().lower()
            vals = [v.strip() for v in value.split(",")]

            if key == "bounds":
                current_bounds = tuple(map(int, vals))  # type: ignore[assignment]
            elif key == "offsets":
                current_offsets = tuple(map(int, vals))  # type: ignore[assignment]
            elif key == "rotate":
                v0 = vals[0].lower()
                if v0 == "true":
                    current_rotate = 90
                elif v0 == "false":
                    current_rotate = 0
                elif v0.isdigit():
                    current_rotate = int(v0)

    if current_region and current_bounds:
        regions[current_region] = RegionInfo(
            name=current_region,
            bounds=current_bounds,
            offsets=current_offsets,
            rotate=current_rotate,
        )

    return page_info, region_names, regions


# Type alias for updated region data: (bounds, offsets, rotate)
UpdatedRegionData = Dict[
    str,
    Tuple[Tuple[int, int, int, int], Optional[Tuple[int, int, int, int]], int],
]


def update_atlas_text(
    atlas_text: str,
    new_size: Tuple[int, int],
    updated_regions: UpdatedRegionData,
) -> str:
    """
    Reconstructs the atlas text with updated bounds/offsets for specific regions.
    """
    lines = atlas_text.splitlines()
    result: List[str] = []
    current_region: Optional[str] = None
    in_page_header = False

    for line in lines:
        stripped = line.strip()

        if stripped.endswith(".png"):
            result.append(line)
            in_page_header = True
            continue

        if in_page_header:
            if stripped.startswith("size:"):
                result.append(f"size: {new_size[0]},{new_size[1]}")
                continue
            if ":" not in stripped and stripped:
                in_page_header = False

        if ":" not in stripped and stripped and not stripped.endswith(".png"):
            current_region = stripped
            result.append(line)
            continue

        if current_region in updated_regions:
            new_bounds, new_offsets, rotate_val = updated_regions[current_region]

            if stripped.startswith("bounds:"):
                result.append(
                    f"  bounds: {new_bounds[0]}, {new_bounds[1]}, "
                    f"{new_bounds[2]}, {new_bounds[3]}"
                )
                continue

            if stripped.startswith("offsets:"):
                if new_offsets:
                    result.append(
                        f"  offsets: {new_offsets[0]}, {new_offsets[1]}, "
                        f"{new_offsets[2]}, {new_offsets[3]}"
                    )
                continue

            if stripped.startswith("rotate:"):
                if rotate_val == 90:
                    rotate_str = "true"
                elif rotate_val == 180:
                    rotate_str = "180"
                elif rotate_val == 270:
                    rotate_str = "270"
                else:
                    rotate_str = "false"
                result.append(f"  rotate: {rotate_str}")
                continue

        result.append(line)

    return "\n".join(result)



def rebuild_atlas_text(
    page_info: Dict[str, str],
    new_size: Tuple[int, int],
    region_names: List[str],
    region_data: Dict[str, Tuple[Tuple[int, int, int, int], Optional[Tuple[int, int, int, int]], int]],
) -> str:
    """
    Build a complete atlas text from scratch.

    Args:
        page_info: Original page metadata (must contain 'page').
        new_size: (width, height) of the new canvas.
        region_names: Ordered list of region names.
        region_data: Mapping of name → (bounds, offsets, rotate).
    """
    lines: List[str] = []

    # Blank line before page header (Spine convention)
    lines.append("")
    lines.append(page_info.get("page", "atlas.png"))
    lines.append(f"size: {new_size[0]},{new_size[1]}")

    # Reproduce other page-level keys (filter, format, repeat, etc.)
    for key, value in page_info.items():
        if key in ("page", "size"):
            continue
        lines.append(f"{key}: {value}")

    for name in region_names:
        if name not in region_data:
            continue
        bounds, offsets, rotate_val = region_data[name]
        lines.append(name)
        if rotate_val == 90:
            rotate_str = "true"
        elif rotate_val == 180:
            rotate_str = "180"
        elif rotate_val == 270:
            rotate_str = "270"
        else:
            rotate_str = "false"
        lines.append(f"  rotate: {rotate_str}")
        lines.append(
            f"  bounds: {bounds[0]}, {bounds[1]}, {bounds[2]}, {bounds[3]}"
        )
        if offsets:
            lines.append(
                f"  offsets: {offsets[0]}, {offsets[1]}, "
                f"{offsets[2]}, {offsets[3]}"
            )
        else:
            lines.append(
                f"  offsets: 0, 0, {bounds[2]}, {bounds[3]}"
            )
        lines.append(f"  index: -1")

    return "\n".join(lines)


class _PlacementOption(NamedTuple):
    """A candidate placement for the mod image."""

    label: str
    canvas_w: int
    canvas_h: int
    paste_x: int
    paste_y: int
    rotated: bool  # True = mod image rotated 90° CW before pasting


class AtlasModifier:
    """Handles merging mod images into an atlas and saving the result."""

    def __init__(self, atlas_text: str, atlas_path: Path, base_image: Image.Image) -> None:
        self.atlas_text = atlas_text
        self.atlas_path = atlas_path
        self.base_image = base_image.convert("RGBA")

        _, self.region_names, self.regions = parse_atlas(atlas_text)

    # ------------------------------------------------------------------ #
    #  Placement strategy                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _find_best_placement(
        base_w: int, base_h: int, mod_w: int, mod_h: int
    ) -> _PlacementOption:
        """
        Evaluate 4 placement strategies and return the one with the
        smallest total canvas area (width × height).

        Strategies:
          1. right           — mod appended to the right
          2. right + rotate  — mod rotated 90° CW then appended to the right
          3. below           — mod appended below
          4. below + rotate  — mod rotated 90° CW then appended below
        """
        # After 90° CW rotation, width/height swap.
        rot_w, rot_h = mod_h, mod_w

        candidates: List[_PlacementOption] = [
            _PlacementOption(
                label="right",
                canvas_w=base_w + mod_w,
                canvas_h=max(base_h, mod_h),
                paste_x=base_w,
                paste_y=0,
                rotated=False,
            ),
            _PlacementOption(
                label="right+rotated",
                canvas_w=base_w + rot_w,
                canvas_h=max(base_h, rot_h),
                paste_x=base_w,
                paste_y=0,
                rotated=True,
            ),
            _PlacementOption(
                label="below",
                canvas_w=max(base_w, mod_w),
                canvas_h=base_h + mod_h,
                paste_x=0,
                paste_y=base_h,
                rotated=False,
            ),
            _PlacementOption(
                label="below+rotated",
                canvas_w=max(base_w, rot_w),
                canvas_h=base_h + rot_h,
                paste_x=0,
                paste_y=base_h,
                rotated=True,
            ),
        ]

        best = min(candidates, key=lambda c: c.canvas_w * c.canvas_h)

        for c in candidates:
            area = c.canvas_w * c.canvas_h
            tag = " ← best" if c is best else ""
            logging.info(
                f"  {c.label:20s}  {c.canvas_w}x{c.canvas_h} = {area:,} px²{tag}"
            )

        return best

    # ------------------------------------------------------------------ #
    #  Merge                                                               #
    # ------------------------------------------------------------------ #

    def merge_mod_image(
        self, mod_image_path: Path, selected_regions: List[str]
    ) -> Tuple[Image.Image, str]:
        """
        Merges a mod image onto the base atlas canvas for the selected regions.

        The placement strategy (right / below, with optional 90° rotation)
        that yields the smallest total canvas area is chosen automatically.

        Returns:
            Tuple of (merged PIL Image, new atlas text).
        """
        mod_img = Image.open(mod_image_path).convert("RGBA")

        base_w, base_h = self.base_image.size
        mod_w, mod_h = mod_img.size

        logging.info(f"Base: {base_w}x{base_h}, Mod: {mod_w}x{mod_h}")

        # Determine original canvas dimensions from the first selected region
        orig_canvas_w, orig_canvas_h = mod_w, mod_h

        first_region = self.regions.get(selected_regions[0])
        if first_region and first_region.offsets:
            orig_canvas_w = first_region.offsets[2]
            orig_canvas_h = first_region.offsets[3]

        # Pad mod image to original canvas size if needed
        if mod_w != orig_canvas_w or mod_h != orig_canvas_h:
            logging.info(
                f"Padding mod image to original canvas: "
                f"{orig_canvas_w}x{orig_canvas_h}"
            )
            padded_mod = Image.new(
                "RGBA", (orig_canvas_w, orig_canvas_h), (0, 0, 0, 0)
            )
            padded_mod.paste(mod_img, (0, orig_canvas_h - mod_h))
            mod_img = padded_mod
            mod_w, mod_h = orig_canvas_w, orig_canvas_h

        # --- Find the best placement ---
        best = self._find_best_placement(base_w, base_h, mod_w, mod_h)
        logging.info(f"Chosen placement: {best.label}")

        # Rotate the mod image if the best strategy requires it
        if best.rotated:
            # ROTATE_90 in Pillow == 90° counter-clockwise
            mod_img = mod_img.transpose(Image.Transpose.ROTATE_90)
            mod_w, mod_h = mod_h, mod_w  # swap after rotation

        # Create new combined Atlas Image
        merged = Image.new(
            "RGBA", (best.canvas_w, best.canvas_h), (0, 0, 0, 0)
        )
        merged.paste(self.base_image, (0, 0))
        merged.paste(mod_img, (best.paste_x, best.paste_y))

        # --- Prepare data for atlas text update ---
        #
        # PIL ROTATE_90 = 90° counter-clockwise
        # In Spine Atlas format:
        #   bounds always store ORIGINAL dimensions (before rotation)
        #   Extractor will swap w/h when cropping if rotated
        rotate_val = 90 if best.rotated else 0

        # Bounds use ORIGINAL dimensions - no swap!
        atlas_bounds_w = mod_w
        atlas_bounds_h = mod_h

        updated_regions_data: UpdatedRegionData = {}

        for name in selected_regions:
            new_bounds = (
                best.paste_x,
                best.paste_y,
                atlas_bounds_w,
                atlas_bounds_h,
            )
            new_offsets = (0, 0, atlas_bounds_w, atlas_bounds_h)
            updated_regions_data[name] = (new_bounds, new_offsets, rotate_val)

        new_atlas_text = update_atlas_text(
            self.atlas_text,
            (best.canvas_w, best.canvas_h),
            updated_regions_data,
        )

        return merged, new_atlas_text

    def save(self, output_dir: Path, merged_image: Image.Image, atlas_text: str) -> Path:
        """
        Save the merged PNG, updated atlas text, and copy .skel if it exists.

        Returns:
            The output directory path.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save merged PNG (using the original base PNG's filename)
        base_png_name = self.atlas_path.with_suffix(".png").name
        merged_png_path = output_dir / base_png_name
        merged_image.save(merged_png_path)

        # Save updated atlas text
        merged_atlas_path = output_dir / self.atlas_path.name
        merged_atlas_path.write_text(atlas_text, encoding="utf-8")

        # Copy .skel if it exists
        skel_path = self.atlas_path.with_suffix(".skel")
        if skel_path.exists():
            shutil.copy(skel_path, output_dir / skel_path.name)
            logging.info("Copied .skel file.")

        logging.info(f"Saved merged files to: {output_dir}")
        return output_dir

    # ------------------------------------------------------------------ #
    #  Repack                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_raw_sprite(
        image: Image.Image, region: RegionInfo
    ) -> Image.Image:
        """
        Crop the raw sprite from *image* according to *region* bounds/rotate.

        Returns the sprite in its **unrotated** orientation (the visual pixel
        data the artist intended), without any offset padding.
        """
        x, y, raw_w, raw_h = region.bounds
        rot = region.rotate

        # When rotated 90/270, the atlas stores w/h swapped
        crop_w = raw_h if rot in (90, 270) else raw_w
        crop_h = raw_w if rot in (90, 270) else raw_h

        sprite = image.crop((x, y, x + crop_w, y + crop_h))

        # Undo atlas rotation to get the original orientation
        if rot == 90:
            sprite = sprite.transpose(Image.Transpose.ROTATE_270)
        elif rot == 270:
            sprite = sprite.transpose(Image.Transpose.ROTATE_90)
        elif rot == 180:
            sprite = sprite.transpose(Image.Transpose.ROTATE_180)

        return sprite

    @staticmethod
    def _shelf_pack(
        items: List[Tuple[str, int, int]],
    ) -> Tuple[int, int, List[Tuple[str, int, int, int, int, bool]]]:
        """
        Pack rectangles using Shelf Next-Fit Decreasing Height with
        automatic strip-width optimisation.

        Tries multiple candidate strip widths and picks the layout that
        yields the smallest bounding-box area.

        Args:
            items: list of (name, width, height).

        Returns:
            (canvas_w, canvas_h, placements)
            where each placement is (name, x, y, placed_w, placed_h, rotated).
        """
        import math

        if not items:
            return 0, 0, []

        def _pack_with_width(
            rects: List[Tuple[str, int, int]],
            strip_w: int,
            allow_rotate: bool,
        ) -> Tuple[int, int, List[Tuple[str, int, int, int, int, bool]]]:
            """Pack *rects* into shelves limited to *strip_w* pixels wide."""
            # Sort by height descending — tall items first for better shelves
            sorted_rects = sorted(
                rects, key=lambda r: max(r[1], r[2]), reverse=True
            )

            placements: List[Tuple[str, int, int, int, int, bool]] = []
            shelf_y = 0
            shelf_h = 0
            cursor_x = 0
            used_w = 0

            for name, w, h in sorted_rects:
                pw, ph, rotated = w, h, False

                if allow_rotate:
                    # Pick the orientation that better fits the shelf height.
                    # If the shelf already has a height, prefer the orientation
                    # whose height is closer to shelf_h (to waste less).
                    if shelf_h > 0:
                        # Option A: as-is
                        waste_a = max(0, h - shelf_h) if h <= shelf_h else h - shelf_h
                        # Option B: rotated
                        waste_b = max(0, w - shelf_h) if w <= shelf_h else w - shelf_h
                        if waste_b < waste_a:
                            pw, ph, rotated = h, w, True
                    else:
                        # No shelf yet — prefer landscape (shorter height)
                        if h > w:
                            pw, ph, rotated = h, w, True

                # Does it fit in current shelf?
                if cursor_x + pw > strip_w and cursor_x > 0:
                    # Start a new shelf
                    shelf_y += shelf_h
                    cursor_x = 0
                    shelf_h = 0

                placements.append(
                    (name, cursor_x, shelf_y, pw, ph, rotated)
                )
                cursor_x += pw
                used_w = max(used_w, cursor_x)
                shelf_h = max(shelf_h, ph)

            canvas_h = shelf_y + shelf_h
            return used_w, canvas_h, placements

        # --- Determine candidate strip widths to try ---
        max_single = max(max(w, h) for _, w, h in items)
        total_w = sum(max(w, h) for _, w, h in items)
        total_area = sum(w * h for _, w, h in items)
        sqrt_area = int(math.isqrt(total_area))

        # Build a set of candidate widths between the widest item and total
        candidates: set[int] = set()
        candidates.add(max_single)
        candidates.add(total_w)
        candidates.add(max(max_single, sqrt_area))
        candidates.add(max(max_single, int(sqrt_area * 0.8)))
        candidates.add(max(max_single, int(sqrt_area * 1.2)))
        candidates.add(max(max_single, int(sqrt_area * 1.5)))
        candidates.add(max(max_single, int(sqrt_area * 2.0)))

        # Also add widths based on multiples of the widest item
        for mult in range(1, 6):
            candidates.add(max_single * mult)

        best_result: Optional[
            Tuple[int, int, List[Tuple[str, int, int, int, int, bool]]]
        ] = None
        best_area = float("inf")

        for strip_w in sorted(candidates):
            for allow_rot in (False, True):
                cw, ch, placements = _pack_with_width(
                    items, strip_w, allow_rot
                )
                area = cw * ch
                if area < best_area:
                    best_area = area
                    best_result = (cw, ch, placements)

        assert best_result is not None
        cw, ch, _ = best_result
        logging.info(
            f"  Shelf pack: best {cw}x{ch} = {cw * ch:,} px²  "
            f"(tried {len(candidates)} widths × 2 rotate modes)"
        )
        return best_result

    def repack(
        self,
        merged_image: Image.Image,
        atlas_text: str,
    ) -> Tuple[Image.Image, str]:
        """
        Repack all regions from *merged_image* into a new optimally-sized
        canvas, deduplicating regions that share identical pixel data.

        Args:
            merged_image: The image output from ``merge_mod_image``.
            atlas_text: The atlas text output from ``merge_mod_image``.

        Returns:
            Tuple of (repacked PIL Image, new atlas text).
        """
        page_info, region_names, regions = parse_atlas(atlas_text)

        # ---- 1. Extract raw sprites ----
        sprites: Dict[str, Image.Image] = {}
        for name, info in regions.items():
            sprites[name] = self._extract_raw_sprite(merged_image, info)

        logging.info(f"Repack: extracted {len(sprites)} sprites")

        # ---- 2. Deduplicate by pixel hash ----
        hash_to_canonical: Dict[str, str] = {}  # hash → first name
        canonical_map: Dict[str, str] = {}      # name → canonical name

        for name in region_names:
            if name not in sprites:
                continue
            pixel_hash = hashlib.md5(
                sprites[name].tobytes(), usedforsecurity=False
            ).hexdigest()

            if pixel_hash in hash_to_canonical:
                canonical = hash_to_canonical[pixel_hash]
                canonical_map[name] = canonical
                logging.info(f"  dedup: '{name}' == '{canonical}'")
            else:
                hash_to_canonical[pixel_hash] = name
                canonical_map[name] = name

        unique_names = list(hash_to_canonical.values())
        logging.info(
            f"Repack: {len(sprites)} regions → {len(unique_names)} unique"
        )

        # ---- 3. Bin-pack unique sprites ----
        pack_items: List[Tuple[str, int, int]] = [
            (n, sprites[n].width, sprites[n].height) for n in unique_names
        ]
        canvas_w, canvas_h, placements = self._shelf_pack(pack_items)

        logging.info(f"Repack: canvas {canvas_w}x{canvas_h}")

        # ---- 4. Build placement lookup ----
        #  name → (x, y, placed_w, placed_h, rotated)
        placement_map: Dict[str, Tuple[int, int, int, int, bool]] = {}
        for name, px, py, pw, ph, rotated in placements:
            placement_map[name] = (px, py, pw, ph, rotated)

        # ---- 5. Paste sprites onto new canvas ----
        canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

        for name in unique_names:
            px, py, pw, ph, rotated = placement_map[name]
            sprite = sprites[name]
            if rotated:
                sprite = sprite.transpose(Image.Transpose.ROTATE_90)
            canvas.paste(sprite, (px, py))

        # ---- 6. Build region data for atlas text ----
        region_data: Dict[
            str,
            Tuple[Tuple[int, int, int, int], Optional[Tuple[int, int, int, int]], int],
        ] = {}

        for name in region_names:
            if name not in canonical_map:
                continue
            canonical = canonical_map[name]
            px, py, pw, ph, rotated = placement_map[canonical]
            # PIL ROTATE_90 = 90° CCW
            rotate_val = 90 if rotated else 0

            orig_sprite = sprites[name]
            orig_w, orig_h = orig_sprite.width, orig_sprite.height

            # Bounds always use ORIGINAL dimensions - no swap!
            bounds = (px, py, orig_w, orig_h)

            offsets: Optional[Tuple[int, int, int, int]] = (
                0, 0, bounds[2], bounds[3]
            )
            region_data[name] = (bounds, offsets, rotate_val)

        new_atlas_text = rebuild_atlas_text(
            page_info, (canvas_w, canvas_h), region_names, region_data
        )

        return canvas, new_atlas_text