# Developer notes

Technical notes for anyone working on AtlasToolkit: how the pieces fit, the
rules that are easy to break, and decisions that look like bugs but are not.
User-facing changes are in `CHANGELOG.md`.

## Layout

- `www/` is the whole app: a static site with no build step (plain ES
  modules). The same files run as the web app (GitHub Pages / PWA) and inside
  the desktop app.
- `www/js/` is the app's atlas engine: parse → model → serialize
  (`atlas-document.js`), crop/rotate/offset math (`core-region-ops.js`),
  extraction (`atlas-extracter.js`), mod preparation and packing
  (`atlas-modifier.js`, `repack-nest.js`), edit sessions (`atlas-session.js`)
  and the UI-facing API (`atlas-api.js`). `platform.js` isolates browser vs
  desktop differences.
- `atlas_toolkit/app/` is the desktop shell only: pywebview window, native
  dialogs, disk I/O, drag-and-drop paths and the self-updater
  (`atlas_toolkit/update/`). It holds no atlas logic.
- The rest of `atlas_toolkit/` is a Python port of `www/js/`, used as a
  library and not by the app (see "Python engine" below): `api.py`
  (`SpineAtlas`), `document.py`, and `domain/`, `engine/`, `mesh/`, `raster/`,
  which mirror the JS modules one for one.
- The original Python engine (before the JS rewrite) was retired. Its last
  version is the pinned oracle commit `9655e3c` (see
  `tests/browser/gen-ground-truth-ops.py`); it is read with
  `git show <sha>:<path>`, never from a branch name. The port above is new
  code, not that engine.
- Root `package.json` exists only so `node --test` can load the ES modules.

## Branches and releases

- Work lands on `dev`; `main` receives releases. `legacy/js-project` and
  `legacy/python-engine` are archives, never pushed to.
- The Python engine lives on its own line (`rewrite/python-base`), which is
  never merged into `dev`. If it replaces `main`, the branches are swapped,
  not merged.
- Bump `version` in `pyproject.toml` and run `uv lock` in the same commit. A
  push to `main` that changes `pyproject.toml` runs `auto_tag.yml`, which tags
  `vX.Y.Z` and calls `build_release.yml` (Windows installer + portable zip).
- `deploy_pages.yml` deploys `www/` to GitHub Pages on pushes to `main` that
  touch `www/**`. Both workflows can be run by hand (`workflow_dispatch`)
  against any branch for a test build.
- The installer removes the `ui` folder left by 0.3.x installs.

## Service worker cache

Every module in `www/js/` must be listed in `CORE_ASSETS` in `www/sw.js`, and
`CACHE_NAME` (`atlas-toolkit-vN`) must be bumped **in the same commit** as any
change to a cached file. Otherwise installed PWAs keep serving the old files.
Check the changed-file list against `CORE_ASSETS` before committing; this has
been missed several times.

## Vendored `.skel` parser

`www/js/vendor/spine-skeleton-binary/` is gitignored. CI copies it from the
repository and tag in the `VENDOR_SOURCE_REPO` / `VENDOR_SOURCE_TAG` secrets.
The tag must be v1.1.0 or later: earlier versions do not return `hullLength`,
and the mesh silhouette overlay then draws nothing without any error. For local
work, copy the parser's `src/*.js` into that folder.

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

## Engine invariants

These look like refactoring opportunities but are deliberate:

- Scale and ratio math uses `roundHalfEven` (`core-region-ops.js`), never
  `Math.round`.
- Every page canvas produced by packing is rounded **up** to a multiple of 4
  after the size is chosen (transparent padding on the right/bottom only, no
  sprite moves). The `size:` line comes from the same rounded value.
- `_prepareModImage` returns two different flags: `sharedCanvasMod` (more than
  one selected region sharing one canvas) and `isFullCanvas` (the mod needs no
  offset or trim, also true for one region). Repack resets offsets only for
  regions in `fullCanvasRegions`, which comes from `isFullCanvas`. Mixing the
  two corrupted repack output until 0.3.4.
- Repack keeps each region's pristine offsets except for full-canvas mods.
- Every apply replays the full ordered batch list from pristine copies; it
  never builds on the previous result.
- Repack dedups pixel-identical sprites per page. `_canvasHash` does one
  throwaway `getImageData()` read first because Chromium can return a wrong
  digest on the first read of a large, freshly drawn canvas.
