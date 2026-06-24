# AtlasToolkit

**Spine Atlas Toolkit - Extract, modify, and repack atlas sprites.**

A GUI tool for working with [Spine](http://esotericsoftware.com/) atlas files.
View atlas regions, extract individual sprites, replace sprites with other image, and repack the atlas — all through a simple web-based interface powered by [pywebview](https://pywebview.flowrl.com/).

## Usage

- Now, Web version is available [here](https://com55.github.io/AtlasToolkit/)!
- You can use the pre-built executables or download stable version source code from [Releases](https://github.com/com55/AtlasToolkit/releases/latest).
  - Windows is distributed as an **installer** (`AtlasToolkit-Setup-x64.exe`, per-user, no admin) plus a **portable** zip. The installed build updates itself silently in-app; the portable build links to the releases page.
  - **One-time migration:** builds made before the installer switch used the old single-exe auto-update and will report "asset not found" when checking for this release — download and run the new installer once manually.
- Or, clone the repository and run from source (in-development / unstable):
  ```bash
  git clone https://github.com/com55/AtlasToolkit.git
  cd AtlasToolkit
  ```

## Setup & Run (If run from source)

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

## Features

- **Atlas Preview** — View the full atlas image with region overlays
- **Region Extraction** — Extract individual sprites or all regions at once
- **Sprite Modification** — Replace sprites with mod images, auto-expanding the canvas as needed
- **Smart Repack** — Repack all regions into an optimally-sized atlas after modification
- **Drag & Drop** — Load `.atlas` files by dragging them into the app; in Modify Mode, drop a `.png` image directly to apply it as a mod
- **Copy to Clipboard** — Right-click the preview image to copy it to clipboard (Extract Mode)
- **Auto Format Conversion** — Automatically converts LibGDX atlas format (`xy`/`orig`/`offset`) to the Spine format on load
- **Multi-page Atlas** — Supports atlas files that reference multiple page images
- **File Association** — Can be set as the default app for `.atlas` files; opening an atlas file directly will launch the app and load it automatically
- **Update Notifications** — Checks GitHub for new releases on startup and shows an in-app banner when an update is available
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
