# Developer notes

Technical notes for anyone working on AtlasToolkit: how the pieces fit, the
rules that are easy to break, and decisions that look like bugs but are not.
User-facing changes are in `CHANGELOG.md`.

## Layout

- `atlas_toolkit/` is the atlas engine, in Python: `api.py` (`SpineAtlas`),
  `packer.py` (`PackOptions`), `document.py`, and `domain/`, `engine/`,
  `mesh/`, `raster/`. It is also usable as a library.
- `atlas_toolkit/host/` (`AppHost`) is the app's interface to the engine: the
  UI calls it with JSON arguments plus binary buffers.
- `www/` is the UI. `scripts/build_web.py` builds it into `dist/web` (the web
  app / PWA: the engine runs in Pyodide inside a Worker) or, with
  `--target desktop`, into the page the desktop app serves. `www/sw.js` is a
  template, so serve a build, never `www/` itself.
- `atlas_toolkit/app/` is the desktop shell: a pywebview window that loads the
  UI from a token-guarded local server running the same `AppHost` in CPython,
  plus native dialogs, disk I/O and the self-updater (`atlas_toolkit/update/`).
- The JS engine that shipped up to v0.4.2 is archived on `legacy/js-app`;
  nothing in this tree depends on it. Comments that say "port of X.js" name
  where a function came from.

## Branches and releases

- `main` receives releases. `legacy/js-app`, `legacy/js-project` and
  `legacy/python-engine` are archives, never pushed to.
- Bump `version` in `pyproject.toml` and run `uv lock` in the same commit. A
  push to `main` that changes `pyproject.toml` runs `auto_tag.yml`, which tags
  `vX.Y.Z` and calls `build_release.yml` (Windows installer + portable zip).
- `deploy_pages.yml` builds the web app and deploys it to GitHub Pages.
- Both workflows can be run by hand (`workflow_dispatch`) against any branch
  for a test build; a manual `build_release.yml` run sets `DEV_BUILD`, which
  adds "Install update from file…" for testing the self-update.
- The installer removes the `ui` folder left by 0.3.x installs.

## Service worker cache

`scripts/build_web.py` writes `sw.js` with every built file in `CORE_ASSETS`
and a `CACHE_NAME` (`atlas-toolkit-py-<digest>`) derived from their content,
so a changed file always gets a new cache. On activation it deletes the other
`atlas-toolkit-py-*` caches and the old JS app's `atlas-toolkit-v*` caches.

## Spine atlas format

- `offsets: left, bottom, originalWidth, originalHeight`: the Y offset counts
  from the **bottom**. Paste at `y = originalHeight - offY - h`.
- `rotate: true` means the region is stored rotated 90° counter-clockwise;
  it is restored with a 90° clockwise turn. Bounds always hold the
  pre-rotation width and height; the on-page footprint is swapped.
- A page line is a filename ending in `.png` with no `:`.
- The serializer canonicalizes: default `offsets`, `rotate`, `format`,
  `filter`, `repeat`, `pma` and `index` lines are dropped on save. A byte diff
  against a hand-written atlas will show them disappear; that is expected.
- The nest packers can write `rotate: 180` and `rotate: 270`; the target
  Spine runtimes accept them.

## Engine invariants

These look like refactoring opportunities but are deliberate:

- Scale and ratio math uses `round_half_even` (`domain/region_ops.py`), never
  `round()` on halves.
- Every page canvas produced by packing is rounded **up** to a multiple of 4
  after the size is chosen (transparent padding on the right/bottom only, no
  sprite moves). The `size:` line comes from the same rounded value.
- `prepare_mod_image` returns two different flags: `shared_canvas_mod` (more
  than one selected region sharing one canvas) and `is_full_canvas` (the mod
  needs no offset or trim, also true for one region). Repack resets offsets
  only for regions in `full_canvas_regions`, which comes from
  `is_full_canvas`. Mixing the two corrupted repack output once.
- Repack keeps each region's pristine offsets except for full-canvas mods.
- Every pack replays the full ordered batch list from pristine copies; it
  never builds on the previous result.
- Repack dedups pixel-identical sprites per page, after mesh masking, so dedup
  follows the visible pixels.
- The shelf packer keeps every candidate width within 15% of the smallest
  area and then takes the squarest.

## Region identity

Region lists are `{key, label}` pairs. `key` is the stable parser identity
used for lookups, mods and session batches; `label` is what is displayed and
written. They differ after a Rename. Extracted filenames are built from
`key` on purpose, so two regions with the same atlas name cannot overwrite
each other.

## UI rules

- Any code that shows or hides a row inside `#left-panel` or `#right-panel`
  must call `refreshPanelSplit()` (`panel-resizer.js`) afterwards, or the
  portrait splitter's limits go stale.
- Mesh Cropping (View) and Mesh-Aware (Edit) are independent toggles that only
  share the loaded `.skel`.
- Long or structural operations run inside `withBusy` / `withLock`
  (`busy-overlay.js`). New entry points must check `isBusy()` and run inside
  them: a full-screen overlay blocks the mouse but not keyboard focus.
- Use `showAlert()` rather than a toast for anything that can appear while
  another overlay is open; a toast renders underneath it.
