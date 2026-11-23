# AtlasToolkit

**Spine Atlas Toolkit - Extract, modify, and repack atlas sprites.**

View the regions of a [Spine](http://esotericsoftware.com/) atlas, extract
sprites, replace them with your own images, and save a repacked atlas. The
same app runs in the browser and on the desktop.

## Usage

- **[Web app](https://com55.github.io/AtlasToolkit/)** — use it directly, no installation required. It can be installed as a PWA for offline use.
- **Desktop app (Windows)** — download from [Releases](https://github.com/com55/AtlasToolkit/releases/latest): an installer (`AtlasToolkit-Setup-x64.exe`, per-user, updates itself) or a portable zip.
- **Python library** — use the engine from your own scripts: see [its README](packages/spine-atlas-toolkit/README.md).

## Features

- **View Mode** — preview single regions or several at once, extract one or all of them as PNGs, copy the preview to the clipboard.
- **Edit Mode** — replace regions with your own images (drop a `.png` on the preview), add, rename and remove regions, then save the new `.atlas` and page images.
- **Packing** — Auto, Shelf, Rectangles or Polygons. With **Try to Keep Layout** (on by default) an edited page keeps its existing positions when the edit fits, and is repacked otherwise.
- **Mesh support** — with the `.skel` next to the atlas, previews and extracts can be cut to each region's mesh, and packing can nest regions by their mesh outline.
- **Multi-page atlases** and LibGDX-format atlases (converted on load).
- **Desktop extras** — open `.atlas` files from Explorer, and update from inside the app.

## Run from source

Needs Python 3.11+, [uv](https://github.com/astral-sh/uv#installation) and Node.js.

```bash
git clone https://github.com/com55/AtlasToolkit.git
cd AtlasToolkit
uv sync
npm ci
```

- **Desktop app:**
  ```bash
  uv run python scripts/build_web.py --target desktop
  uv run python main.py
  ```
- **Web app:**
  ```bash
  uv run python scripts/build_web.py
  uv run python scripts/serve_www.py   # http://127.0.0.1:8000/
  ```

Rebuild after changing anything in `www/`. Without uv, `pip install -r requirements.txt` installs the same dependencies.
Developer notes are in [NOTES.md](NOTES.md), and changes in [CHANGELOG.md](CHANGELOG.md).

## Contributing

Found a bug or have a suggestion? Please [open an issue](https://github.com/com55/AtlasToolkit/issues). Pull requests are welcome.

## License

Apache License 2.0: see [LICENSE](LICENSE) and [NOTICE](NOTICE). Third-party components are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
Releases up to and including 0.4.x were published under the MIT License and stay available under those terms.

Atlas format reference: [Spine Atlas Format](https://esotericsoftware.com/spine-atlas-format).

## Disclaimer

This project is **not affiliated with, endorsed by, or associated with Esoteric Software** or the Spine runtime in any way. It was created for **educational and personal use only**.
