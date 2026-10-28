# Changelog

All notable changes to AtlasToolkit. Versions match the Git tags and the
[GitHub Releases](https://github.com/com55/AtlasToolkit/releases), which carry
the full notes and screenshots.

## [Unreleased] - Python engine

A Python port of the atlas engine, usable as a library (`atlas_toolkit`). The
desktop and web apps are unchanged and still run the JavaScript engine.

### Breaking
- Mesh-aware packing now applies to `pma: true` pages. `PackerOptions(mesh_aware=None)`
  (the default) masks them to their mesh hulls whenever mesh data is usable;
  opt out with `PackerOptions(mesh_aware=False)`. `packer="polygons"` now
  nests and masks pma pages by their mesh hulls too; it always uses the mesh,
  so the only opt-out there is another packer. `mesh_crop=True` now crops
  regions on pma pages too, clearing all four channels outside the hull.

### Added
- `SpineAtlas`: open an atlas from disk (`open`) or memory (`load`), list
  regions, extract single, all or composite sprites (with Forced Resizing and
  Mesh Cropping), record edits (`replace`, `replace_composite`, `add_region`,
  `rename_region`, `remove_region`) and pack them once with `build()` /
  `save()` (packer `"shelf"`, `"rectangles"` or `"polygons"`, with
  `PackerOptions` padding and mesh-aware).
- `build(split_composite=True)`: a `replace_composite` image is cut back into
  one piece per region, the inverse of `extract_composite(force_resize=True)`.
  When the build is mesh-aware (the default with mesh data) each meshed region
  keeps its mesh hull; a semi-transparent pixel shared by several regions has
  its alpha split between them, so drawing them all gives the image back.
- Multi-page atlases can be edited, including Add, Rename and Remove. Only
  pages that own an edited region are repacked. `add_region(page=...)` picks
  the page; without it the page whose regions cover the least area is used.
- `alpha_mode=` (`AlphaMode.STRAIGHT` / `AlphaMode.PREMULTIPLIED`, or the
  strings) on `replace`, `replace_composite`, `add_region` and every extract
  method: the image is converted to or from each page's own alpha space.
  Without it nothing changes. On a pma page a straight extract drops additive
  colour and is otherwise exact when replaced back with the same mode. A composite
  group spanning pma and non-pma pages raises `MixedAlphaGroup` when
  `alpha_mode` is given.
- Packed pages are capped at 10000 px per side: `CanvasTooLarge` is raised
  before the page is allocated, with `.page` naming the page on a multi-page
  atlas.
- The core package needs only Pillow, numpy and the `.skel` parser; the
  desktop shell's dependencies moved to the `app` extra.

### Changed
- `build()` / `save()` take `packer=` and `packer_options=PackerOptions(...)`
  instead of `nest`, `gap` and `mesh_aware`, which are removed.
- `padding` (formerly the Smart Packing gap) follows the Spine Texture Packer:
  pixels between images, default 2 for every packer (the Smart Packing gap
  was 4). The shelf packer now leaves this padding too; before, it packed
  sprites edge to edge.
- `mesh_aware` defaults to on when the atlas has usable mesh data, and
  `SpineAtlas.open()` reads a `.skel` next to the atlas. So a default
  `build()` masks repacked pages to the mesh hulls, and
  `build(split_composite=True)` gives each meshed region its hull.
  `PackerOptions(mesh_aware=False)` turns the masking off (the shelf layout
  still has at least 1 px padding; the old edge-to-edge layout is gone).
  Untouched and rename-only pages keep their pixels.
- `packer="rectangles"` can be combined with `mesh_aware`: pixels are cut to
  the hull, but the packing uses each region's whole rect.
- `pma: true` pages keep additive colour (RGB on alpha-0 pixels) through
  every repack, extract and split. A composite whose regions all live on pma
  pages round-trips exactly through `extract_composite` / `replace_composite`
  / `build(split_composite=True)` when it is extracted with the same names in
  the same order, no region is resized and `mesh_crop` matches the build's
  ownership; additive colour goes to the topmost region
  covering it. In a group that mixes pma and non-pma pages, the pma pieces
  are cleared outside their owner and keep alpha-0 colour only where they
  are the topmost owner. Forced Resizing on pma pages uses a premultiplied Lanczos that
  scales a pixel down when its filtered alpha overshoots 255.
- `extract_composite` warns when its regions span pma and non-pma pages, as
  `replace_composite` already did; such a composite is stacked as straight
  alpha.

### Fixed
- `pma: true` pages: composite extracts and previews stack premultiplied
  colour instead of treating it as straight alpha.

### Differences from the app's engine
- `pma` is kept on repack (the app drops it).
- pma pages are masked to mesh hulls, keep alpha-0 colour when deduplicating
  sprites, and are resized with a premultiplied Lanczos (the app treats pma
  pixels as straight alpha).
- Page lines other than the standard ones, such as `scale:`, are kept on
  repack.
- Extracted file names keep non-ASCII letters (the app turns them into `_`).
- Output images carry no metadata such as embedded colour profiles.
- Repack deduplication also compares sprite sizes.
- Forced Resizing with Mesh Cropping resizes a region before masking it, so a
  scaled composite round-trips exactly.
- The shelf packer leaves padding between sprites (the app packs them edge to
  edge), and nest packing defaults to 2 px padding (the app's gap is 4).
  `"rectangles"` can be combined with mesh_aware (the app always nests
  mesh-aware regions on their mesh).

## [0.4.1] - 2026-10-12

### Fixed
- Saved `.atlas` files now end with a newline, like Spine's own exporter.
  Without it, runtimes built on spine-cpp could read past the end of the file
  text when loading an edited atlas.

## [0.4.0] - 2026-10-09

The desktop app and the web app now share one interface and one engine.

### Added
- **Mesh Cropping** (View mode): crop each region to its mesh silhouette from a
  sibling `.skel`, so neighbouring pixels no longer leak into previews or
  extracted sprites.
- **Mesh-Aware** (Edit mode): apply the same silhouette before repacking.
- **Show Mesh Silhouettes**: draw the selected regions' meshes over the preview
  (preview-only).
- `.skel` picker next to the mesh toggles, with missing / invalid / ready
  states; `.skel` files can also be dropped onto the window.
- **Forced Resizing** (View mode): stretch a selection to the largest region's
  canvas and stack it in the preview.
- **Smart Packing** (Beta): silhouette-aware packing that fills gaps, with a
  configurable gap (default 4 px). Saved atlases may contain `rotate: 180` and
  `rotate: 270`.
- **Advance Mode** (Beta): Rename, Add and Remove regions on single-page atlases.
- **Copy .skel** option in the Save As menu (on by default).
- Confirmation before a save or extract replaces existing files.

### Changed
- Every edit repacks the page immediately; the Repack checkbox is gone. The
  packer prefers near-square sheets.
- The desktop app now runs the same JavaScript engine and UI as the web app;
  the Python side only handles native dialogs, file access and updates.
- Saving a preview image with no extension stores it as `.png`.
- The installer removes the old `ui` folder left by 0.3.x.

### Known issues
- Smart Packing with Mesh-Aware on very large atlases (for example 4096x4096
  with hundreds of regions) can exceed the 5-minute packing limit and be
  cancelled.

## [0.3.4] - 2026-07-15

### Fixed
- Repacking a single region modded with a full-canvas image of a different size
  kept stale offsets, corrupting the extracted sprite. Offsets are now reset for
  full-canvas mods regardless of how many regions are selected. Also fixed in
  the web version.

## [0.3.3] - 2026-07-10

### Fixed
- Repacked and merged page PNGs are padded to multiple-of-4 dimensions for
  GPU block compression (ASTC/ETC2/DXT); the `size:` line always matches the
  saved PNG.

## [0.3.2] - 2026-07-03

### Fixed
- Right-click **Copy Image** on the preview, broken since the 0.3.0 UI split.

## [0.3.1] - 2026-06-28

### Fixed
- Reliable silent self-update for installed Windows builds: the installer runs
  directly from the app, Mark-of-the-Web is removed from the downloaded
  installer, `AppMutex` is dropped so `/FORCECLOSEAPPLICATIONS` can close the
  app, command-line arguments are restored after the update, and the update
  process is detached so the app can exit cleanly.

## [0.3.0] - 2026-06-28

### Added
- New UI with an app bar and separate View / Edit modes, Save Image, a
  multi-page page switcher, a missing-page dialog and a preview cache.
- Multi-page atlas repack in modify mode.
- Windows installer (Inno Setup, per-user, no admin) with optional `.atlas`
  file association, plus a portable zip.
- Installer-based silent self-update (portable builds link to the releases
  page).
- `--debug` command-line flag.

### Changed
- Nuitka onedir build instead of a single bundled exe.
- Python code reorganised into the `atlas_toolkit` package.
- Atlas parsing and rendering fixes ported from the web version (offsets,
  rotation, page detection).

### Migration
- Builds from 0.2.2 cannot self-update to the installer: download and run it
  once.

## [0.2.2] - 2026-04-02

### Fixed
- Region names were sometimes misread as page images.
- Missing offsets when updating atlas text.
- Empty region selection in the modifier.
- Hardened update-notification rendering.

## [0.2.1] - 2026-02-26

### Fixed
- `rotate` was not written when the original region had no `rotate:` line but
  the merged region needed rotation.
- Bounds were swapped after rotation; Spine bounds keep pre-rotation sizes.

## [0.2.0] - 2026-02-24

### Added
- LibGDX-to-Spine atlas format converter.
- Update check on startup with a notification bar.
- Repack preference saved between sessions.
- App version in the window title.

### Changed
- Executable signing in CI.
- Cleaner atlas text output matching the standard Spine format.

### Fixed
- Manual CI builds showed the branch name as the version.

## [0.1.2] - 2026-02-21

### Fixed
- Packaged build crashes: drag-and-drop setup, console output buffering and a
  pywebview recursion issue under Nuitka.
- Multi-monitor window centring and header text wrapping.
- Release automation: tagging now triggers the build workflow.

[0.4.1]: https://github.com/com55/AtlasToolkit/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/com55/AtlasToolkit/compare/v0.3.4...v0.4.0
[0.3.4]: https://github.com/com55/AtlasToolkit/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/com55/AtlasToolkit/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/com55/AtlasToolkit/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/com55/AtlasToolkit/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/com55/AtlasToolkit/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/com55/AtlasToolkit/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/com55/AtlasToolkit/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/com55/AtlasToolkit/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/com55/AtlasToolkit/compare/v0.1.1...v0.1.2
