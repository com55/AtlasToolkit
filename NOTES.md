# Design notes (js-project)

## Removed: "Repack All Pages To One" (Task 4b)

A UI option under the Repack toggle once let the user collapse a multi-page
atlas into a single page (`sel-repack-mode` = `all`, backed by
`_repackAllPagesToSingle` / `_packCanvasesSimple` in `atlas-api.js`). It was
removed because:

- It has **no Python equivalent** — the reference engine
  (`atlas_toolkit/`) only does per-page repack; identical-logic parity with
  Python is the top priority for this port.
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

Python's `session.py::build_modify_view` instead builds a single
`AtlasModifier` from only the first page's image and applies its scale
factor to every region's bounds, regardless of which page they're on. For
single-page atlases (the common case, and the one this fixed a real bug for
— see the "scale-mismatch" case in `test/browser/verify-app-e2e.mjs`) the two
approaches are identical. They'd only diverge for a multi-page atlas whose
pages mismatch their declared `size:` by *different* ratios, which is
pathological and untested on either side. `_register_mod_batch` in
`session.py` already goes per-page for the exact same reason, so
`build_modify_view`'s first-page-only scale looks like an oversight there,
not an intentional contract — this port intentionally does not reproduce it.
