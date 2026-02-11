"""
Atlas Mod Merger Script

Merges modified mod images back into the original atlas PNG,
expanding the canvas horizontally and updating region bounds in the atlas file.
"""

import logging
import shutil
from pathlib import Path
from typing import NamedTuple, List, Dict, Tuple, Optional

from PIL import Image

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
    # Using specific type hints for current attributes
    current_bounds: Optional[Tuple[int, int, int, int]] = None
    current_offsets: Optional[Tuple[int, int, int, int]] = None
    current_rotate: int = 0
    page_name: Optional[str] = None

    lines = atlas_text.splitlines()

    for line in lines:
        line = line.strip()

        if not line:
            # End of block, save current region if exists
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

        # Page header
        if line.endswith(".png"):
            page_name = line
            page_info["page"] = page_name
            continue

        # Page metadata (has colon, valid page name, no regions yet)
        if ":" in line and page_name and not region_names:
            key, value = line.split(":", 1)
            page_info[key.strip()] = value.strip()
            continue

        # New region name (no colon, not a png)
        if ":" not in line:
            # Save previous region
            if current_region and current_bounds:
                regions[current_region] = RegionInfo(
                    name=current_region,
                    bounds=current_bounds,
                    offsets=current_offsets,
                    rotate=current_rotate,
                )
            current_region = line
            region_names.append(line)
            # Reset region-specific vars
            current_bounds = None
            current_offsets = None
            current_rotate = 0
            continue

        # Region properties
        if ":" in line and current_region:
            key, value = line.split(":", 1)
            key = key.strip().lower()
            vals = [v.strip() for v in value.split(",")]

            if key == "bounds":
                current_bounds = tuple(map(int, vals)) # type: ignore
            elif key == "offsets":
                current_offsets = tuple(map(int, vals)) # type: ignore
            elif key == "rotate":
                v0 = vals[0].lower()
                if v0 == "true":
                    current_rotate = 90
                elif v0 == "false":
                    current_rotate = 0
                elif v0.isdigit():
                    current_rotate = int(v0)

    # Save the very last region
    if current_region and current_bounds:
        regions[current_region] = RegionInfo(
            name=current_region,
            bounds=current_bounds,
            offsets=current_offsets,
            rotate=current_rotate,
        )

    return page_info, region_names, regions


def update_atlas_text(
    atlas_text: str,
    new_size: Tuple[int, int],
    updated_regions: Dict[str, Tuple[Tuple[int, int, int, int], Optional[Tuple[int, int, int, int]]]],
) -> str:
    """
    Reconstructs the atlas text with updated bounds/offsets for specific regions.
    """
    lines = atlas_text.splitlines()
    result: List[str] = []
    current_region: Optional[str] = None
    in_page_header = False

    iterator = iter(lines)
    
    for line in iterator:
        stripped = line.strip()

        # Page header handling
        if stripped.endswith(".png"):
            result.append(line)
            in_page_header = True
            continue

        if in_page_header:
            if stripped.startswith("size:"):
                # Update the page size
                result.append(f"size: {new_size[0]},{new_size[1]}")
                continue
            if ":" not in stripped and stripped:
                # Exited header
                in_page_header = False
        
        # Region Name Detection
        if ":" not in stripped and stripped and not stripped.endswith(".png"):
            current_region = stripped
            result.append(line)
            continue

        # Modify properties if it's a target region
        if current_region in updated_regions:
            new_bounds, new_offsets = updated_regions[current_region]
            
            if stripped.startswith("bounds:"):
                result.append(f"  bounds: {new_bounds[0]}, {new_bounds[1]}, {new_bounds[2]}, {new_bounds[3]}")
                continue
            
            if stripped.startswith("offsets:"):
                if new_offsets:
                    result.append(f"  offsets: {new_offsets[0]}, {new_offsets[1]}, {new_offsets[2]}, {new_offsets[3]}")
                # If no new offsets but line exists, we might want to keep or remove. 
                # Assuming keeping implies updating if data exists.
                continue
            
            if stripped.startswith("rotate:"):
                # Modded images are usually upright (not rotated)
                result.append("  rotate: false")
                continue

        # Keep original line if no changes needed
        result.append(line)

    return "\n".join(result)


