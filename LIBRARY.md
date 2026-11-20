# AtlasToolkit Python library

The `spine_atlas_toolkit` package is the atlas engine that the web and desktop apps
run. It can also be used from your own scripts, without the app.
Differences from the JavaScript engine of 0.4.x are listed in
[CHANGELOG.md](CHANGELOG.md).

## Install

From source, Python 3.11 or later:

```bash
uv sync                # library only; add --extra app for the desktop shell
uv run python your_script.py
```

## Quick start

Open an atlas, look at it, edit it, and save the result:

```python
from PIL import Image
from spine_atlas_toolkit import SpineAtlas, CanvasTooLarge, PackOptions, Packer

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
    atlas.save("out/hero.atlas", options=PackOptions(packer=Packer.RECTANGLES, padding=4))  # + page PNGs
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
built = atlas.build()                           # a new SpineAtlas; `atlas` is unchanged
```

Edits are only recorded. `build()` packs them once and returns a new
`SpineAtlas`; `save()` builds and writes. A region added with `add_region`
can be extracted only after `build()`, and removing a region added since the
atlas was loaded or built undoes the add.

## Pack options

`build()` and `save()` take `options=PackOptions(...)`, for example
`atlas.build(options=PackOptions(packer="polygons", padding=4))`.

- `packer`: `"auto"` (default), `"shelf"`, `"rectangles"` or `"polygons"`.
  A string or a `Packer` member. `"auto"` picks `"polygons"` when the atlas
  has usable mesh data (a `.skel` next to it, or a skeleton passed to
  `load()`), otherwise `"rectangles"`. `"shelf"` puts one row of images, no
  nesting; `"rectangles"` is Smart Packing on solid rects; `"polygons"` is
  Smart Packing on mesh silhouettes and needs mesh data
  (`PackOptions(mesh_aware=False)` with it raises `ValueError`). With
  `"polygons"`, a region's rect can contain pixels of neighbours nested into
  its empty corners; extract with `mesh_crop=True` to see only the region.
  Auto's size-aware choice (Polygons > Rectangles > Shelf by region count
  and each region's size) comes later.
- `padding`: pixels between packed images, default 2, 1 to 256.
- `mesh_aware`: cut each region's pixels outside its mesh hull before
  packing. Default on whenever the atlas has usable mesh data, so a `.skel`
  next to the atlas changes the default output.
- `try_keep_layout` (default on): an edited page first tries to keep its
  layout. Removed regions leave empty space; a replacement fits when it is
  the size `extract()` returns for that region (its full canvas), shows
  nothing outside the region's trimmed rect (outside its mesh hull is
  ignored when mesh-aware), and leaves pixels it shares with an overlapping
  neighbour unchanged; regions that share one rect must be replaced alike;
  additions go into its free space and may grow the page right/down.
  Free space is everything outside existing regions' rects and visible
  pixels; with mesh data, outside their mesh hulls. New regions may be
  stored rotated 90/180/270. A page with any other edit, whose image is not
  the size its text declares, or that holds a shared-image composite
  replace is repacked with `packer`. `try_keep_layout=False` repacks every
  edited page.
- `bleed`: Spine's packer Bleed. On every page the pack writes that is not
  pma, transparent pixels within 2 px of an image take its colour (alpha
  stays 0) so linear filtering draws no dark edges. Default: on when the
  pack splits composite images (the default), off when it shares them.
- `share_composite_image`: `replace_composite` only. Off (default) cuts the
  image back into one piece per region: the inverse of
  `extract_composite(force_resize=True)`. On, every region of the group uses
  the whole image at the image's own size (offsets dropped), and a page
  holding such a replace is repacked.

## Mesh data

`mesh_crop=True` on the extract methods and `packer="polygons"` or
`PackOptions(mesh_aware=True)` on `build()` / `save()` use the meshes from
the `.skel` file; without usable mesh data they raise `SkeletonUnavailable`.

An added region whose name has a mesh in the `.skel` is cut to its hull when
`mesh_aware` is on, and stays a mesh region in the built atlas.

## Premultiplied alpha

On a `pma: true` page, images are read and written premultiplied: extracts
return premultiplied pixels, additive colour on alpha-0 pixels included, and
`replace` / `replace_composite` take premultiplied images. Mesh Cropping and
mesh-aware packing clear all four channels outside the hull. A composite
that mixes pma and non-pma pages is stacked as straight alpha and warns.

For straight-alpha images, such as PNGs from an ordinary image editor, pass
`alpha_mode="straight"` (or `AlphaMode.STRAIGHT`) to `replace`,
`replace_composite`, `add_region` and the extract methods:
`atlas.replace({"head": "new_head.png"}, alpha_mode="straight")`. Each image
is converted to or from its page's own space; `"premultiplied"` does the same
for straight pages. Without `alpha_mode` nothing is converted. On a pma page
only its own space (no `alpha_mode`, or `"premultiplied"`) round-trips
exactly: a straight extract drops additive colour (RGB above alpha,
including colour on alpha-0 pixels). A composite group that mixes pma and
non-pma pages raises `MixedAlphaGroup` when `alpha_mode` is given.

## Multi-page atlases

Only the pages that own an edited
region are repacked; the others are kept as they are. `add_region(page=...)`
picks the page, otherwise the page whose regions cover the least area is
used.

## Errors

Errors derive from `AtlasError`: `RegionNotFound`, `DuplicateRegionName`,
`InvalidRegionName`, `MissingPageImage`, `SkeletonUnavailable`,
`CanvasTooLarge`, `MixedAlphaGroup` and `DuplicatePage`. `CanvasTooLarge`
is raised when a packed page would be over 10000 px on a side; `.page`
names the page on a multi-page atlas. `DuplicatePage` is raised on load
when one page file is listed twice. A region bigger than its page image
raises `ValueError` when it is read.
