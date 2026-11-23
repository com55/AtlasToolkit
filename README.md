<div align="center">

<img src="https://raw.githubusercontent.com/com55/AtlasToolkit/main/www/icons/icon-192.png" width="96" alt="AtlasToolkit icon">

# AtlasToolkit

**View, extract, edit, and repack Spine texture atlases.**

One Python engine. In your browser, on your desktop, or in your own scripts.

[![Open Web App](https://img.shields.io/badge/Open_Web_App-0ea5e9?style=for-the-badge&logo=googlechrome&logoColor=white)](https://com55.github.io/AtlasToolkit/)
[![Download for Windows](https://img.shields.io/badge/Download_for_Windows-2563eb?style=for-the-badge&logo=github&logoColor=white)](https://github.com/com55/AtlasToolkit/releases/latest)
[![Python Library](https://img.shields.io/badge/Python_Library-f5c542?style=for-the-badge&logo=python&logoColor=222222)](packages/spine-atlas-toolkit/README.md)

[![App release](https://img.shields.io/github/v/release/com55/AtlasToolkit?label=App%20%26%20Web&color=2563eb)](https://github.com/com55/AtlasToolkit/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/com55/AtlasToolkit/total?label=downloads&color=2563eb)](https://github.com/com55/AtlasToolkit/releases)
[![PyPI](https://img.shields.io/pypi/v/spine-atlas-toolkit?logo=pypi&logoColor=white&label=PyPI&color=3776ab)](https://pypi.org/project/spine-atlas-toolkit/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/spine-atlas-toolkit?label=downloads&color=3776ab)](https://pypi.org/project/spine-atlas-toolkit/)

[![License](https://img.shields.io/badge/License-Apache_2.0-22a06b)](LICENSE)

[Features](#features) · [Get started](#get-started) · [Packing](#packing) · [Python library](#python-library) · [Documentation](#documentation)

</div>

AtlasToolkit opens a Spine `.atlas` and its texture pages so you can inspect
regions, extract sprites, replace artwork, and save an updated atlas. Load a
matching `.skel` to crop images to their meshes and pack regions by their outlines.

Files are processed locally: in your browser for the web app, and on your
computer for the desktop app.

## Features

| Feature | What you can do |
|---|---|
| **View & extract** | Preview one region or several together. Export individual PNGs, save a combined image, or copy the preview to the clipboard. |
| **Edit regions** | Replace sprites with your own images, or add, rename, and remove regions. |
| **Flexible packing** | Choose Auto, Shelf, Rectangles, or Polygons to arrange edited regions. |
| **Keep your layout** | Preserve existing positions when an edit fits, with automatic repacking when needed. |
| **Mesh support** | Crop previews and extracts to mesh outlines, and nest regions using their mesh geometry. |
| **Composite editing** | Edit several regions as one image, then split the replacement back into individual pieces. |
| **Multi-page atlases** | Work with atlases containing multiple texture pages. |
| **Web & desktop** | Use the browser app, install it as a PWA, or download the Windows app. |
| **Python library** | Use the same engine to read, extract, edit, and repack atlases in your scripts. |

## Get started

| Use it your way | Start here |
|---|---|
| **Web app** | [Open AtlasToolkit](https://com55.github.io/AtlasToolkit/) — no installation required. Install it as a PWA for offline use. |
| **Windows installer** | Download `AtlasToolkit-Setup-x64.exe` from [Releases](https://github.com/com55/AtlasToolkit/releases/latest). Installs per user and supports in-app updates. |
| **Windows portable** | Download `AtlasToolkit-Windows-x64-portable.zip`, extract it, and run `AtlasToolkit.exe`. Download a new ZIP to update. |
| **Python** | Run `pip install spine-atlas-toolkit`, then follow the [library guide](packages/spine-atlas-toolkit/README.md). |

### Open → Select → Edit → Save

1. **Open** your `.atlas` and its referenced PNG page images. In the app,
   select them together; include the matching `.skel` if you want mesh support.
2. **Select** regions to preview them. Use **Extract Selected** or **Extract All**
   to export sprites.
3. Switch to **Edit Mode**, then drop a replacement PNG on the preview or use
   **Modify Selected**. You can also add, rename, or remove regions.
4. Review the result and packing settings, then choose **Save As…** to export
   the updated `.atlas` and page images.

## Packing

Choose a packer in Edit Mode's **Settings** panel. The info buttons explain
each option with illustrated examples.

| Packer | How it arranges regions |
|---|---|
| **Auto** | Uses Polygons when usable mesh data is available; otherwise uses Rectangles. |
| **Shelf** | Places regions in rows. |
| **Rectangles** | Packs regions by their rectangular bounds. |
| **Polygons** | Nests regions by their mesh outlines. Requires usable mesh data from a `.skel`. |

<p align="center">
  <img src="https://github.com/user-attachments/assets/9abdd300-0689-4304-b17d-15325599cd1e" width="600" alt="Illustrated comparison of Shelf, Rectangles, and Polygons packing">
  <br>
  <sub>The same regions, arranged with three different packers.</sub>
</p>

### Try to Keep Layout

Enabled by default. An edited page keeps its existing positions when the edit
fits, and new regions go into available space. If the layout cannot accommodate
the changes, the page is repacked. Turn it off to always repack edited pages.

<p align="center">
  <img src="https://github.com/user-attachments/assets/bd757f22-7e2d-4b79-8b4e-41008841238d" width="600" alt="Comparison of the original atlas and edited results with Try to Keep Layout enabled and disabled">
  <br>
  <sub>Keep the original arrangement when your changes fit.</sub>
</p>

Settings also include **Mesh-Aware**, **Share Composite Image**, **Padding**,
and **Bleed**.

## Python library

The engine is available on PyPI as
[`spine-atlas-toolkit`](https://pypi.org/project/spine-atlas-toolkit/).

```bash
pip install spine-atlas-toolkit
```

```python
import spine_atlas_toolkit as sat

atlas = sat.SpineAtlas.open("hero.atlas")

# Extract a region as a PNG.
atlas.extract("head").save("head.png")

# Replace its artwork and save an updated atlas.
atlas.replace({"head": "new_head.png"})
atlas.save("out/hero.atlas")
```

Requires **Python 3.11+**. The library and apps have separate version numbers;
the app's About dialog shows both.

See the [library guide](packages/spine-atlas-toolkit/README.md) for pack options, mesh cropping, premultiplied alpha,
multi-page workflows, and the API reference.

## Compatibility

| Input | Support |
|---|---|
| Spine `.atlas` + PNG texture pages | View, extract, edit, and repack. |
| Multi-page atlases | Supported. |
| LibGDX-format atlases | Converted on load. |
| Binary `.skel` files | Mesh data from Spine **3.8.x** and **4.2.x**. |
| Atlas without a `.skel` | Viewing, extraction, and editing remain available; mesh cropping and polygon packing need usable mesh data. |

The `.skel` versions above describe **mesh parsing support**. They do not limit
all atlas operations to those Spine versions.

## Documentation

| Document | Contents |
|---|---|
| [Library guide](packages/spine-atlas-toolkit/README.md) | Installation, examples, and the Python API. |
| [Changelog](CHANGELOG.md) | Changes between app releases. |
| [Library changelog](packages/spine-atlas-toolkit/CHANGELOG.md) | Changes between library releases. |
| [Developer notes](NOTES.md) | Architecture, development rules, and testing. |
| [Third-party notices](THIRD_PARTY_NOTICES.md) | Third-party components and their licenses. |

## Run from source

Requires **Python 3.11+**, [uv](https://docs.astral.sh/uv/), and **Node.js**.

```bash
git clone https://github.com/com55/AtlasToolkit.git
cd AtlasToolkit
uv sync
npm ci
```

**Desktop app**

```bash
uv run python scripts/build_web.py --target desktop
uv run python main.py
```

**Web app**

```bash
uv run python scripts/build_web.py
uv run python scripts/serve_www.py
```

Open `http://127.0.0.1:8000/`. Rebuild after changing the frontend or Python
code included in the app. See [Developer notes](NOTES.md) for more details.

## Contributing

Bug reports, suggestions, and pull requests are welcome.
[Open an issue](https://github.com/com55/AtlasToolkit/issues) and include steps
to reproduce the problem, your app version, and whether you used the web or
desktop app. A small sample atlas is helpful when you can share one.

## License

AtlasToolkit is licensed under the **Apache License 2.0**. See [LICENSE](LICENSE),
[NOTICE](NOTICE), and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Releases up to and including **0.4.x** remain available under the **MIT License**.

AtlasToolkit is not affiliated with, endorsed by, or associated with Esoteric
Software or the Spine runtime.

Atlas format reference: [Spine Atlas Format](https://esotericsoftware.com/spine-atlas-format).
