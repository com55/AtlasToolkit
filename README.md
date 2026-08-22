# AtlasToolkit

**Spine Atlas Toolkit - Extract, modify, and repack atlas sprites.**

A tool for working with [Spine](http://esotericsoftware.com/) atlas files, available as a **hosted web app**, a **desktop app** (powered by [pywebview](https://pywebview.flowrl.com/)), or run directly from source.
View atlas regions, extract individual sprites, replace sprites with other images, and repack the atlas — all through the same simple interface everywhere.

## Usage

- **Web app** — use it directly, no installation required: 👉 **[https://com55.github.io/AtlasToolkit/](https://com55.github.io/AtlasToolkit/)**
- **Desktop app** — download the pre-built executables from [Releases](https://github.com/com55/AtlasToolkit/releases/latest).
  - Windows is distributed as an **installer** (`AtlasToolkit-Setup-x64.exe`, per-user, no admin) plus a **portable** zip. The installed build updates itself silently in-app; the portable build links to the releases page.
  - **One-time migration:** builds made before the installer switch used the old single-exe auto-update and will report "asset not found" when checking for this release — download and run the new installer once manually.
- **From source** (in-development / unstable) — clone the repository:
  ```bash
  git clone https://github.com/com55/AtlasToolkit.git
  cd AtlasToolkit
  ```

## Setup & Run (desktop app, from source)

Once you are inside the project directory, choose your preferred method:

- **Using [uv](https://github.com/astral-sh/uv#installation) (Recommended)**
  ```bash
  uv sync
  uv run python main.py
  ```
- **Using pip**
  ```bash
  pip install -r requirements.txt
  python main.py
  ```
  or
  ```bash
  pip install pillow pywebview requests
  python main.py
  ```

## Setup & Run (web app, from source)

```bash
cd www
# Open index.html in your browser, or serve it: python3 -m http.server
```

## Features

- **Atlas Preview** — View the full atlas image with region overlays
- **Region Extraction** — Extract individual sprites or all regions at once
- **Sprite Modification** — Replace sprites with mod images, auto-expanding the canvas as needed
- **Smart Repack** — Repack all regions into an optimally-sized atlas after modification
- **Drag & Drop** — Load `.atlas` files by dragging them into the app; in Modify Mode, drop a `.png` image directly to apply it as a mod
- **Copy to Clipboard** — Right-click the preview image to copy it to clipboard (Extract Mode)
- **Auto Format Conversion** — Automatically converts LibGDX atlas format (`xy`/`orig`/`offset`) to the Spine format on load
- **Multi-page Atlas** — Supports atlas files that reference multiple page images
- **File Association** (desktop app) — Can be set as the default app for `.atlas` files; opening an atlas file directly will launch the app and load it automatically
- **Update Notifications** (desktop app) — Checks GitHub for new releases on startup and shows an in-app banner when an update is available
- **PWA Support** (web app) — Install as a Progressive Web App for offline use
- **Save Modified Atlas** — Export updated `.atlas`, `.png`, and also copy `.skel` file if available

## Acknowledgments

- [Spine Atlas Format](https://esotericsoftware.com/spine-atlas-format) — Atlas format specification reference

## Contributing

- Found a bug or have a suggestion? Please [open an issue](https://github.com/com55/AtlasToolkit/issues)
- Pull requests are welcome!

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## Disclaimer

This project is **not affiliated with, endorsed by, or associated with Esoteric Software** or the Spine runtime in any way. It was created for **educational and personal use only**.
