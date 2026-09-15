"""PillowRasterBackend -- the only concrete RasterBackend Phase 3 ships.

Implements atlas_toolkit.raster.base.RasterBackend's full non-mesh surface.
rasterize_mesh_mask and composite_destination_in raise NotImplementedError
-- see base.py's docstrings on those two methods for why (a separate,
later "mesh" stage per the architecture spec's §7 ordering).
"""
from __future__ import annotations

import io

from PIL import Image

from atlas_toolkit.raster.base import ImageHandle


class PillowRasterBackend:
    def __init__(self) -> None:
        self._images: dict[ImageHandle, Image.Image] = {}
        self._next_handle = 1

    def _store(self, img: Image.Image) -> ImageHandle:
        handle = ImageHandle(self._next_handle)
        self._next_handle += 1
        self._images[handle] = img
        return handle

    def decode_png(self, data: bytes) -> ImageHandle:
        img = Image.open(io.BytesIO(data))
        img.load()  # force decode while the BytesIO buffer is still alive
        return self._store(img.convert("RGBA"))

    def encode_png(self, handle: ImageHandle) -> bytes:
        buf = io.BytesIO()
        self._images[handle].save(buf, format="PNG")
        return buf.getvalue()

    def create(self, width: int, height: int) -> ImageHandle:
        return self._store(Image.new("RGBA", (width, height), (0, 0, 0, 0)))

    def size(self, handle: ImageHandle) -> tuple[int, int]:
        return self._images[handle].size

    def clone(self, handle: ImageHandle) -> ImageHandle:
        return self._store(self._images[handle].copy())

    def dispose(self, handle: ImageHandle) -> None:
        del self._images[handle]