- `atlas-modifier.js`'s merge path (`mergeModImage`, `findBestPlacement`,
  `repack(mergedCanvas, …)`, `repackMultiPage`) has no production caller since
  repack became always-on. It still has unit tests, so a passing suite does
  not mean it is reachable.

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
  share the loaded `.skel`. The `.skel` picker follows the toggle of the
  current mode.
- Long or structural operations run inside `withBusy` / `withLock`
  (`busy-overlay.js`). New entry points must check `isBusy()` and run inside
  them: a full-screen overlay blocks the mouse but not keyboard focus.
- Use `showAlert()` rather than a toast for anything that can appear while
  another overlay is open; a toast renders underneath it.
- Preview auto-fit goes through `fitScaleIfOversized()` (`preview.js`) only.
- On touch devices file pickers drop the `accept` filter (native pickers often
  hide files otherwise) and validate the selection afterwards.

## Tests

- `npm test` runs the Node unit tests in `tests/*.test.js`.
- `npm run test:browser` runs every `tests/browser/verify-*.mjs` through
  `run-all.mjs` with `playwright-core`; each suite skips itself if no browser
  is available. `verify-ui-flows.mjs` has a known flaky set (composite preview
  rendered, next → Page 2/2 preview changed, Reset restores pristine edit
  view, cancel aborts load) that fails the same way on untouched code.
- Browser harnesses inject page scripts through template literals: a stray
  backtick or `\'` inside one silently truncates the script, and only running
  the test shows it.
- Any test of a mask or composite needs at least one "definitely transparent"
  (`alpha === 0`) assertion, not only "opaque here".
- Fixtures are synthetic. `tests/fixtures/mesh-sample.json` comes from
  `tests/fixtures/gen-mesh-sample.mjs`. Real-world atlases stay local in
  `.workspaces/` (gitignored); cases generated from them go to the gitignored
  `tests/browser/ground_truth_ops.local.json` and are skipped when absent.
- Native OS drag-and-drop onto the desktop window cannot be automated; check
  it by hand before a release.

## Known limitations

- Smart Packing with Mesh-Aware on very large atlases (around 4096×4096 with
  hundreds of regions) can exceed the 5-minute limit in `job-runner.js`.
- App: Add / Rename / Remove are single-page only. The Python library edits
  multi-page atlases in its deferred mode (`SpineAtlas`); its immediate mode
  (the app-style session) still refuses every edit on a multi-page atlas.
- Python library: picking a page automatically in `add_region` rebuilds every
  page's model on each call, which adds up over hundreds of adds; pass `page=`
  in bulk.

## Design decisions

### Removed: "Repack All Pages To One" (Task 4b)

A UI option under the Repack toggle once let the user collapse a multi-page
atlas into a single page (`sel-repack-mode` = `all`, backed by
`_repackAllPagesToSingle` / `_packCanvasesSimple` in `atlas-api.js`). It was
removed because:

- It has **no equivalent in the historical Python engine** (pinned SHA
  `9655e3c`; that source is no longer in this tree) — that engine only did
  per-page repack; identical-logic parity with it is the top priority.
- It had **no real use case** and only ever carried the *active* page's modded
  pixels into the combined canvas (mods on other pages silently reverted to
  original pixels), so it was subtly wrong for the one scenario it targeted.

Only the mode *selector* was removed; the Repack toggle itself remains (it now
means the single per-page repack mode, which is what `repackMode === 'page'`
always did). The `repackMode` preference key is no longer read or written; any
stale value in localStorage is harmless.

It can be re-added later if a genuine need arises, but should then share the
real shelf-packer (`_shelfPack` in `atlas-modifier.js`) rather than the toy
`_packCanvasesSimple` bin-packer, with an explicit decision on dedup and
rotation semantics for the cross-page combine.

### Deviation: modify-mode overlay bounds are scaled per page

`AtlasSession.getModifyRegionBounds()` (`atlas-session.js`), used by
`enter_modify_mode()`, builds one `AtlasModifier` per page (mirroring
`_registerModBatch`'s multi-page branch) so each page's regions are scaled
against *that page's own* loaded image size.

The historical Python `session.py::build_modify_view` (pinned SHA `9655e3c`)
instead built a single `AtlasModifier` from only the first page's image and
applied its scale factor to every region's bounds, regardless of which page
they're on. For single-page atlases (the common case, and the one this fixed
a real bug for — see the "scale-mismatch" case in
`tests/browser/verify-app-e2e.mjs`) the two approaches are identical. They'd
only diverge for a multi-page atlas whose pages mismatch their declared
`size:` by *different* ratios, which is pathological and untested on either
side. `_register_mod_batch` in that same historical `session.py` already went
per-page for the exact same reason, so `build_modify_view`'s first-page-only
scale looks like an oversight there, not an intentional contract — this
engine intentionally does not reproduce it.

