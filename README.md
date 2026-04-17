# AtlasToolkit

**Spine Atlas Toolkit - Extract, modify, and repack atlas sprites.**

A web-based tool for working with [Spine](http://esotericsoftware.com/) atlas files.
View atlas regions, extract individual sprites, replace sprites with other images, and repack the atlas — all through a simple browser interface.

## Usage

Use the hosted web app directly — no installation required:

👉 **[https://com55.github.io/AtlasToolkit/](https://com55.github.io/AtlasToolkit/)**

Or clone and open locally:
```bash
git clone -b js-project https://github.com/com55/AtlasToolkit.git
cd AtlasToolkit/www
# Open index.html in your browser
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
- **Save Modified Atlas** — Export updated `.atlas`, `.png`, and also copy `.skel` file if available
- **PWA Support** — Install as a Progressive Web App for offline use

## Acknowledgments

- [Spine Atlas Format](https://esotericsoftware.com/spine-atlas-format) — Atlas format specification reference

## Contributing

- Found a bug or have a suggestion? Please [open an issue](https://github.com/com55/AtlasToolkit/issues)
- Pull requests are welcome!

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## Disclaimer

This project is **not affiliated with, endorsed by, or associated with Esoteric Software** or the Spine runtime in any way. It was created for **educational and personal use only**.
