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