### Deviation: repack packing diverges from repacker.py

`_shelfPack` (`www/js/atlas-modifier.js`) no longer selects purely by
minimum packed area, the way `repacker.py::repack_from_sprites` (and this
engine's own earlier behavior) did. It now collects every candidate width's
result, then picks the one closest to a 1:1 aspect ratio among those within
15% of the minimum area -- so a set of sprites that could tile into either a
20x120 strip or a 40x60 rectangle at the identical area now produces the
40x60 rectangle.

This is an intentional, permanent divergence from the pinned Python oracle,
not a bug to reconcile: the project is retiring the Python reference
implementation entirely in favor of `www/js/` as the sole engine (part of a
multi-spec Repack rework series -- specs for this series are kept
local per this project's convention, not committed),
and several of that series' later changes (mesh-masked packing sources,
silhouette-aware nesting, multi-page pooling) have no Python equivalent at
all. `repacker.py`'s pixel-level correctness (rotation, offsets, banker's
rounding, crop math) remains the reference for everything BUT packing
placement -- `tests/browser/verify-ops.mjs`'s `extractCases`/`mergeCases`
still assert exact parity against it. Only the 3 repack-op fixtures in
`ground_truth_ops.json` are re-pinned to the JS engine's own output instead
(via `tests/browser/regen-repack-fixtures.mjs`, re-run after any future
change to the packing algorithm).

