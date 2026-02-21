# AtlasToolkit

**Spine Atlas Toolkit - Extract, modify, and repack atlas sprites.**

A GUI tool for working with [Spine](http://esotericsoftware.com/) atlas files.
View atlas regions, extract individual sprites, replace sprites with other image, and repack the atlas — all through a simple web-based interface powered by [pywebview](https://pywebview.flowrl.com/).

## Usage

- You can use the pre-built executables from [Releases](https://github.com/com55/AtlasToolkit/releases/latest).
- Or, clone the repository and run from source (in-development / unstable):

  ```bash
  git clone https://github.com/com55/AtlasToolkit.git
  cd AtlasToolkit
  ```

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

## Features

- **Atlas Preview** — View the full atlas image with region overlays
- **Region Extraction** — Extract individual sprites or all regions at once
- **Sprite Modification** — Replace sprites with mod images, auto-expanding the canvas as needed
- **Smart Repack** — Repack all regions into an optimally-sized atlas after modification
- **Drag & Drop** — Load `.atlas` files by drag-and-drop
- **Save Modified Atlas** — Export updated `.atlas`, `.png`, and also copy `.skel` file if available.

## Acknowledgments

- [Spine Atlas Format](https://esotericsoftware.com/spine-atlas-format) — Atlas format specification reference

## Contributing

- Found a bug or have a suggestion? Please [open an issue](https://github.com/com55/AtlasToolkit/issues)
- Pull requests are welcome!

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## Disclaimer

This project is **not affiliated with, endorsed by, or associated with Esoteric Software** or the Spine runtime in any way. It was created for **educational and personal use only**.
