# Design notes

## Removed: "Repack All Pages To One" (Task 4b)

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

## Deviation: modify-mode overlay bounds are scaled per page

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

## Deviation: repack packing diverges from repacker.py

`_shelfPack` (`www/js/atlas-modifier.js`) no longer selects purely by
minimum packed area, the way `repacker.py::repack_from_sprites` (and this
engine's own earlier behavior) did. It now collects every candidate width's
result, then picks the one closest to a 1:1 aspect ratio among those within
15% of the minimum area -- so a set of sprites that could tile into either a
20x120 strip or a 40x60 rectangle at the identical area now produces the
40x60 rectangle.

This is an intentional, permanent divergence from the pinned Python oracle,
not a bug to reconcile: the project is retiring the Python reference
implementation entirely in favor of `www/js/` as the sole engine (see the
Repack rework spec series starting
`docs/superpowers/specs/2026-09-04-repack-always-on-square-pack-design.md`),
and several of that series' later changes (mesh-masked packing sources,
silhouette-aware nesting, multi-page pooling) have no Python equivalent at
all. `repacker.py`'s pixel-level correctness (rotation, offsets, banker's
rounding, crop math) remains the reference for everything BUT packing
placement -- `tests/browser/verify-ops.mjs`'s `extractCases`/`mergeCases`
still assert exact parity against it. Only the 3 repack-op fixtures in
`ground_truth_ops.json` are re-pinned to the JS engine's own output instead
(via `tests/browser/regen-repack-fixtures.mjs`, re-run after any future
change to the packing algorithm).
