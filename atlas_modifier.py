"""
Atlas Mod Merger Module

Merges modified mod images back into the original atlas PNG,
expanding the canvas (right or below, with optional 90° rotation)
and updating region bounds in the atlas file.
The placement strategy that yields the smallest total pixel area is chosen.
"""

from __future__ import annotations

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
                rotate_str = "true" if rotate_val == 90 else "false"
                result.append(f"  rotate: {rotate_str}")
                continue

        result.append(line)

    return "\n".join(result)


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
            # ROTATE_270 in Pillow == 90° clockwise
            mod_img = mod_img.transpose(Image.Transpose.ROTATE_270)
            mod_w, mod_h = mod_h, mod_w  # swap after rotation

        # Create new combined Atlas Image
        merged = Image.new(
            "RGBA", (best.canvas_w, best.canvas_h), (0, 0, 0, 0)
        )
        merged.paste(self.base_image, (0, 0))
        merged.paste(mod_img, (best.paste_x, best.paste_y))

        # --- Prepare data for atlas text update ---
        #
        # In Spine Atlas format when rotate is true (90° CW),
        # bounds are written as (x, y, height, width).
        rotate_val = 90 if best.rotated else 0

        if best.rotated:
            # bounds: (x, y, h_original, w_original) — Spine convention
            atlas_bounds_w = mod_h  # original height before rotation
            atlas_bounds_h = mod_w  # original width before rotation
        else:
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