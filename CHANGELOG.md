# Changelog

All notable changes to the AtlasToolkit apps. Versions match the `v*` Git
tags and the [GitHub Releases](https://github.com/com55/AtlasToolkit/releases),
which carry the full notes and screenshots. The `spine-atlas-toolkit` library
has its own [changelog](packages/spine-atlas-toolkit/CHANGELOG.md).

## [Unreleased]

### Added
- Before an atlas is open, a card in the preview area says which files to
  open together; click it to open them.
- A ring before "Preparing AtlasToolkit engine..." fills as the engine loads.
- Host mode: a web page can open an atlas in AtlasToolkit inside an iframe
  (`?host=<origin>`) and get the edited files back over `postMessage`; a
  desktop program can do the same with `--return <dir>`, which writes the
  outputs and a `result.json`. See "Embedding" in the README.

### Changed
- The .skel opened together with an atlas is used even when its name differs
  from the atlas's (`hero.atlas` with `hero-pro.skel`), as long as one atlas
  and one .skel are given. With several, the one named like the atlas wins.
- Desktop: Open can select the atlas, its images and its .skel together
  (default filter `*.atlas`; also `*.atlas;*.png;*.skel` and All files), and a
  drop of several files opens the atlas among them with its .skel.

## [1.0.1] - 2026-11-23 - app

App only; the library is unchanged.

### Fixed
- Extract no longer writes two regions to one file. A `/` in a region name
  makes folders (`head/face` → `head/face.png`, as the atlas was packed from
  subfolders), and names that still meet, ignoring case (`Head` and `head`),
  get ` (2)`, ` (3)`, … A single region is saved as just `face.png`.
- The web app asks before a refresh or closing the tab while there are
  unsaved edits.
- A failed Extract shows an error and puts the status back to Ready instead
  of staying on "Extracting...". When several regions go to a folder, the
  folder is picked before extracting, so a long extraction can no longer make
  the browser refuse the picker.
- GitHub Pages redeploys when the app's host code, `APP_VERSION` or the
  license files change.

### Changed
- The Python and JavaScript tests run on every push and pull request, and
  both release workflows wait for them before tagging.

## [1.0.0] - 2026-11-21 - Python engine

App 1.0.0 and library `spine-atlas-toolkit` 1.0.0. From here on the two have
separate versions: the app's is `APP_VERSION`, the library's
`spine_atlas_toolkit.__version__`.
The library's changes from here on are in its own
[changelog](packages/spine-atlas-toolkit/CHANGELOG.md).

A Python port of the atlas engine, usable as a library (`spine_atlas_toolkit`,
on PyPI as `spine-atlas-toolkit`). The apps run the Python engine too: the
web/PWA app in a Pyodide worker, the desktop app through a local server.

### Added
- About dialog (the "i" at the right of the status bar): app and library
  versions, the runtime and the third-party packages this build uses, and the
  license texts.

### Breaking: package name and public API
- The library is the `spine-atlas-toolkit` distribution and is imported as
  `spine_atlas_toolkit` (it was `atlas_toolkit`, never published).
- The app's own code (the host the UI calls, the desktop shell and the
  updater) moved to a separate `atlas_toolkit_app` package that is not
  published. `SpineAtlas` methods that exist only for the app are now
  `_app_pack`, `_app_pack_is_cached`, `_app_transaction`, `_app_region_views`,
  `_app_mesh_hull_layout` and `_app_validate_region_name`, and `RegionView` is
  no longer exported.

### Breaking: `PackOptions`
- `build()` and `save()` take one
  `options=PackOptions(...)` instead of `packer=`, `packer_options=` and
  `split_composite=`. `PackerOptions` is gone; the old keywords raise
  `TypeError`.
- `PackOptions` fields: `packer` (`"auto"` default, `"shelf"`, `"rectangles"`,
  `"polygons"`), `padding`, `mesh_aware`, `try_keep_layout` (default True),
  `bleed`, `share_composite_image` (default False).
  `"grid"`, `"rectangles_in_place"` and `"polygons_in_place"` are no longer
  packer names: keep the layout with `try_keep_layout=True` and any packer.
  `PackerOptions(repack="always")` is `try_keep_layout=False`;
  `split_composite=True` is the new default, and `split_composite=False` is
  `share_composite_image=True`.
