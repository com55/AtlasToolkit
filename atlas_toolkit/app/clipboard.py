# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Focus-independent Windows image clipboard support."""
import ctypes
from ctypes import wintypes
from io import BytesIO
import sys
import time

from PIL import Image


def _image_to_dib(image: Image.Image) -> bytes:
    """Return a 32-bit BI_RGB DIB, preserving Pillow's BGRA pixel bytes."""
    output = BytesIO()
    image.convert("RGBA").save(output, format="BMP")
    return output.getvalue()[14:]


def png_to_dib(png: bytes) -> bytes:
    with Image.open(BytesIO(png)) as image:
        return _image_to_dib(image)


class _WindowsBackend:
    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = [
            (self.user, "GetDesktopWindow", [], wintypes.HWND),
            (self.user, "OpenClipboard", [wintypes.HWND], wintypes.BOOL),
            (self.user, "EmptyClipboard", [], wintypes.BOOL),
            (self.user, "SetClipboardData", [wintypes.UINT, wintypes.HANDLE], wintypes.HANDLE),
            (self.user, "CloseClipboard", [], wintypes.BOOL),
            (self.user, "RegisterClipboardFormatW", [wintypes.LPCWSTR], wintypes.UINT),
            (self.kernel, "GlobalAlloc", [wintypes.UINT, ctypes.c_size_t], wintypes.HANDLE),
            (self.kernel, "GlobalLock", [wintypes.HANDLE], ctypes.c_void_p),
            (self.kernel, "GlobalUnlock", [wintypes.HANDLE], wintypes.BOOL),
            (self.kernel, "GlobalFree", [wintypes.HANDLE], wintypes.HANDLE),
        ]
        for library, name, args, result in signatures:
            function = getattr(library, name)
            function.argtypes = args
            function.restype = result

    def open(self):
        # EmptyClipboard needs a non-null owner for subsequent SetClipboardData.
        return self.user.OpenClipboard(self.user.GetDesktopWindow())

    def empty(self):
        if not self.user.EmptyClipboard():
            raise ctypes.WinError(ctypes.get_last_error())

    def register_png(self):
        result = self.user.RegisterClipboardFormatW("PNG")
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())
        return result

    def put(self, format_id, data):
        handle = self.kernel.GlobalAlloc(2, len(data))  # GMEM_MOVEABLE
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            pointer = self.kernel.GlobalLock(handle)
            if not pointer:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                ctypes.memmove(pointer, data, len(data))
            finally:
                self.kernel.GlobalUnlock(handle)
            if not self.user.SetClipboardData(format_id, handle):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.kernel.GlobalFree(handle)
            raise
        # Successful SetClipboardData transfers ownership to Windows.

    def close(self):
        if not self.user.CloseClipboard():
            raise ctypes.WinError(ctypes.get_last_error())


def copy_png(png: bytes, *, backend=None) -> None:
    """Publish PNG and CF_DIB together; backend injection permits Linux tests."""
    _publish(png, png_to_dib(png), backend)


def copy_rgba(width: int, height: int, rgba: bytes, *, backend=None) -> None:
    """Publish raw straight-alpha pixels without a decode step. The PNG is
    transient clipboard data, so it takes the fastest compression level."""
    image = Image.frombytes("RGBA", (int(width), int(height)), bytes(rgba))
    output = BytesIO()
    image.save(output, format="PNG", compress_level=1)
    _publish(output.getvalue(), _image_to_dib(image), backend)


def _publish(png: bytes, dib: bytes, backend) -> None:
    if backend is None:
        if sys.platform != "win32":
            raise NotImplementedError("Native image clipboard is only supported on Windows.")
        backend = _WindowsBackend()
    png_format = backend.register_png()
    for attempt in range(5):
        if backend.open():
            break
        if attempt == 4:
            raise OSError("Could not open the clipboard.")
        time.sleep(0.02)
    try:
        backend.empty()
        backend.put(png_format, png)
        backend.put(8, dib)  # CF_DIB
    finally:
        backend.close()
