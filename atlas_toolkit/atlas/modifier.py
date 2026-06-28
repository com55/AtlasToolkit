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
from typing import Dict, List, NamedTuple, Optional, Tuple

from PIL import Image

from atlas_toolkit.core.document import (
    AtlasDocument,
    Region,
    UpdatedRegionData,
)
from atlas_toolkit.atlas.repacker import repack_from_sprites, repack_single_page
from atlas_toolkit.core.region_ops import extract_raw_sprite


def parse_atlas(
    atlas_text: str,
) -> Tuple[Dict[str, object], List[str], Dict[str, Region]]:
    doc = AtlasDocument.parse(atlas_text)
    return doc.first_page_info(), doc.region_keys(), doc.regions_by_key()


logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def update_atlas_text(
    atlas_text: str,
    new_size: Tuple[int, int],
    updated_regions: UpdatedRegionData,
) -> str:
    """Apply region and page-size updates, then emit canonical atlas text."""
    doc = AtlasDocument.parse(atlas_text)
    return doc.with_updates(updated_regions, page_size=new_size).serialize()


def rebuild_atlas_text(
    page_info: Dict[str, object],
    new_size: Tuple[int, int],
    region_names: List[str],
    region_data: Dict[str, tuple],
) -> str:
    """Build canonical atlas text from page metadata and region data."""
    return AtlasDocument.from_rebuild_args(
        page_info, new_size, region_names, region_data
    ).serialize()


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
        self.atlas_path = atlas_path
        self.base_image = base_image.convert("RGBA")

        # Scale atlas coordinates to match real image size (if mismatched)
        self.atlas_text = self._scale_atlas_text(atlas_text)
        _, self.region_names, self.regions = parse_atlas(self.atlas_text)

    def adopt_merge_result(self, image: Image.Image, atlas_text: str) -> None:
        """Use a merge/repack output as the base for subsequent modifications."""
        self.base_image = image.convert("RGBA")
        self.atlas_text = atlas_text
        _, self.region_names, self.regions = parse_atlas(self.atlas_text)

    def _scale_atlas_text(self, atlas_text: str) -> str:
        """If image size differs from atlas page size, return atlas text
        with all coordinates scaled to match the real image."""
        page_info, _, regions = parse_atlas(atlas_text)
        size_str = page_info.get("size")
        if not isinstance(size_str, str):
            return atlas_text

        atlas_w, atlas_h = (int(v.strip()) for v in size_str.split(","))
        real_w, real_h = self.base_image.size

        if real_w == atlas_w and real_h == atlas_h:
            return atlas_text

        sx = real_w / atlas_w
        sy = real_h / atlas_h
        logging.info(
            f"Modifier: scaling atlas coords "
            f"(Atlas={atlas_w}x{atlas_h} → Image={real_w}x{real_h})"
        )

        updated: UpdatedRegionData = {}
        for name, info in regions.items():
            x, y, w, h = info.bounds
            new_bounds = (round(x * sx), round(y * sy), round(w * sx), round(h * sy))
            new_offsets: Optional[Tuple[int, int, int, int]] = None
            if info.offsets:
                ox, oy, ow, oh = info.offsets
                new_offsets = (round(ox * sx), round(oy * sy), round(ow * sx), round(oh * sy))
            updated[name] = (new_bounds, new_offsets, info.rotate)

        return update_atlas_text(atlas_text, (real_w, real_h), updated)

    # ------------------------------------------------------------------ #
    #  Placement strategy                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _find_best_placement(
        base_w: int,
        base_h: int,
        mod_w: int,
        mod_h: int,
        *,
        allow_rotate: bool = True,
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

        if not allow_rotate:
            candidates = [c for c in candidates if not c.rotated]

        best = min(candidates, key=lambda c: c.canvas_w * c.canvas_h)

        for c in candidates:
            area = c.canvas_w * c.canvas_h
            tag = " ← best" if c is best else ""
            logging.info(
                f"  {c.label:20s}  {c.canvas_w}x{c.canvas_h} = {area:,} px²{tag}"
            )

        return best

    @staticmethod
    def _canvas_size_match(
        mod_w: int,
        mod_h: int,
        canvas_w: int,
        canvas_h: int,
        tolerance: float = 0.02,
    ) -> bool:
        """True when *mod* dimensions match *canvas* within rounding tolerance."""
        if canvas_w <= 0 or canvas_h <= 0:
            return False
        dw = abs(mod_w - canvas_w)
        dh = abs(mod_h - canvas_h)
        return (
            dw <= max(2, round(canvas_w * tolerance))
            and dh <= max(2, round(canvas_h * tolerance))
        )

    def _resolve_mod_canvas(
        self,
        selected_regions: List[str],
        mod_w: int,
        mod_h: int,
    ) -> Tuple[int, int, int, int, int, int, bool]:
        """
        Derive target canvas size and padding anchor for a mod image.

        Returns:
            (orig_canvas_w, orig_canvas_h, base_orig_w, base_orig_h,
             off_x, off_y, is_full_canvas)
        """
        canvas_sizes: set[Tuple[int, int]] = set()
        regions_with_offsets: List[Region] = []
        for name in selected_regions:
            region = self.regions.get(name)
            if region and region.offsets:
                canvas_sizes.add((region.offsets[2], region.offsets[3]))
                regions_with_offsets.append(region)

        if regions_with_offsets:

            def _anchor_key(r: Region) -> tuple[int, int, int]:
                o = r.offsets
                assert o is not None
                return (o[0] + o[1], o[0], o[1])

            base_orig_w, base_orig_h = next(iter(canvas_sizes))
            anchor = min(regions_with_offsets, key=_anchor_key)
            anchor_off = anchor.offsets
            assert anchor_off is not None
            off_x, off_y = anchor_off[0], anchor_off[1]
            orig_canvas_w, orig_canvas_h = base_orig_w, base_orig_h
        else:
            base_orig_w, base_orig_h = mod_w, mod_h
            off_x, off_y = 0, 0
            orig_canvas_w, orig_canvas_h = mod_w, mod_h

        shared_canvas = len(canvas_sizes) == 1 and len(selected_regions) > 1

        # Detect proportional scale (e.g. mod is 2x the expected canvas)
        if (
            orig_canvas_w > 0
            and orig_canvas_h > 0
            and (mod_w != orig_canvas_w or mod_h != orig_canvas_h)
        ):
            ratio_w = mod_w / orig_canvas_w
            ratio_h = mod_h / orig_canvas_h
            if abs(ratio_w - ratio_h) < 0.05 and not (0.95 < ratio_w < 1.05):
                mod_scale = (ratio_w + ratio_h) / 2
                orig_canvas_w = round(orig_canvas_w * mod_scale)
                orig_canvas_h = round(orig_canvas_h * mod_scale)
                logging.info(
                    f"Mod image scale: {mod_scale:.3f}x "
                    f"(canvas → {orig_canvas_w}x{orig_canvas_h})"
                )
            else:
                orig_canvas_w = mod_w
                orig_canvas_h = mod_h

        is_full_canvas = shared_canvas or self._canvas_size_match(
            mod_w, mod_h, orig_canvas_w, orig_canvas_h
        )
        if is_full_canvas:
            orig_canvas_w, orig_canvas_h = mod_w, mod_h
            off_x, off_y = 0, 0

        return (
            orig_canvas_w,
            orig_canvas_h,
            base_orig_w,
            base_orig_h,
            off_x,
            off_y,
            is_full_canvas,
        )

    def _selected_share_canvas(self, selected_regions: List[str]) -> bool:
        """True when every selected region shares one logical canvas size."""
        sizes: set[Tuple[int, int]] = set()
        for name in selected_regions:
            region = self.regions.get(name)
            if not region or not region.offsets:
                return False
            sizes.add((region.offsets[2], region.offsets[3]))
        return len(sizes) == 1 and len(selected_regions) > 1

    # ------------------------------------------------------------------ #
    #  Merge                                                               #
    # ------------------------------------------------------------------ #

    def merge_mod_image(
        self,
        mod_image_path: Path,
        selected_regions: List[str],
        *,
        prepared_mod: Optional[Tuple[Image.Image, int, int, bool]] = None,
    ) -> Tuple[Image.Image, str]:
        """
        Merges a mod image onto the base atlas canvas for the selected regions.

        The placement strategy (right / below, with optional 90° rotation)
        that yields the smallest total canvas area is chosen automatically.

        Returns:
            Tuple of (merged PIL Image, new atlas text).
        """
        if not selected_regions:
            raise ValueError("No regions selected for modification")

        if prepared_mod is not None:
            mod_img, mod_w, mod_h, shared_canvas_mod = prepared_mod
        else:
            mod_img, mod_w, mod_h, shared_canvas_mod = self._prepare_mod_image(
                mod_image_path, selected_regions
            )

        base_w, base_h = self.base_image.size

        # --- Find the best placement ---
        best = self._find_best_placement(
            base_w,
            base_h,
            mod_w,
            mod_h,
            allow_rotate=not shared_canvas_mod,
        )
        logging.info(f"Chosen placement: {best.label}")

        # Rotate the mod image if the best strategy requires it
        if best.rotated:
            # ROTATE_90 in Pillow == 90° counter-clockwise
            mod_img = mod_img.transpose(Image.Transpose.ROTATE_90)

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
            # Full-canvas mod (typical extract output): packed size equals
            # original size — no whitespace stripping, omit offsets in atlas.
            new_offsets = (0, 0, atlas_bounds_w, atlas_bounds_h)
            updated_regions_data[name] = (
                new_bounds,
                new_offsets,
                rotate_val,
            )

        new_atlas_text = update_atlas_text(
            self.atlas_text,
            (best.canvas_w, best.canvas_h),
            updated_regions_data,
        )

        return merged, new_atlas_text

    def _prepare_mod_image(
        self, mod_image_path: Path, selected_regions: List[str]
    ) -> Tuple[Image.Image, int, int, bool]:
        """Load and pad a mod image for the selected regions' logical canvas."""
        mod_img = Image.open(mod_image_path).convert("RGBA")

        base_w, base_h = self.base_image.size
        mod_w, mod_h = mod_img.size

        logging.info(f"Base: {base_w}x{base_h}, Mod: {mod_w}x{mod_h}")

        (
            orig_canvas_w,
            orig_canvas_h,
            base_orig_w,
            base_orig_h,
            off_x_orig,
            off_y_orig,
            is_full_canvas,
        ) = self._resolve_mod_canvas(selected_regions, mod_w, mod_h)

        if is_full_canvas:
            logging.info("Mod image treated as full canvas replacement")

        if not is_full_canvas and (
            mod_w != orig_canvas_w or mod_h != orig_canvas_h
        ):
            scale_x = (orig_canvas_w / base_orig_w) if base_orig_w > 0 else 1
            scale_y = (orig_canvas_h / base_orig_h) if base_orig_h > 0 else 1
            paste_x = round(off_x_orig * scale_x)
            paste_y = orig_canvas_h - mod_h - round(off_y_orig * scale_y)
            logging.info(
                f"Padding mod image to canvas: "
                f"{orig_canvas_w}x{orig_canvas_h} at ({paste_x}, {paste_y})"
            )
            padded_mod = Image.new(
                "RGBA", (orig_canvas_w, orig_canvas_h), (0, 0, 0, 0)
            )
            padded_mod.paste(mod_img, (paste_x, paste_y))
            mod_img = padded_mod
            mod_w, mod_h = orig_canvas_w, orig_canvas_h

        shared_canvas_mod = (
            is_full_canvas and self._selected_share_canvas(selected_regions)
        )
        if shared_canvas_mod:
            logging.info(
                "Shared logical canvas: all selected regions use one atlas area"
            )

        return mod_img, mod_w, mod_h, shared_canvas_mod

    def repack_with_modded_sprites(
        self,
        modded_sprites: Dict[str, Image.Image],
        *,
        full_canvas_regions: Optional[set[str]] = None,
    ) -> Tuple[Image.Image, str]:
        """Unpack base sprites, overlay *modded_sprites*, then shelf-pack."""
        sprites: Dict[str, Image.Image] = {
            name: extract_raw_sprite(self.base_image, region)
            for name, region in self.regions.items()
        }
        logging.info("Repack: extracted %s sprites from base", len(sprites))
        for name, sprite in modded_sprites.items():
            if name in sprites:
                sprites[name] = sprite
        result = repack_from_sprites(
            sprites,
            self.atlas_text,
            full_canvas_regions=full_canvas_regions,
        )
        return result.image, result.atlas_text

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

    def repack(
        self,
        merged_image: Image.Image,
        atlas_text: str,
    ) -> Tuple[Image.Image, str]:
        """Repack all regions from an already-merged canvas (legacy helper)."""
        result = repack_single_page(merged_image, atlas_text)
        return result.image, result.atlas_text