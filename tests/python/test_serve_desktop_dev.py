# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Socket-free fixture generation contract for the desktop browser harness."""
from pathlib import Path
import subprocess
import sys

from PIL import Image

from atlas_toolkit.document import AtlasDocument


def test_make_fixture(tmp_path):
    root = Path(__file__).resolve().parents[2]
    subprocess.run([sys.executable, str(root / "scripts/serve_desktop_dev.py"),
                    "--make-fixture", str(tmp_path)], check=True, cwd=root)
    for name, names in (("one", ["a", "b"]), ("two", ["c"]), ("missing", ["a"])):
        text = (tmp_path / name / f"{name}.atlas").read_text()
        assert AtlasDocument.parse(text).pages
        assert all(f"\n{region}\n" in text for region in names)
    assert not (tmp_path / "missing/missing.png").exists()
    with Image.open(tmp_path / "one/one.png") as image:
        assert image.size == (9, 7)
        assert image.getpixel((1, 2)) != image.getpixel((5, 1))
    with Image.open(tmp_path / "missing-src/missing.png") as image:
        assert image.size == (9, 7)
    with Image.open(tmp_path / "two/two.png") as image:
        assert image.size == (9, 7)
    with Image.open(tmp_path / "mod.png") as image:
        assert image.size == (3, 2)
