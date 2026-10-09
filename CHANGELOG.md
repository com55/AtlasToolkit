# Changelog

All notable changes to AtlasToolkit. Versions match the Git tags and the
[GitHub Releases](https://github.com/com55/AtlasToolkit/releases), which carry
the full notes and screenshots.

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
