"""Domain models and geometry."""

from atlas_toolkit.core.document import AtlasDocument, Page, Region
from atlas_toolkit.core.overlay import overlay_rect, overlay_rects_for_regions

__all__ = [
    "AtlasDocument",
    "Page",
    "Region",
    "overlay_rect",
    "overlay_rects_for_regions",
]