- Preview auto-fit goes through `fitScaleIfOversized()` (`preview.js`) only.
- On touch devices file pickers drop the `accept` filter (native pickers often
  hide files otherwise) and validate the selection afterwards.
- Settings locks and fallbacks are computed by the host
  (`preview_settings`), never duplicated in JS. Tooltips use `data-help`, and
  every info button opens the one info modal.

## Tests

- `uv run pytest tests/python -q` runs the Python suite.
- `npm test` runs the Node unit tests of the UI modules (`tests/*.test.js`).
- `npm run test:browser` runs the browser suites (`verify-python-app.mjs`,
  `verify-desktop-app.mjs`, `verify-engine-transfer.mjs`) through
  `run-all.mjs` with `playwright-core`; build the site first. Each suite skips
  itself if no browser is available.
- Browser harnesses inject page scripts through template literals: a stray
  backtick or `\'` inside one silently truncates the script, and only running
  the test shows it.
- Any test of a mask or composite needs at least one "definitely transparent"
  (`alpha === 0`) assertion, not only "opaque here".
- Fixtures are synthetic. Real-world atlases stay local in `.workspaces/`
  (gitignored).
- Native OS drag-and-drop onto the desktop window cannot be automated; check
  it by hand before a release.
- A passing test is not proof that a guard works. Break the code on purpose
  and see the test fail, check that the edit actually landed, and restore
  from a saved copy (not `git checkout`, which drops uncommitted work). Vary
  both axes, use patterned images where every pixel is different, and pin
  split alpha per piece rather than only through a composite.
- Pinned goldens (`tests/python/fixtures/packer_refactor_golden.json`,
  `pma_straight_baseline.json`, the hashes in
  `test_patch_in_place_equivalence.py`) guard layouts and pixels across
  refactors; re-pin one only for an intended output change.

## Known limitations

- Python library: picking a page automatically in `add_region` rebuilds every
  page's model on each call, which adds up over hundreds of adds; pass `page=`
  in bulk.
- Pyodide runs packing about twice as slowly as CPython, and its wasm32 heap
  is capped near 4 GB.

## Python engine

- **Package rule:** nothing under `atlas_toolkit/domain/` imports
  `atlas_toolkit/engine/`. `tests/python/test_packing_import_boundary.py`
  enforces it by AST, relative imports included; `domain/packing.py` gets the
  modifier helpers it needs by injection.
- **Sessions:** `engine/session.py`'s `AtlasSession` has an immediate mode
  (repack after every edit) and a deferred mode (record edits, pack once in
  `pack_pending`) that `SpineAtlas` uses. Each `pack_pending` replays every
  batch from the pristine pages and packs only the pages that own a modified,
  added or removed region; a page with only renames keeps its pixels and gets
  new names in its text.
- **Name order:** the packers trust the caller's region order; callers pass
  names through `js_object_key_order()` (integer-like names first, the order
  the JS engine produced).
- **`AtlasDocument.with_updates` shares list fields** (`split`, `pad`,
  `extra_pairs`, whole unchanged `Region` / `Page` objects) with its input.
  Treat regions and pages taken from a document as read-only; rebuild through
  `with_updates` / `clone` / `from_rebuild_args`.
- **Pillow `paste(src, box)` without a mask overwrites** (alpha included). For
  "source over", paste onto a transparent layer and `Image.alpha_composite`.
- **numpy 2:** `uint8 * 255` stays `uint8` and wraps; cast to `float64` (or a
  wider int) before pixel math and clip before casting back. Pyodide's `intp`
  is 32-bit, so index and key arithmetic that can pass 2³¹ uses explicit
  `int64`.
- **Pack settings:** `atlas_toolkit/packer.py`'s `resolve_packer` validates a
  `PackOptions` and resolves it completely into a frozen `ResolvedPack` --
  every switch the session already has, plus two Python-only `pack_pending`
  keywords (`shelf_padding`, `mesh_footprint`) that `_pack_page` adds to a
  copy of `get_nest_options()`. The `ResolvedPack` is also the build cache
  key, so `packer="auto"` and the packer it picks are one entry, and a bleed
  left auto is resolved at this point rather than in `api.py`.
  `tests/python/gen_packer_refactor_golden.py` pins the pre-refactor layouts
  at the session layer.

## Packaging

The core engine (`atlas_toolkit/{document,domain,engine,mesh,raster}`) needs only
`numpy`, `pillow` (a range, `>=10.0,<13`; the suite passes on 10.0.1 and 12.x)
and `skel-mesh-parser`. The desktop shell (`atlas_toolkit.app`,
`atlas_toolkit.update`) needs `pywebview` and `requests`, which live in the
`app` extra: `uv sync --extra app`, and `uv export --extra app` in the release
workflow. `atlas_toolkit/app/paths.py` is desktop-only for the same reason.

`skel-mesh-parser` is a `git+https://` URL dependency pinned to the tag
`py-v0.3.0` (Python tags are `py-v*`). `micropip` cannot install a URL
dependency inside Pyodide, so `build_web.py` builds `py3-none-any` wheels of
the parser and of `atlas_toolkit`, ships them in `dist/web/wheels/`, and the
Worker unpacks them before starting the host.
