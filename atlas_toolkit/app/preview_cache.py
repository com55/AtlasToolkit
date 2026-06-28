"""Write preview PNGs to disk and return file:// URLs for the WebView."""

from __future__ import annotations

import atexit
import os
import tempfile
from pathlib import Path

from PIL.Image import Image

from atlas_toolkit.app.config import get_config_dir


class PreviewCache:
    """Full-resolution preview files — avoids base64 through the pywebview bridge."""

    def __init__(self) -> None:
        cache_root = get_config_dir() / "preview_cache"
        cache_root.mkdir(parents=True, exist_ok=True)
        self._session_dir = tempfile.TemporaryDirectory(
            prefix="session_",
            dir=cache_root,
        )
        atexit.register(self._session_dir.cleanup)
        self._dir = Path(self._session_dir.name)
        self._generation = 0
        self._paths_by_key: dict[str, Path] = {}

    @property
    def directory(self) -> Path:
        return self._dir

    def store_image(self, img: Image, key: str = "preview") -> str:
        """Save *img* to a managed temp file and return a cache-busted file URI."""
        safe_key = "".join(c if c.isalnum() or c in "._-" else "_" for c in key)
        old_path = self._paths_by_key.pop(safe_key, None)
        if old_path is not None:
            try:
                old_path.unlink()
            except OSError:
                pass
        self._generation += 1
        fd, raw_path = tempfile.mkstemp(
            suffix=".png",
            prefix=f"{safe_key}_",
            dir=self._dir,
        )
        path = Path(raw_path)
        try:
            os.close(fd)
            img.save(path, format="PNG", compress_level=1)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        self._paths_by_key[safe_key] = path
        return f"{path.resolve().as_uri()}?v={self._generation}"

    def clear(self) -> None:
        """Drop all tracked preview files for the current session."""
        self._generation += 1
        for path in self._paths_by_key.values():
            try:
                path.unlink()
            except OSError:
                pass
        self._paths_by_key.clear()
