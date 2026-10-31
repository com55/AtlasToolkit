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
  uv sync --extra app
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

## Python library

The `atlas_toolkit` package also contains a Python port of the atlas engine,
usable from scripts without the app. Its output matches the app's (checked
against the app's own code running in a real browser), apart from the
differences listed in [CHANGELOG.md](CHANGELOG.md). The desktop and web apps
still run the JavaScript engine in `www/js/`.

Install from source (Python 3.11 or later):

```bash
uv sync                # library only; add --extra app for the desktop shell
uv run python your_script.py
```

Open an atlas, look at it, edit it, and save the result:

```python
from PIL import Image
from atlas_toolkit import SpineAtlas, CanvasTooLarge, Packer, PackerOptions

atlas = SpineAtlas.open("hero.atlas")          # page PNGs (and hero.skel, if any) next to it
for region in atlas.regions:
    print(region.name, region.page, region.bounds)

atlas.extract("head").save("head.png")
atlas.extract_all_to("sprites/")

atlas.replace({"head": "new_head.png"})
atlas.add_region("hat", Image.new("RGBA", (8, 8), (0, 255, 0, 255)), page="hero_2.png")
atlas.rename_region("arm", "left_arm")
atlas.remove_region("tail")

try:
    atlas.save("out/hero.atlas", packer=Packer.RECTANGLES, packer_options=PackerOptions(padding=4))  # + page PNGs
except CanvasTooLarge as error:
    print("too big:", error.page, error)
```

Atlases can also be loaded from memory, and several regions can be edited as
one picture:

```python
atlas = SpineAtlas.load(text, {"hero.png": page_one, "hero_2.png": page_two})
sheet = atlas.extract_composite(["head", "arm"], force_resize=True)
# ... edit `sheet` in an image editor ...
atlas.replace_composite(["head", "arm"], sheet)
built = atlas.build(split_composite=True)       # a new SpineAtlas; `atlas` is unchanged
```

- Edits are only recorded. `build()` packs them once and returns a new
  `SpineAtlas`; `save()` builds and writes. Both take:
  - `packer`: `"shelf"` (default), `"rectangles"` (Smart Packing on solid
    rects) or `"polygons"` (Smart Packing on mesh silhouettes, needs mesh
    data; `PackerOptions(mesh_aware=False)` with it raises `ValueError`). A
    string or a `Packer` member. `"grid"`, `"rectangles_in_place"` and
    `"polygons_in_place"` are reserved and raise `NotImplementedError`. With
    `"polygons"`, a region's rect can contain pixels of neighbours nested
    into its empty corners; extract with `mesh_crop=True` to see only the
    region.
  - `packer_options`: a `PackerOptions`:
    - `padding` is the pixels between images, default 2, 1 to 256.
    - `mesh_aware` cuts each region outside its mesh hull before packing. It
      defaults to on whenever the atlas has usable mesh data, so a `.skel`
      next to the atlas changes the default output.
  - `split_composite`.

  `atlas.build(packer="polygons", packer_options=PackerOptions(padding=4))`
- With `split_composite=True`, a `replace_composite` image is cut back into one
  piece per region: the inverse of `extract_composite(force_resize=True)`.
- `mesh_crop=True` on the extract methods and `packer="polygons"` or
  `PackerOptions(mesh_aware=True)` on `build()` / `save()` use the meshes from
  the `.skel` file; without usable mesh data they raise `SkeletonUnavailable`.
- On a `pma: true` page, images are read and written premultiplied: extracts
  return premultiplied pixels, additive colour on alpha-0 pixels included, and
  `replace` / `replace_composite` take premultiplied images. Mesh Cropping and
  mesh-aware packing clear all four channels outside the hull. A composite
  that mixes pma and non-pma pages is stacked as straight alpha and warns.
- For straight-alpha images, such as PNGs from an ordinary image editor, pass
  `alpha_mode="straight"` (or `AlphaMode.STRAIGHT`) to `replace`,
  `replace_composite`, `add_region` and the extract methods:
  `atlas.replace({"head": "new_head.png"}, alpha_mode="straight")`. Each image
  is converted to or from its page's own space; `"premultiplied"` does the same
  for straight pages. Without `alpha_mode` nothing is converted. On a pma page
  only its own space (no `alpha_mode`, or `"premultiplied"`) round-trips
  exactly: a straight extract drops additive colour (RGB above alpha,
  including colour on alpha-0 pixels). A composite group that mixes pma and
  non-pma pages raises `MixedAlphaGroup` when `alpha_mode` is given.
- Multi-page atlases can be edited. Only the pages that own an edited
  region are repacked; the others are kept as they are. `add_region(page=...)`
  picks the page, otherwise the page whose regions cover the least area is
  used.
- A region added with `add_region` can be extracted only after `build()`.
- Errors derive from `AtlasError`: `RegionNotFound`, `DuplicateRegionName`,
  `InvalidRegionName`, `MissingPageImage`, `SkeletonUnavailable`,
  `CanvasTooLarge`, `MixedAlphaGroup` and `ConflictingPagePma`. `CanvasTooLarge` is raised when a packed page would be over
  10000 px on a side; `.page` names the page on a multi-page atlas.
  `ConflictingPagePma` is raised on load when one page file is listed twice
  with different `pma` values. A region bigger than its page image raises
  `ValueError` when it is read.

## Features

- **Atlas Preview** — View the full atlas image with region overlays
- **Region Extraction** — Extract individual sprites or all regions at once
- **Sprite Modification** — Replace sprites with mod images; the atlas is automatically repacked into a compact, square-ish layout after every modification
- **Smart Repack** — Regions are deduplicated and tightly packed, favoring a near-square canvas over the smallest possible one (up to 15% larger, if it makes the sheet noticeably squarer)
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
