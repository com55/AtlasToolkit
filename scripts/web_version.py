# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Stamp the PWA index.html with the app version from APP_VERSION.

APP_VERSION is the apps' version (exe builds copy it into VERSION); the
library's own version lives in spine_atlas_toolkit/__init__.py. This module
is stdlib-only so CI can run it without installing anything.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

_TITLE_RE = re.compile(r"<title>[^<]*</title>")
_META_RE = re.compile(r'<meta\s+name="app-version"\s+content="[^"]*"\s*/?>', re.I)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_version(root: Path | None = None) -> str:
    root = root or repo_root()
    try:
        version = (root / "APP_VERSION").read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        return "0.0.0"
    return version or "0.0.0"


def stamp_index_html(html: str, version: str) -> str:
    v = version.lstrip("v")
    title = f"<title>Atlas Toolkit v{v}</title>"
    meta = f'<meta name="app-version" content="{v}" />'
    html = _TITLE_RE.sub(title, html, count=1)
    if _META_RE.search(html):
        return _META_RE.sub(meta, html, count=1)
    return html.replace(title, f"{meta}\n    {title}", 1)


def stamp_index_file(index_path: Path, version: str | None = None) -> str:
    v = version or load_version()
    original = index_path.read_text(encoding="utf-8")
    stamped = stamp_index_html(original, v)
    index_path.write_text(stamped, encoding="utf-8")
    return v


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stamp",
        type=Path,
        help="Rewrite this index.html in place with the APP_VERSION version",
    )
    args = parser.parse_args()
    version = load_version()
    if args.stamp:
        stamp_index_file(args.stamp, version)
        print(f"Stamped {args.stamp} with v{version}")
        return
    print(version)


if __name__ == "__main__":
    main()
