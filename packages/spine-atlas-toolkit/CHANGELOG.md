# Changelog

All notable changes to `spine-atlas-toolkit`, the Python library on
[PyPI](https://pypi.org/project/spine-atlas-toolkit/). Versions match the
`lib-v*` Git tags. The AtlasToolkit apps have their own version and
[changelog](https://github.com/com55/AtlasToolkit/blob/main/CHANGELOG.md).

## [1.0.0] - 2026-11-21

First release: the atlas engine of the AtlasToolkit apps, as a library.

### Reading and extracting
- `SpineAtlas.open()` opens an `.atlas` from disk with its page PNGs and a
  `.skel` of the same name; `SpineAtlas.load()` opens one from memory.
  LibGDX-format atlases are converted on load.
- `extract`, `extract_all`, `extract_composite` and their `*_to` variants
  return or save regions at their original size. `extract_composite` stacks
  several regions, optionally scaled to the largest (`force_resize`).
- With usable mesh data (Spine 3.8.x or 4.2.x binary `.skel`),
  `mesh_crop=True` clears pixels outside a region's mesh, on pma pages too.
- Extracted file names keep non-ASCII letters; names that would map to the
  same file raise `AtlasError` instead of overwriting.

### Editing and packing
- Edits are recorded (`replace`, `replace_composite`, `add_region`,
  `rename_region`, `remove_region`, `reset`, `set_skeleton`) and packed once
  by `build()` (a new `SpineAtlas`) or `save()`.
- `PackOptions`: `packer` (`"auto"` default: `"polygons"` with mesh data,
  else `"rectangles"`; `"shelf"`, `"rectangles"`, `"polygons"`), `padding`
  (default 2, as Spine's Texture Packer), `mesh_aware`, `try_keep_layout`,
  `bleed`, `share_composite_image`.
- `try_keep_layout=True` (default): a page whose edits fit keeps its layout.
  Removed regions leave empty space; a replaced region whose new pixels fit
  its old slot (the canvas size `extract()` gives, nothing visible outside the
  old trimmed rect, pixels shared with an overlapping neighbour unchanged) is
  drawn there with its rect and offsets unchanged; additions go into free
  space. Any other edited page is repacked with the selected packer.
  Kept-layout pages are not deduplicated afresh.
- `share_composite_image=False` (default): a `replace_composite` image is cut
  back into one piece per region, the inverse of
  `extract_composite(force_resize=True)`. With mesh-aware packing each meshed
  region keeps its mesh hull, and a semi-transparent pixel shared by several
  regions has its alpha split between them, so drawing them all gives the
  image back.
- `mesh_aware` (default: on with usable mesh data) cuts pixels outside each
  region's mesh before packing; `"polygons"` nests regions by their mesh
  outline. Added regions with mesh data are cut to their hull too.
- `bleed` (default: on when composites are split): on pages that are not
  pma, transparent pixels within 2 px of an image take the mean colour of
  their visible neighbours (alpha stays 0), so texture filtering draws no
  dark edges.
- Only pages that own an edited region are repacked; untouched and
  rename-only pages keep their pixels. `add_region(page=...)` picks the page,
  otherwise the page whose regions cover the least area. Removing a region
  added in the same session undoes the add.
- `"rectangles"` / `"polygons"` pages are trimmed to their regions (rounded
  up to a multiple of 4). Packed pages are capped at 10000 px per side
  (`CanvasTooLarge`, with `.page`).
- The last 8 packs of the current edits are cached (within 512 MB of edited
  page pixels), so switching options back does not repack.
- Page lines other than the standard ones, such as `scale:`, are kept;
  output images carry no metadata such as colour profiles.

### Premultiplied alpha
- `pma: true` pages are read and written premultiplied and keep additive
  colour (RGB on alpha-0 pixels) through repack, extract and split.
- `alpha_mode="straight"` / `"premultiplied"` on edits and extracts converts
  images to or from each page's own alpha space; a composite group spanning
  pma and non-pma pages raises `MixedAlphaGroup` when it is given.

### Errors
- All derive from `AtlasError`: `RegionNotFound`, `DuplicateRegionName`,
  `InvalidRegionName`, `MissingPageImage`, `SkeletonUnavailable`,
  `CanvasTooLarge`, `MixedAlphaGroup` and `DuplicatePage` (an atlas listing
  one page file twice, which Spine refuses too).
