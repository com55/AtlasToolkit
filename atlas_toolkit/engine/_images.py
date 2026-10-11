"""Private raster ownership facade used by :mod:`engine.session`."""
from __future__ import annotations

import numpy as np

from atlas_toolkit.raster.base import ImageHandle, RasterBackend


class _SessionRaster:
    """Forward a backend while tracking handles allocated through this facade."""

    def __init__(self, backend: RasterBackend) -> None:
        self._backend = backend
        self._owned: set[ImageHandle] = set()

    def _track(self, handle: ImageHandle) -> ImageHandle:
        self._owned.add(handle)
        return handle

    def decode_png(self, data: bytes) -> ImageHandle:
        return self._track(self._backend.decode_png(data))

    def encode_png(self, handle: ImageHandle) -> bytes:
        return self._backend.encode_png(handle)

    def create(self, width: int, height: int) -> ImageHandle:
        return self._track(self._backend.create(width, height))

    def size(self, handle: ImageHandle) -> tuple[int, int]:
        return self._backend.size(handle)

    def crop_rotate(self, handle: ImageHandle, x: int, y: int, w: int, h: int, rotate: int) -> ImageHandle:
        return self._track(self._backend.crop_rotate(handle, x, y, w, h, rotate))

    def rotate_image(self, handle: ImageHandle, degrees: int) -> ImageHandle:
        return self._track(self._backend.rotate_image(handle, degrees))

    def paste(self, dest: ImageHandle, src: ImageHandle, x: int, y: int) -> None:
        self._backend.paste(dest, src, x, y)

    def rasterize_mesh_mask(self, uvs: list[float], triangles: list[int], width: int, height: int) -> ImageHandle:
        return self._track(self._backend.rasterize_mesh_mask(uvs, triangles, width, height))

    def composite_destination_in(
        self, dest: ImageHandle, mask: ImageHandle, src_rect: tuple[int, int, int, int] | None = None,
    ) -> None:
        self._backend.composite_destination_in(dest, mask, src_rect)

    def read_alpha_mask(self, handle: ImageHandle) -> np.ndarray:
        return self._backend.read_alpha_mask(handle)

    def get_pixels(self, handle: ImageHandle) -> bytes:
        return self._backend.get_pixels(handle)

    def clone(self, handle: ImageHandle) -> ImageHandle:
        return self._track(self._backend.clone(handle))

    def adopt(self, native: object) -> ImageHandle:
        return self._track(self._backend.adopt(native))

    def export(self, handle: ImageHandle) -> object:
        return self._backend.export(handle)

    def resize_lanczos(self, handle: ImageHandle, width: int, height: int) -> ImageHandle:
        return self._track(self._backend.resize_lanczos(handle, width, height))

    def dispose(self, handle: ImageHandle) -> None:
        if handle in self._owned:
            self._backend.dispose(handle)
            self._owned.remove(handle)

    def checkpoint(self) -> frozenset[ImageHandle]:
        return frozenset(self._owned)

    def release_except(self, keep: frozenset[ImageHandle]) -> None:
        for handle in tuple(self._owned - keep):
            self.dispose(handle)