- Default packer is `"auto"`: `"polygons"` when the atlas has usable mesh
  data, otherwise `"rectangles"` (it was `"shelf"`).
- Default output changes: pages with a replace or remove now try to keep their
  layout instead of being repacked; a `replace_composite` image is cut into
  pieces per region by default (it used to be shared whole); auto bleed is now
  on by default, since it follows the split.
- One old combination has no equivalent: `repack="auto"` used to patch an
  add-only page in the same build that repacked a page with a replace or
  remove. `try_keep_layout` is one switch for every page.

### Breaking
- Mesh-aware packing now applies to `pma: true` pages. `mesh_aware=None`
  (the default) masks them to their mesh hulls whenever mesh data is usable;
  opt out with `PackOptions(mesh_aware=False)`. `packer="polygons"` now
  nests and masks pma pages by their mesh hulls too; it always uses the mesh,
  so the only opt-out there is another packer. `mesh_crop=True` now crops
  regions on pma pages too, clearing all four channels outside the hull.

### Added
- `PackOptions(bleed=...)`: Spine's packer Bleed. On every page a build
  writes that is not pma, transparent pixels within 2 px of an image take the
  mean colour of their visible neighbours (alpha stays 0), so linear texture
  filtering draws no dark edges. `None` (default) bleeds when the build
  splits composites (with `share_composite_image=False`, the default);
  `True` / `False` force it. Untouched and rename-only pages keep their pixels.
- `PackOptions(try_keep_layout=True)` (default): a page whose edits
  fit keeps its layout. Removed regions leave empty space; a replaced region
  whose new pixels fit its old slot (the canvas size `extract()` gives,
  nothing visible outside the old trimmed rect, pixels shared with an
  overlapping neighbour unchanged) is drawn there with its rect and offsets
  unchanged; when composites are split, each piece is drawn into its region's
  slot if the image is that region's canvas size and the piece meets the
  same rules (with `share_composite_image=True` a page holding such a replace
  is repacked); additions go into free space. Any other
  edited page is repacked with the selected packer (including "shelf").
  `PackOptions(try_keep_layout=False)` repacks every edited page.
  Kept-layout pages are not deduplicated afresh: regions that become pixel-identical
  keep their own rects. Vacating a rect or pasting a mod can clear
  never-rendered pixels of an overlapping hulled region, so its extract()
  without mesh_crop may change; what Spine renders does not. Python engine
  only; the JavaScript apps have no in-place modes.
- `SpineAtlas`: open an atlas from disk (`open`) or memory (`load`), list
  regions, extract single, all or composite sprites (with Forced Resizing and
  Mesh Cropping), record edits (`replace`, `replace_composite`, `add_region`,
  `rename_region`, `remove_region`) and pack them once with `build()` /
  `save()` (`PackOptions`: packer, padding, mesh-aware, keep-layout, bleed).
- `PackOptions(share_composite_image=False)` (default): a `replace_composite`
  image is cut back into one piece per region, the inverse of
  `extract_composite(force_resize=True)`.
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
  colour and is otherwise exact when replaced back with the same mode. A
  composite group spanning pma and non-pma pages raises `MixedAlphaGroup` when
  `alpha_mode` is given.
- Loading refuses an atlas that lists one page file twice (`DuplicatePage`),
  as Spine does. Reading a region bigger than its page image raises
  `ValueError` instead of allocating a padded crop of any size.
- Packed pages are capped at 10000 px per side: `CanvasTooLarge` is raised
  before the page is allocated, with `.page` naming the page on a multi-page
  atlas.
- The core package needs only Pillow, numpy and the `.skel` parser; the
  desktop shell's dependencies moved to the `app` extra.

### Changed
- `"rectangles"` / `"polygons"` pages are trimmed to their regions' extent
  (rounded up to a multiple of 4); nest growth used to leave part of its last
  strip empty.
