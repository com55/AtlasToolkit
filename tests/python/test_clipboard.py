# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
from io import BytesIO
import struct

from PIL import Image
import pytest

from atlas_toolkit.app.clipboard import copy_png, png_to_dib, _WindowsBackend


def png_bytes():
    output = BytesIO()
    Image.new("RGBA", (3, 2), (20, 40, 60, 80)).save(output, format="PNG")
    return output.getvalue()


def test_dib_header_and_pixels():
    dib = png_to_dib(png_bytes())
    size, width, height, planes, bits, compression, length = struct.unpack_from("<IiiHHII", dib)
    assert (size, width, height, planes, compression) == (40, 3, 2, 1, 0)
    assert bits in (24, 32)
    assert len(dib) - size == length == ((width * bits + 31) // 32) * 4 * height
    assert bits == 32
    assert dib[40:44] == bytes([60, 40, 20, 80])


@pytest.mark.parametrize("failure", [None, "empty", "png", "dib"])
def test_session_order_and_close(failure):
    calls = []
    class Backend:
        def register_png(self):
            calls.append("register")
            return 99
        def open(self):
            calls.append("open")
            return True
        def empty(self):
            calls.append("empty")
            if failure == "empty":
                raise OSError("empty")
        def put(self, fmt, data):
            name = "png" if fmt == 99 else "dib"
            calls.append(name)
            assert data == (png_bytes() if fmt == 99 else png_to_dib(png_bytes()))
            if failure == name:
                raise OSError(name)
        def close(self):
            calls.append("close")
    if failure:
        with pytest.raises(OSError, match=failure):
            copy_png(png_bytes(), backend=Backend())
    else:
        copy_png(png_bytes(), backend=Backend())
    expected = ["register", "open", "empty", "png", "dib"]
    if failure:
        expected = expected[:expected.index(failure) + 1]
    assert calls == expected + ["close"]


def test_retry_open(monkeypatch):
    calls = []
    monkeypatch.setattr("atlas_toolkit.app.clipboard.time.sleep", lambda delay: None)
    class Backend:
        def register_png(self): return 99
        def open(self):
            calls.append("open")
            return len(calls) == 3
        def empty(self): pass
        def put(self, fmt, data): pass
        def close(self): calls.append("close")
    copy_png(png_bytes(), backend=Backend())
    assert calls == ["open"] * 3 + ["close"]


def test_failed_transfer_frees_memory():
    from types import SimpleNamespace
    calls = []
    backend = object.__new__(_WindowsBackend)
    backend.kernel = SimpleNamespace(
        GlobalAlloc=lambda flags, size: 123,
        GlobalLock=lambda handle: 456,
        GlobalUnlock=lambda handle: calls.append(("unlock", handle)),
        GlobalFree=lambda handle: calls.append(("free", handle)),
    )
    backend.user = SimpleNamespace(SetClipboardData=lambda fmt, handle: 0)
    from atlas_toolkit.app import clipboard
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(clipboard.ctypes, "memmove", lambda *args: None)
        patch.setattr(clipboard.ctypes, "get_last_error", lambda: 5, raising=False)
        patch.setattr(clipboard.ctypes, "WinError", lambda code: OSError(code, "failed"), raising=False)
        with pytest.raises(OSError):
            backend.put(99, b"png")
    assert calls == [("unlock", 123), ("free", 123)]


def test_copy_rgba_publishes_the_same_pixels():
    from io import BytesIO
    from PIL import Image
    from atlas_toolkit.app.clipboard import copy_rgba
    image = Image.new("RGBA", (3, 2))
    image.putdata([(x * 40, y * 90, 7, 50 + x * 60 + y) for y in range(2) for x in range(3)])
    published = {}

    class Backend:
        def register_png(self): return 99
        def open(self): return True
        def empty(self): pass
        def put(self, fmt, data): published[fmt] = data
        def close(self): pass

    copy_rgba(3, 2, image.tobytes(), backend=Backend())
    assert Image.open(BytesIO(published[99])).convert("RGBA").tobytes() == image.tobytes()
    assert published[8] == png_to_dib(published[99])
    with pytest.raises(ValueError):
        copy_rgba(3, 3, image.tobytes(), backend=Backend())
