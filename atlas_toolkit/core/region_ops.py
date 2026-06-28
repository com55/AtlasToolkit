"""Crop and extract operations on atlas Region instances."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PIL import Image

from atlas_toolkit.core.document import Page, Region

if TYPE_CHECKING:
    pass


def crop_and_rotate(
    image: Image.Image, x: int, y: int, w: int, h: int, rotate: int
) -> Image.Image:
    """Crop a region from *image* and undo atlas stored rotation."""
    crop_w = h if rotate in (90, 270) else w
    crop_h = w if rotate in (90, 270) else h

    sprite = image.crop((x, y, x + crop_w, y + crop_h))

    if rotate == 90:
        sprite = sprite.transpose(Image.Transpose.ROTATE_270)
    elif rotate == 270:
        sprite = sprite.transpose(Image.Transpose.ROTATE_90)
    elif rotate == 180:
        sprite = sprite.transpose(Image.Transpose.ROTATE_180)

    return sprite


def extract_raw_sprite(image: Image.Image, region: Region) -> Image.Image:
    """Crop unrotated sprite pixels — no offset padding."""
    x, y, w, h = region.bounds
    return crop_and_rotate(image, x, y, w, h, region.rotate)


def extract_region_from_page(
    page_image: Image.Image,
    region: Region,
    page: Optional[Page] = None,
) -> Image.Image:
    """Extract a region with page scale factors and offset restoration applied."""
    x, y, raw_w, raw_h = region.x, region.y, region.w, region.h
    rot = region.rotate

    if page and (page.scale_x != 1.0 or page.scale_y != 1.0):
        sx, sy = page.scale_x, page.scale_y
        x = round(x * sx)
        y = round(y * sy)
        raw_w = round(raw_w * sx)
        raw_h = round(raw_h * sy)

    sprite = crop_and_rotate(page_image, x, y, raw_w, raw_h, rot)
    current_w, current_h = sprite.size

    if not region.offsets:
        return sprite

    off_x, off_y, orig_w, orig_h = region.offsets
    if page and (page.scale_x != 1.0 or page.scale_y != 1.0):
        sx, sy = page.scale_x, page.scale_y
        off_x = round(off_x * sx)
        off_y = round(off_y * sy)
        orig_w = round(orig_w * sx)
        orig_h = round(orig_h * sy)

    canvas = Image.new("RGBA", (orig_w, orig_h), (0, 0, 0, 0))
    paste_x = off_x
    paste_y = orig_h - off_y - current_h
    canvas.paste(sprite, (paste_x, paste_y))
    return canvas