- The build cache keeps the last 8 packs of the current edits (within 512 MB
  of edited page pixels) instead of one, so switching settings back is
  instant. A pack that never used its padding (kept layout, no repack, no
  addition) is reused for any padding. `"auto"` and the packer it resolves to
  share one entry.
- A split composite on straight pages: each piece keeps the
  composite's colour under its alpha-0 pixels instead of black, so the seam
  between pieces filters cleanly where the piece is drawn in place.
- A page whose only edits are additions and renames is no longer repacked by
  default (pass `PackOptions(try_keep_layout=False)`).
- Removing a region added in the same atlas undoes the add; a single-page
  atlas used to repack every region.
- A single-page atlas with only renames keeps its image.
- Added regions with mesh data are cut to their hull when mesh-aware is on,
  nest by hull under `polygons`, and stay mesh regions in the built atlas.
- Patch placement may store new regions rotated 180/270 under every packer,
  shelf included, and may clear never-rendered pixels outside an existing mesh
  hull where a new region lands.
- `build()` / `save()` take `options=PackOptions(...)` instead of `nest`,
  `gap` and `mesh_aware`, which are removed.
- `padding` (formerly the Smart Packing gap) follows the Spine Texture Packer:
  pixels between images, default 2 for every packer (the Smart Packing gap
  was 4). The shelf packer now leaves this padding too; before, it packed
  sprites edge to edge.
- `mesh_aware` defaults to on when the atlas has usable mesh data, and
  `SpineAtlas.open()` reads a `.skel` next to the atlas. So a default
  `build()` masks repacked pages to the mesh hulls, and
  a split composite gives each meshed region its hull.
  `PackOptions(mesh_aware=False)` turns the masking off (the shelf layout
  still has at least 1 px padding; the old edge-to-edge layout is gone).
  Untouched and rename-only pages keep their pixels.
- `packer="rectangles"` can be combined with `mesh_aware`: pixels are cut to
  the hull, but the packing uses each region's whole rect.
- `pma: true` pages keep additive colour (RGB on alpha-0 pixels) through
  every repack, extract and split. A composite whose regions all live on pma
  pages round-trips exactly through `extract_composite` / `replace_composite`
  / a split `build()` when it is extracted with the same names in
  the same order, no region is resized and `mesh_crop` matches the build's
  ownership; additive colour goes to the topmost region
  covering it. In a group that mixes pma and non-pma pages, the pma pieces
  are cleared outside their owner and keep alpha-0 colour only where they
  are the topmost owner. Forced Resizing on pma pages uses a premultiplied Lanczos that
  scales a pixel down when its filtered alpha overshoots 255.
- `extract_composite` warns when its regions span pma and non-pma pages, as
  `replace_composite` already did; such a composite is stacked as straight
  alpha.

- Mesh Cropping previews and extracts are about 2-3x faster (vectorized mesh
  masks, masking only the area a region covers), with the same pixels.
  View Mode Extract and Save image write PNGs at compression level 3: about
  1.7x faster, files about 10% larger. Saved atlases keep the default level.
- The web app stores its settings under new keys, so existing web users
  start from the defaults once. The desktop app keeps `config.json`.
- The JS engine is gone from the tree (archived on `legacy/js-app`), and so
  are the tests that compared the Python engine with it.

### Fixed
- `pma: true` pages: composite extracts and previews stack premultiplied
  colour instead of treating it as straight alpha.

### Differences from the JS engine (0.4.x)
- Keeping a page's layout (adds go into free space), undo-on-remove and hull
  cutting of added regions are Python-only; the app always repacks and keeps
  added sprites whole.
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
- Nest pages are trimmed to their regions (the app keeps the grown canvas).
- The shelf packer leaves padding between sprites (the app packs them edge to
  edge), and nest packing defaults to 2 px padding (the app's gap is 4).
  `"rectangles"` can be combined with mesh_aware (the app always nests
  mesh-aware regions on their mesh).

## [0.4.2] - 2026-10-28

### Fixed
- Extracting several regions to a folder that already holds files with the same
  names: the "Overwrite files?" dialog was covered by the Processing overlay and
  could not be clicked. Only the extraction itself shows Processing now.

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