Two of the three re-pinned fixtures (`repackCases[1]`, the `realworldCases`
multi-page "atlas_a" case) exercise the standalone `repackMultiPage()` export in
`atlas-modifier.js` — which has zero production callers. Real multi-page
repack goes through `AtlasSession._rebuildMultiPageRepack()` instead, which
repacks each touched page independently via `AtlasModifier.repackWithModdedSprites()`
(the single-page packer, dedup ON per page) — deliberately rewritten away
from `repackMultiPage()`'s old global cross-page sprite reassignment after it
caused a real bug (moving a sprite from one page to another unexpectedly; see
`_rebuildMultiPageRepack()`'s own comment). `repackMultiPage()` never dedups
at all, unlike the real per-page production path — so these two fixtures
protect the standalone helper's own behavior, not the app's actual multi-page
repack behavior. (This also means the "multi-page repack does NOT dedup"
rule recorded elsewhere in project memory is now describing this orphaned
helper, not the live app — worth correcting separately.)

Masking (mesh-masked-repack-source spec) happens before `_canvasHash` runs, so dedup groups
now reflect *visible* pixels rather than raw rectangle pixels — two regions with identical
raw crops but different meshes no longer dedup together; two regions with different raw
crops that happen to look identical after their respective masks now do. This is strictly
more correct (dedup is meant to mean "these end up looking the same") and is a documented,
intentional side effect, not a bug.

Nest Regions (mesh-silhouette-nesting spec), when its own toggle is on, is a further, opt-in
deviation from any Python parity concept -- packing shape and canvas dimensions are not
expected to match `repacker.py` at all once enabled, same spirit as the squareness-bias note
above.

Nest Regions is also the first place AtlasToolkit ever emits `rotate: 180` or `rotate: 270`
in a saved `.atlas` file -- every prior packer (this engine's `_shelfPack` and the retired
Python `repacker.py`) only ever produced `rotate: true`/`false` (0 or 90). This is a
deliberate, confirmed decision (not an oversight): the target Spine runtimes are confirmed to
accept non-boolean `rotate` values, so `nestPack`'s full 4-way rotation search
(`repack-nest.js`'s `rotationVariants`) is kept as-is rather than restricted to `[0, 90]`.

## Python engine

- **Port only live code.** Before porting a JS function, check with grep that
  production calls it (the merge path in `atlas-modifier.js` is dead, see
  above).
- **Package rule:** nothing under `atlas_toolkit/domain/` imports
  `atlas_toolkit/engine/`. `tests/python/test_packing_import_boundary.py`
  enforces it by AST, relative imports included; `domain/packing.py` gets the
  modifier helpers it needs by injection.
- **Sessions:** `engine/session.py`'s `AtlasSession` has an immediate mode
  (repack after every edit, like the app) and a deferred mode (record edits,
  pack once in `pack_pending`) that `SpineAtlas` uses. Each `pack_pending`
  replays every batch from the pristine pages and packs only the pages that
  own a modified, added or removed region; a page with only renames keeps its
  pixels and gets new names in its text.
- **Name order:** the packers trust the caller's region order. JS gets it from
  `Object.keys()`; Python callers pass names through `js_object_key_order()`.
- **`AtlasDocument.with_updates` shares list fields** (`split`, `pad`,
  `extra_pairs`, whole unchanged `Region` / `Page` objects) with its input.
  Treat regions and pages taken from a document as read-only; rebuild through
  `with_updates` / `clone` / `from_rebuild_args`.
- **Pillow `paste(src, box)` without a mask overwrites** (alpha included). For
  "source over", paste onto a transparent layer and `Image.alpha_composite`.
- The deliberate differences from the JS engine are listed in `CHANGELOG.md`
  under "Differences from the app's engine". Keep new ones out of parity
  cases and pin them with their own tests.
- **Pack modes:** `atlas_toolkit/packer.py`'s `resolve_packer` turns
  `packer` / `PackerOptions` into the session's existing switches, plus two
  Python-only `pack_pending` keywords (`shelf_padding`, `mesh_footprint`) that
  `_pack_page` adds to a copy of `get_nest_options()`. That dict itself stays
  the JS port. Modes that cannot be expressed this way (`grid`, `*_in_place`)
  need the modifier to dispatch on the mode itself: `grid` needs a new packer
  function, and `*_in_place` needs the original placements plus a per-region
  fit check with a fallback, so the session would hold one pack config and
  `_pack_and_emit` would dispatch on `Packer`.
  `tests/python/gen_packer_refactor_golden.py` pins the
  pre-refactor layouts at the session layer.

### Python tests

- `uv run pytest tests/python -q` runs the suite.
- JS parity comes in two kinds:
  - pixel work runs the real JS in Chromium: `tests/python/gen_js_reference_*.mjs`
    (`playwright-core`, skipped when missing) writes a committed JSON fixture,
    and the tests compare decoded pixels, never PNG bytes;
  - pure logic runs the JS in plain Node per test (through `conftest.py`
    fixtures); generators such as `gen_js_reference_packing.mjs` use a
    `FakeCanvas` that records draws instead of rendering.
- Regenerate a fixture with `node tests/python/gen_js_reference_<name>.mjs`
  and check that the existing cases did not change.
- No parity case may use a `pma: true` page or extra page lines such as
  `scale:`: the Python engine keeps them and the JS engine does not. The
  Python engine also reads and writes pma pages premultiplied (masking,
  pasting, deduplication, Lanczos and the composite split keep alpha-0
  colour), where the JS engine treats them as straight alpha.
- Byte-exact mask oracles need axis-aligned geometry: a diagonal edge hits
  partly covered pixels, which Canvas antialiases and the Python rasterizer
  does not.
- A passing test is not proof that a guard works. Break the code on purpose
  and see the test fail, check that the edit actually landed, and restore
  from a saved copy (not `git checkout`, which drops uncommitted work). Vary
  both axes, use patterned images where every pixel is different, and pin
  split alpha per piece rather than only through a composite.

## Python port: packaging

The core engine (`atlas_toolkit/{document,domain,engine,mesh,raster}`) needs only
`numpy`, `pillow` (a range, `>=10.0,<13`; the suite passes on 10.0.1 and 12.x)
and `skel-mesh-parser`. The desktop shell (`atlas_toolkit.app`,
`atlas_toolkit.update`) needs `pywebview` and `requests`, which live in the
`app` extra: `uv sync --extra app`, and `uv export --extra app` in the release
workflow. `atlas_toolkit/app/paths.py` (was `atlas_toolkit/paths.py`) is
desktop-only for the same reason.

**Open: `skel-mesh-parser` is a `git+https://` URL dependency.** `micropip`
cannot install that inside Pyodide. It is pinned to the tag `py-v0.3.0` (the
`python` branch's release that returns `hull_length`; Python tags are `py-v*`,
JS tags `js-v*`). The parser has no dependencies and is pure Python, so a
`py3-none-any` wheel of it only needs somewhere `micropip` can fetch it from
(a GitHub release asset, an index, or bundling it into the Worker's
filesystem). Not done yet.

`numpy>=2.4.6` is also higher than the numpy a given Pyodide release ships;
the numpy calls used (`cumsum`, `frombuffer`, boolean indexing) are old API,
so the floor is probably relaxable, but that has not been tested.
