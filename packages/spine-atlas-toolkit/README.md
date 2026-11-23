# spine-atlas-toolkit

Read, extract, edit and repack [Spine](http://esotericsoftware.com/) texture
atlases (`.atlas` + page PNGs) from Python. This is the engine of the
[AtlasToolkit](https://github.com/com55/AtlasToolkit) web and desktop apps.

## Install

```bash
pip install spine-atlas-toolkit
```

Python 3.11 or later. Dependencies: NumPy, Pillow and `skel-mesh-parser`.

```python
import spine_atlas_toolkit as sat
```

## Open and extract

```python
atlas = sat.SpineAtlas.open("hero.atlas")    # loads the page PNGs, and hero.skel if present

for region in atlas.regions:
    print(region.name, region.page, region.bounds)

atlas.extract("head").save("head.png")       # a PIL image at the region's original size
atlas.extract_all_to("sprites/")             # every region as a PNG
```

## Edit and save

Edits are recorded first; `save()` (or `build()`) packs them once.

```python
from PIL import Image

atlas.replace({"head": "new_head.png"})      # a path or a PIL image
atlas.add_region("hat", Image.open("hat.png"))
atlas.rename_region("arm", "left_arm")
atlas.remove_region("tail")

atlas.save("out/hero.atlas")                 # writes the .atlas and its page PNGs
```

`build()` does the same in memory and returns a new `SpineAtlas`; the original
is unchanged. `SpineAtlas.load(text, {"hero.png": image, ...})` opens an atlas
from memory.

## Edit several regions as one picture

```python
sheet = atlas.extract_composite(["head", "arm"], force_resize=True)
# ... edit `sheet` in an image editor ...
atlas.replace_composite(["head", "arm"], sheet)
```

By default the edited picture is cut back into one piece per region.

## Pack options

```python
atlas.save("out/hero.atlas", options=sat.PackOptions(packer="rectangles", padding=4))
```

| Option | Default | What it does |
|---|---|---|
| `packer` | `"auto"` | `"shelf"` (rows), `"rectangles"` (nests rects), `"polygons"` (nests by mesh outline; needs a `.skel`). `"auto"` is `"polygons"` with mesh data, else `"rectangles"`. |
| `padding` | `2` | Pixels between packed images (1–256). |
| `mesh_aware` | on with mesh data | Cut pixels outside each region's mesh before packing. |
| `try_keep_layout` | `True` | An edited page keeps its existing positions when the edit fits (same size, no new overlap; additions go into free space); otherwise it is repacked. `False` always repacks edited pages. |
| `bleed` | auto | Fill transparent pixels next to images with their colour (alpha stays 0), so filtering draws no dark edges. On when composites are split. |
| `share_composite_image` | `False` | For `replace_composite`: give every region the whole picture instead of cutting it into pieces. |

## Mesh data (`.skel`)

With a `.skel` next to the atlas (or passed to `load()`), these use the mesh
outlines:

- `extract(..., mesh_crop=True)` cuts pixels outside the region's mesh.
- `packer="polygons"` and `mesh_aware` (see above).

Without usable mesh data they raise `SkeletonUnavailable`.

## Premultiplied alpha

Pages marked `pma: true` are read and written premultiplied. For ordinary
(straight-alpha) PNGs, pass `alpha_mode="straight"` to `replace`,
`replace_composite`, `add_region` and the extract methods:

```python
atlas.replace({"head": "new_head.png"}, alpha_mode="straight")
```

## Multi-page atlases

Only the pages that own an edited region are repacked. `add_region(page=...)`
picks the page; otherwise the least-filled page is used.

## Errors

All errors derive from `sat.AtlasError`: `RegionNotFound`,
`DuplicateRegionName`, `InvalidRegionName`, `MissingPageImage`,
`SkeletonUnavailable`, `CanvasTooLarge` (a page over 10000 px; `.page` names
it), `MixedAlphaGroup` and `DuplicatePage`.

## API reference

Everything below is importable from `spine_atlas_toolkit`. Arguments after `*`
are keyword-only; `alpha_mode` is `None`, `"straight"` or `"premultiplied"`
(see [Premultiplied alpha](#premultiplied-alpha)).

### `SpineAtlas`

**Open**

| | |
|---|---|
| `SpineAtlas.open(atlas_path, *, images=None, skeleton=None)` | Open an `.atlas` file. Its page PNGs and a `.skel` with the same name are read from its folder unless given: `images` as `{page file name: image or path}` (or one image for a one-page atlas), `skeleton` as bytes or a path. |
| `SpineAtlas.load(atlas_text, pages, *, skeleton=None)` | Open from memory: the atlas text and `{page file name: PIL image or path}` (or one image for a one-page atlas). |

**Properties**

| | |
|---|---|
| `regions` | All regions, as `Region` objects. |
| `pages` | `{page file name: PIL image}`. |
| `atlas_text` | The `.atlas` text. |
| `has_changes` | `True` while there are edits not yet built. |
| `mesh_unavailable_reason` | `None` when mesh data is usable, otherwise why not. |

**Read**

| | |
|---|---|
| `region(name)` | One `Region`. |
| `page_image(name)` | A copy of one page as loaded. |
| `page_is_pma(page)` | Whether a page is premultiplied (`pma: true`). |

**Extract**

| | |
|---|---|
| `extract(names, *, mesh_crop=False, alpha_mode=None)` | One name → a PIL image at the region's original size; a list → `{name: image}`. |
| `extract_all(*, mesh_crop=False, alpha_mode=None)` | `{name: image}` for every region. |
| `extract_to(names, path, *, mesh_crop=False, alpha_mode=None)` | Save as PNG: one name to the file `path`, a list into the folder `path`. |
| `extract_all_to(directory, *, mesh_crop=False, alpha_mode=None)` | Save every region as a PNG into `directory`. |
| `extract_composite(names, *, force_resize=False, mesh_crop=False, alpha_mode=None)` | Several regions stacked into one image; `force_resize` scales them to the largest one. |
| `extract_composite_to(names, path, *, force_resize=False, mesh_crop=False, alpha_mode=None)` | Save `extract_composite()` as a PNG. |

**Edit** (recorded until `build()` / `save()`)

| | |
|---|---|
| `replace(images, *, alpha_mode=None)` | `{name: image or path}`: new pixels for regions. |
| `replace_composite(names, image, *, alpha_mode=None)` | One image for several regions (the inverse of `extract_composite`). |
| `add_region(name, image, page=None, *, alpha_mode=None)` | Add a region; `page` picks the page, otherwise the least-filled one. |
| `rename_region(name, new_name)` | Rename a region. |
| `remove_region(names)` | Remove one region or a list. |
| `reset()` | Discard all recorded edits. |
| `set_skeleton(skeleton)` | Replace the mesh data (`.skel` bytes, or `None` to drop it) without discarding edits. |

**Pack and save**

| | |
|---|---|
| `build(*, options=None)` | Pack the edits and return a new `SpineAtlas`; this one is unchanged. |
| `save(atlas_path, *, options=None)` | `build()`, then write the `.atlas` and its page PNGs next to it. |

`options` is a `PackOptions` (see [Pack options](#pack-options)).

### `Region`

A frozen dataclass: `name`, `original_name` (the name in the loaded atlas;
`None` for an added region), `page`, `bounds` (`(x, y, w, h)` on the page),
`rotate` (0, 90, 180 or 270) and `offsets` (`(left, bottom, original_w,
original_h)` as in the atlas, or `None`), plus the properties `renamed` and
`added`.

### `PackOptions`, `Packer`, `AlphaMode`

- `PackOptions(*, packer="auto", padding=2, mesh_aware=None, try_keep_layout=True, bleed=None, share_composite_image=False)`
  (`None` means automatic; see the table above).
- `Packer`: `AUTO`, `SHELF`, `RECTANGLES`, `POLYGONS` (or the same names as
  lowercase strings).
- `AlphaMode`: `STRAIGHT`, `PREMULTIPLIED` (or `"straight"` / `"premultiplied"`).

### Exceptions

`AtlasError` is the base class of `RegionNotFound`, `DuplicateRegionName`,
`InvalidRegionName`, `MissingPageImage`, `SkeletonUnavailable`,
`CanvasTooLarge`, `MixedAlphaGroup` and `DuplicatePage`.

## More

- Changes: [CHANGELOG](https://github.com/com55/AtlasToolkit/blob/main/packages/spine-atlas-toolkit/CHANGELOG.md)
- Source and issues: [github.com/com55/AtlasToolkit](https://github.com/com55/AtlasToolkit)
- License: Apache-2.0. Not affiliated with Esoteric Software (Spine).