def merge_mod_image(
    base_png_path: Path,
    mod_png_path: Path,
    atlas_path: Path,
    selected_regions: List[str],
    regions: Dict[str, RegionInfo],
    output_dir: Path,
) -> Tuple[Path, Path]:
    """
    Merges mod image onto the base canvas and updates atlas data.
    """
    base_img = Image.open(base_png_path).convert("RGBA")
    mod_img = Image.open(mod_png_path).convert("RGBA")

    base_w, base_h = base_img.size
    mod_w, mod_h = mod_img.size

    logging.info(f"Base: {base_w}x{base_h}, Mod: {mod_w}x{mod_h}")

    # Determine original canvas dimensions from the first selected region
    # This is critical for animation scaling
    orig_canvas_w, orig_canvas_h = mod_w, mod_h
    
    first_region = regions.get(selected_regions[0])
    if first_region and first_region.offsets:
        # offsets[2] is orig_w, offsets[3] is orig_h
        orig_canvas_w = first_region.offsets[2]
        orig_canvas_h = first_region.offsets[3]
    
    # Logic: The mod image is usually the full unpacked sprite.
    # We append it to the right of the base atlas.
    
    # Check if mod image needs padding to match original canvas
    if mod_w != orig_canvas_w or mod_h != orig_canvas_h:
        logging.info(f"Padding mod image to original canvas: {orig_canvas_w}x{orig_canvas_h}")
        padded_mod = Image.new("RGBA", (orig_canvas_w, orig_canvas_h), (0, 0, 0, 0))
        # Default pivot logic: bottom-left align (Spine standard) 
        # But PIL paste is top-left.
        # If mod_img is the full character content, we usually center or align bottom.
        # Assuming mod_img provided IS the replacement frame intended to be full size.
        padded_mod.paste(mod_img, (0, orig_canvas_h - mod_h))
        mod_img = padded_mod
        mod_w, mod_h = orig_canvas_w, orig_canvas_h

    # Create new combined Atlas Image
    new_w = base_w + mod_w
    new_h = max(base_h, mod_h)
    
    merged = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
    merged.paste(base_img, (0, 0))
    merged.paste(mod_img, (base_w, 0)) # Paste mod at the end

    # Prepare data for text update
    atlas_text = atlas_path.read_text(encoding="utf-8")
    updated_regions_data = {}

    for name in selected_regions:
        # Bounds: X starts at base_w (where we pasted), Y=0, W=mod_w, H=mod_h
        new_bounds = (base_w, 0, mod_w, mod_h)
        
        # Offsets: Since mod_img is full size, offsets are 0,0 and size is mod_w, mod_h
        new_offsets = (0, 0, mod_w, mod_h)
        
        updated_regions_data[name] = (new_bounds, new_offsets)

    # Save Files
    output_dir.mkdir(parents=True, exist_ok=True)
    
    new_atlas_text = update_atlas_text(atlas_text, (new_w, new_h), updated_regions_data)
    
    merged_png_path = output_dir / base_png_path.name
    merged.save(merged_png_path)
    
    merged_atlas_path = output_dir / atlas_path.name
    merged_atlas_path.write_text(new_atlas_text, encoding="utf-8")

    logging.info(f"Saved merged files to: {output_dir}")
    return merged_png_path, merged_atlas_path


def select_file(title: str, filetypes: List[Tuple[str, str]]) -> Optional[Path]:
    """Wrapper for file dialog."""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path_str = filedialog.askopenfilename(title=title, filetypes=filetypes)
    root.destroy()
    return Path(path_str) if path_str else None


def main():
    print("=== Atlas Mod Merger ===")
    
    # 1. Inputs
    atlas_path = select_file("Select .atlas file", [("Atlas", "*.atlas")])
    if not atlas_path:
        return

    base_png_path = atlas_path.with_suffix('.png')
    if not base_png_path.exists():
        print(f"Base PNG not found: {base_png_path}")
        base_png_path = select_file("Select Base PNG", [("PNG", "*.png")])
        if not base_png_path: return

    mod_png_path = select_file("Select Mod PNG (New Sprite)", [("PNG", "*.png")])
    if not mod_png_path:
        return

    # 2. Parse
    _, names, regions = parse_atlas(atlas_path.read_text(encoding='utf-8'))

    # 3. Select Region
    print(f"\nFound {len(names)} regions.")
    target_name = input("Enter region name to replace (exact match): ").strip()
    
    if target_name not in regions:
        print(f"Region '{target_name}' not found!")
        return

    # 4. Merge
    output_dir = atlas_path.parent / "merged_output"
    merge_mod_image(
        base_png_path, 
        mod_png_path, 
        atlas_path, 
        [target_name], 
        regions, 
        output_dir
    )
    
    # Optional: Copy skel
    skel_path = atlas_path.with_suffix('.skel')
    if skel_path.exists():
        shutil.copy(skel_path, output_dir / skel_path.name)
        print("Copied .skel file.")

if __name__ == "__main__":
    main()