"""Pristine session load, read, export, and lifetime management."""
from __future__ import annotations

from dataclasses import dataclass

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.domain.converter import auto_convert_atlas
from atlas_toolkit.domain.effective_model import derive_effective_model
from atlas_toolkit.domain.packing import normalize_gap
from atlas_toolkit.domain.region_validation import validate_region_name
from atlas_toolkit.engine._images import _SessionRaster
from atlas_toolkit.engine.composite_split import split_composite as cut_composite
from atlas_toolkit.engine.extraction import AtlasProcessor
from atlas_toolkit.engine.modifier import (
    AtlasModifier,
    PackResult,
    js_object_key_order,
    region_meta,
)
from atlas_toolkit.raster.base import ImageHandle, RasterBackend


class RegionNameError(ValueError):
    """An add/rename name rejected by validate_region_name; `.reason` is its message."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RemoveAllRegionsError(ValueError):
    """A deferred remove that would leave the atlas with no regions."""


@dataclass(frozen=True)
class RegionSummary:
    key: str
    label: str
    page_filename: str


@dataclass(frozen=True)
class SessionSnapshot:
    regions: tuple[RegionSummary, ...]
    pages: tuple[str, ...]
    is_multi_page: bool
    modified_keys: tuple[str, ...]
    has_pending_modifications: bool
    has_output: bool
    generation: int
    modify_region_bounds: dict[str, tuple[int, int, int, int, int]]
    mesh_available: bool
    mesh_mask_enabled: bool
    mesh_aware_repack_enabled: bool
    nest_regions_enabled: bool
    nest_gap_distance: int = 4


@dataclass(frozen=True)
class RepackResult:
    """Published pack metadata; image is borrowed until replacement or clear/close."""
    image: ImageHandle
    regions: dict[str, tuple[int, int, int, int, int]]
    region_pages: dict[str, str]
    pages: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "regions", {name: tuple(bounds) for name, bounds in self.regions.items()},
        )
        object.__setattr__(self, "region_pages", dict(self.region_pages))
        object.__setattr__(self, "pages", tuple(self.pages))


class AtlasSession:
    """In-memory atlas session; it does not write files or integrate a UI/bridge.

    Load, read, extract and pristine save support multi-page atlases. Nonempty
    edits and repack are single-page only and reject multi-page sessions before
    allocating candidate images.
    """

    def __init__(
        self, document: AtlasDocument, page_images: dict[str, ImageHandle], raster: _SessionRaster,
        *, defer_repack: bool = False,
    ) -> None:
        self._document = document
        self._page_images = dict(page_images)
        self._roots = frozenset(page_images.values())
        self._raster = raster
        self._processor = AtlasProcessor(document, self._page_images, raster)
        self._modifications_saved = False
        self._generation = 0
        self._mod_batches: list[dict] = []
        self._modded_sprites: dict[str, ImageHandle] = {}
        self._active: PackResult | None = None
        self._closed = False
        self._defer_repack = bool(defer_repack)

    @classmethod
    def load(
        cls, atlas_text: str, page_images: dict[str, bytes], raster: RasterBackend,
        *, defer_repack: bool = False,
    ) -> "AtlasSession":
        return cls._load(atlas_text, page_images, raster, "decode_png", defer_repack)

    @classmethod
    def load_images(
        cls, atlas_text: str, page_images: dict[str, object], raster: RasterBackend,
        *, defer_repack: bool = False,
    ) -> "AtlasSession":
        """Like load(), but pages are backend-native in-memory images
        (PIL.Image for PillowRasterBackend), adopted as copies."""
        return cls._load(atlas_text, page_images, raster, "adopt", defer_repack)

    @classmethod
    def _load(
        cls, atlas_text: str, page_images: dict, raster: RasterBackend,
        allocate: str, defer_repack: bool,
    ) -> "AtlasSession":
        document = AtlasDocument.parse(auto_convert_atlas(atlas_text))
        if not document.pages:
            raise ValueError("atlas must declare at least one page")
        filenames = document.page_filenames()
        missing = list(dict.fromkeys(name for name in filenames if name not in page_images))
        if missing:
            raise ValueError(f"missing page images: {', '.join(missing)}")
        owned = _SessionRaster(raster)
        allocate_page = getattr(owned, allocate)
        decoded: dict[str, ImageHandle] = {}
        try:
            for filename in filenames:
                if filename not in decoded:
                    decoded[filename] = allocate_page(page_images[filename])
            return cls(document, decoded, owned, defer_repack=defer_repack)
        except BaseException:
            owned.release_except(frozenset())
            raise

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("session is closed")

    def _repack_after_option_change(
        self,
        previous_repack_mask_enabled: bool,
        previous_nest_enabled: bool,
        previous_nest_gap_distance: object,
    ) -> RepackResult | None:
        if self._defer_repack or not self._mod_batches:
            return None
        try:
            return self.repack()
        except BaseException:
            self._processor._repack_mask_enabled = previous_repack_mask_enabled
            self._processor._nest_enabled = previous_nest_enabled
            self._processor._nest_gap_distance = previous_nest_gap_distance
            raise

    def set_mesh_data(self, lookup: dict | None) -> None:
        self._require_open()
        self._processor.set_mesh_mask_data(
            lookup,
            self._processor._mask_enabled,
            self._processor._repack_mask_enabled,
        )

    def set_mesh_mask_enabled(self, enabled: bool) -> None:
        self._require_open()
        enabled = bool(enabled)
        self._processor._mask_enabled = enabled

    def set_mesh_aware_repack_enabled(self, enabled: bool) -> RepackResult | None:
        self._require_open()
        enabled = bool(enabled)
        previous = (
            self._processor._repack_mask_enabled,
            self._processor._nest_enabled,
            self._processor._nest_gap_distance,
        )
        self._processor._repack_mask_enabled = enabled
        return self._repack_after_option_change(*previous)

    def set_nest_regions_enabled(self, enabled: bool) -> RepackResult | None:
        self._require_open()
        enabled = bool(enabled)
        previous = (
            self._processor._repack_mask_enabled,
            self._processor._nest_enabled,
            self._processor._nest_gap_distance,
        )
        self._processor.set_nest_options(
            enabled,
            self._processor._nest_gap_distance,
        )
        return self._repack_after_option_change(*previous)

    def set_nest_gap_distance(self, value: object) -> RepackResult | None:
        self._require_open()
        gap_distance = normalize_gap(value)
        previous = (
            self._processor._repack_mask_enabled,
            self._processor._nest_enabled,
            self._processor._nest_gap_distance,
        )
        self._processor.set_nest_options(
            self._processor._nest_enabled,
            gap_distance,
        )
        return self._repack_after_option_change(*previous)

    def _require_single_page_structural(self) -> None:
        if len(self._document.pages) > 1:
            raise NotImplementedError(
                "structural changes are not supported for multi-page atlases",
            )

    def _fresh_modifier(self) -> AtlasModifier:
        page = self._document.pages[0]
        return AtlasModifier(
            self._document, self._page_images[page.filename], self._raster, page.filename,
        )

    def _load_source(self, image: object) -> ImageHandle:
        """PNG bytes are decoded; anything else is a backend-native image, adopted."""
        if isinstance(image, (bytes, bytearray, memoryview)):
            return self._raster.decode_png(bytes(image))
        return self._raster.adopt(image)

    def _raw_effective_model(self, batches: list[dict] | None = None) -> dict:
        names = js_object_key_order(list(self._processor.regions))
        pristine = {
            key: region_meta(self._processor.regions[key])
            for key in names
        }
        return derive_effective_model(pristine, self._mod_batches if batches is None else batches)

    def _validated_name(self, candidate: str, exclude_key: str | None = None) -> str:
        effective = self._raw_effective_model()
        labels = []
        for key in effective["region_names"]:
            if key == exclude_key:
                continue
            labels.append(
                effective["labels"].get(key, key),
            )
        result = validate_region_name(candidate, labels)
        if not result.ok:
            raise RegionNameError(result.reason)
        return result.value

    def _build_repack(
        self, batches: list[dict], sprites: dict[str, ImageHandle], split_composite: bool = False,
    ) -> PackResult:
        full_canvas_regions = {
            name
            for batch in batches
            if batch.get("type") == "mod" and batch.get("is_full_canvas")
            for name in batch["names"]
        }
        modifier = self._fresh_modifier()
        mesh_lookup_fn = self._processor.get_repack_mesh_geometry
        nest_options = self._processor.get_nest_options()
        pieces = self._composite_pieces(modifier, batches, sprites, mesh_lookup_fn) if split_composite else {}
        if pieces:
            # New containers: pack_pending passes the session's live dict.
            sprites = {name: handle for name, handle in sprites.items() if name not in pieces}
            full_canvas_regions = full_canvas_regions - pieces.keys()
        if pieces or any(batch.get("type") != "mod" for batch in batches):
            pristine_names = js_object_key_order(modifier.region_names)
            pristine_regions = {
                key: region_meta(modifier.regions[key])
                for key in pristine_names
            }
            effective = derive_effective_model(pristine_regions, batches)
            added_sprites = {
                batch["internal_key"]: batch["source"]
                for batch in batches
                if batch.get("type") == "add"
            }
            return modifier.repack_with_effective_model(
                effective["region_names"],
                effective["regions"],
                added_sprites,
                sprites,
                full_canvas_regions=full_canvas_regions,
                mesh_lookup_fn=mesh_lookup_fn,
                nest_options=nest_options,
                piece_sprites=pieces or None,
            )
        return modifier.repack_with_modded_sprites(
            sprites,
            full_canvas_regions=full_canvas_regions,
            mesh_lookup_fn=mesh_lookup_fn,
            nest_options=nest_options,
        )

    def _composite_pieces(self, modifier, batches: list[dict], sprites: dict[str, ImageHandle], mesh_lookup_fn) -> dict:
        """Spec 2026-10-13 §3.1: cut every composite-marked mod batch whose
        survivors have no nine-patch and a non-empty offsets canvas; others
        stay as they are. A survivor without repack mesh (none, or mesh-aware
        repack off) is cut to its whole rect. Survivors: the batch's names (first occurrence) still
        holding its prepared image and not removed, in batch order."""
        removed = {b["target_key"] for b in batches if b.get("type") == "remove"}
        pieces: dict = {}
        for batch in batches:
            if batch.get("type") != "mod" or not batch.get("composite"):
                continue
            image = batch["prepared"].image
            survivors: list[str] = []
            for name in batch["names"]:
                if name in survivors or name in removed or sprites.get(name) != image:
                    continue
                survivors.append(name)
            if not survivors or any(name not in modifier.regions for name in survivors):
                continue
            metas = {name: region_meta(modifier.regions[name]) for name in survivors}
            geometry = {name: mesh_lookup_fn(name) for name in survivors}
            if any(m["split"] is not None or m["pad"] is not None for m in metas.values()):
                continue
            if any(m["offsets"] is not None and (m["offsets"][2] <= 0 or m["offsets"][3] <= 0) for m in metas.values()):
                continue  # malformed canvas size: nothing to scale against
            pieces.update(cut_composite(self._raster, batch["source"], survivors, metas, geometry))
        return pieces

    def _make_result(self, pack: PackResult) -> RepackResult:
        width, height = self._raster.size(pack.image)
        if width <= 0 or height <= 0:
            raise ValueError("Repack output must have positive dimensions.")
        regions = {
            name: tuple(bounds)
            for name, bounds in pack.region_bounds.items()
        }
        document_regions = pack.document.regions_by_key()
        region_pages = {
            name: document_regions[name].page_filename
            for name in regions
        }
        return RepackResult(
            image=pack.image,
            regions=regions,
            region_pages=region_pages,
            pages=tuple(pack.document.page_filenames()),
        )

    def _keep_set(self, batches: list[dict], sprites: dict[str, ImageHandle]) -> set[ImageHandle]:
        keep = set(self._roots)
        for batch in batches:
            batch_type = batch.get("type")
            if batch_type in ("mod", "add"):
                keep.add(batch["source"])
            if batch_type == "mod":
                keep.add(batch["prepared"].image)
        keep.update(sprites.values())
        return keep

    def _publish(
        self,
        batches: list[dict],
        sprites: dict[str, ImageHandle],
        active: PackResult,
        generation: int,
        modifications_saved: bool,
    ) -> None:
        self._mod_batches = batches
        self._modded_sprites = sprites
        self._active = active
        self._generation = generation
        self._modifications_saved = modifications_saved
        keep = self._keep_set(batches, sprites)
        keep.add(active.image)
        self._raster.release_except(frozenset(keep))

    def _record(self, batches: list[dict], sprites: dict[str, ImageHandle]) -> None:
        """Deferred mode: store the candidate batches without packing."""
        self._mod_batches = batches
        self._modded_sprites = sprites
        self._generation += 1
        self._modifications_saved = False
        self._raster.release_except(frozenset(self._keep_set(batches, sprites)))

    def snapshot(self) -> SessionSnapshot:
        self._require_open()
        effective = self._raw_effective_model()
        default_page = self._document.pages[0].filename
        summaries = tuple(
            RegionSummary(
                key,
                effective["labels"].get(key, key),
                effective["regions"][key].get("page", default_page),
            )
            for key in effective["region_names"]
        )
        if self._active is not None:
            bounds = {
                key: tuple(value)
                for key, value in self._active.region_bounds.items()
            }
        else:
            bounds: dict[str, tuple[int, int, int, int, int]] = {}
            for page in self._document.pages:
                image = self._page_images[page.filename]
                modifier = AtlasModifier(self._document, image, self._raster, page.filename)
                for key, region in modifier.regions.items():
                    bounds[key] = tuple(region.bounds_with_rotate())
        nest = self._processor.get_nest_options()
        return SessionSnapshot(
            regions=summaries,
            pages=tuple(self._document.page_filenames()),
            is_multi_page=len(self._document.pages) > 1,
            modified_keys=tuple(
                key for key in effective["region_names"]
                if key in effective["modified_keys"]
            ),
            has_pending_modifications=bool(self._mod_batches) and not self._modifications_saved,
            has_output=self._active is not None,
            generation=self._generation,
            modify_region_bounds=dict(bounds),
            mesh_available=bool(self._processor._mesh_lookup),
            mesh_mask_enabled=self._processor._mask_enabled,
            mesh_aware_repack_enabled=self._processor._repack_mask_enabled,
            nest_regions_enabled=nest["enabled"],
            nest_gap_distance=nest["gap_distance"],
        )

    def extract_regions(self, names: list[str]) -> dict[str, bytes]:
        self._require_open()
        before = self._raster.checkpoint()
        try:
            result: dict[str, bytes] = {}
            for name in names:
                image = self._processor.extract_region(name)
                if image is not None:
                    try:
                        result[name] = self._raster.encode_png(image)
                    finally:
                        self._raster.dispose(image)
            return result
        finally:
            self._raster.release_except(before)

    def get_preview_png(
        self, region_names: list[str] | None = None, force_resize: bool = False,
    ) -> bytes:
        self._require_open()
        before = self._raster.checkpoint()
        try:
            if region_names is None:
                image = self._active.image if self._active is not None else self._processor.get_page_image()
            else:
                image = self._processor.get_preview_image(region_names, force_resize)
            return b"" if image is None else self._raster.encode_png(image)
        finally:
            self._raster.release_except(before)

    def extract_images(self, names: list[str]) -> dict[str, object]:
        """extract_regions() without PNG: backend-native copies, keyed by name."""
        self._require_open()
        before = self._raster.checkpoint()
        try:
            result: dict[str, object] = {}
            for name in names:
                image = self._processor.extract_region(name)
                if image is not None:
                    try:
                        result[name] = self._raster.export(image)
                    finally:
                        self._raster.dispose(image)
            return result
        finally:
            self._raster.release_except(before)

    def get_preview_image(self, region_names: list[str], force_resize: bool = False) -> object | None:
        """get_preview_png(region_names, ...) without PNG: a backend-native copy."""
        self._require_open()
        before = self._raster.checkpoint()
        try:
            image = self._processor.get_preview_image(list(region_names), force_resize)
            return None if image is None else self._raster.export(image)
        finally:
            self._raster.release_except(before)

    def export_pages(self) -> dict[str, object]:
        """Copies of the pristine page images, in page order."""
        self._require_open()
        return {
            page.filename: self._raster.export(self._page_images[page.filename])
            for page in self._document.pages
        }

    def get_mesh_hull_layout(
        self, region_names: list[str], force_resize: bool = False,
    ) -> dict:
        """View Mode hull overlay for these pristine region keys, in the
        same composite space as the multi-region preview."""
        self._require_open()
        return self._processor.preview_hull_layout(list(region_names), force_resize)

    def save(self) -> tuple[str, dict[str, bytes]]:
        self._require_open()
        document = self._active.document if self._active is not None else self._document
        text = document.serialize()
        if self._active is not None:
            page = document.pages[0]
            pages = {page.filename: self._raster.encode_png(self._active.image)}
        else:
            pages = {
                page.filename: self._raster.encode_png(self._page_images[page.filename])
                for page in self._document.pages
            }
        self._modifications_saved = True
        return text, pages

    def apply_mod_image(
        self, image: bytes | object, selected_names: list[str], composite: bool = False,
    ) -> RepackResult | None:
        self._require_open()
        names = list(selected_names)
        if not names:
            return None
        if len(self._document.pages) > 1:
            raise NotImplementedError("pixel modifications are not supported for multi-page atlases")

        before = self._raster.checkpoint()
        try:
            source = self._load_source(image)
            prepare_modifier = self._fresh_modifier()
            ordered_names = [name for name in names if name in prepare_modifier.regions]
            ordered_names.extend(name for name in names if name not in prepare_modifier.regions)
            prepared = prepare_modifier.prepare_mod_image(source, ordered_names)
            batch = {
                "type": "mod",
                "names": list(names),
                "source": source,
                "prepared": prepared,
                "is_full_canvas": prepared.is_full_canvas,
                "composite": bool(composite),
            }
            candidate_batches = [*self._mod_batches, batch]
            candidate_sprites = dict(self._modded_sprites)
            for name in names:
                candidate_sprites[name] = prepared.image
            if not self._defer_repack:
                candidate = self._build_repack(candidate_batches, candidate_sprites)
                result = self._make_result(candidate)
        except BaseException:
            self._raster.release_except(before)
            raise

        if self._defer_repack:
            self._record(candidate_batches, candidate_sprites)
            return None
        self._publish(
            candidate_batches,
            candidate_sprites,
            candidate,
            self._generation + 1,
            False,
        )
        return result

    def _apply_structural_batches(
        self,
        candidate_batches: list[dict],
        before: frozenset[ImageHandle] | None = None,
    ) -> None:
        if before is None:
            before = self._raster.checkpoint()
        if self._defer_repack:
            self._record(candidate_batches, dict(self._modded_sprites))
            return
        try:
            candidate_sprites = dict(self._modded_sprites)
            candidate = self._build_repack(candidate_batches, candidate_sprites)
            self._make_result(candidate)
        except BaseException:
            self._raster.release_except(before)
            raise
        self._publish(
            candidate_batches,
            candidate_sprites,
            candidate,
            self._generation + 1,
            False,
        )

    def add_region(self, image: bytes | object, name: str) -> None:
        self._require_open()
        self._require_single_page_structural()
        atlas_name = self._validated_name(name)
        reserved = set(self._processor.regions)
        reserved.update(
            batch["internal_key"]
            for batch in self._mod_batches
            if batch.get("type") == "add"
        )
        internal_key = atlas_name
        suffix = 2
        while internal_key in reserved:
            internal_key = f"{atlas_name}_{suffix}"
            suffix += 1

        before = self._raster.checkpoint()
        try:
            source = self._load_source(image)
            candidate_batches = [
                *self._mod_batches,
                {"type": "add", "internal_key": internal_key, "atlas_name": atlas_name, "source": source},
            ]
        except BaseException:
            self._raster.release_except(before)
            raise
        self._apply_structural_batches(candidate_batches, before=before)

    def rename_region(self, key: str, new_name: str) -> None:
        self._require_open()
        self._require_single_page_structural()
        atlas_name = self._validated_name(new_name, exclude_key=key)
        candidate_batches = [
            *self._mod_batches,
            {"type": "rename", "target_key": key, "new_atlas_name": atlas_name},
        ]
        self._apply_structural_batches(candidate_batches)

    def remove_regions(self, keys: list[str]) -> None:
        self._require_open()
        targets = list(keys)
        if not targets:
            return
        self._require_single_page_structural()
        candidate_batches = [
            *self._mod_batches,
            *({"type": "remove", "target_key": key} for key in targets),
        ]
        if self._defer_repack and not self._raw_effective_model(candidate_batches)["region_names"]:
            raise RemoveAllRegionsError("cannot remove every region")
        self._apply_structural_batches(candidate_batches)

    def repack(self) -> RepackResult:
        self._require_open()
        if self._defer_repack:
            raise RuntimeError("repack() is not available in deferred mode; use pack_pending()")
        if len(self._document.pages) > 1:
            raise NotImplementedError("pixel modifications are not supported for multi-page atlases")

        before = self._raster.checkpoint()
        try:
            candidate = self._build_repack(self._mod_batches, self._modded_sprites)
            result = self._make_result(candidate)
        except BaseException:
            self._raster.release_except(before)
            raise

        self._publish(
            self._mod_batches,
            self._modded_sprites,
            candidate,
            # v0.4.0 JS rerunRepack() keeps modGeneration: callers key option
            # changes by pack signature, not generation.
            self._generation,
            self._modifications_saved,
        )
        return result

    def pack_pending(self, split_composite: bool = False) -> tuple[str, dict[str, object]]:
        """Pack the recorded batches once and return (text, {page: native image}).
        Never publishes: batches, sprites, pristine pages and snapshot are
        unchanged, and every handle the call allocated is released."""
        self._require_open()
        if not self._mod_batches:
            return self._document.serialize(), self.export_pages()
        before = self._raster.checkpoint()
        try:
            if split_composite:
                candidate = self._build_repack(self._mod_batches, self._modded_sprites, split_composite=True)
            else:
                candidate = self._build_repack(self._mod_batches, self._modded_sprites)
            self._make_result(candidate)
            page = candidate.document.pages[0]
            return candidate.document.serialize(), {page.filename: self._raster.export(candidate.image)}
        finally:
            self._raster.release_except(before)

    def region_labels(self) -> list[tuple[str, str]]:
        """(key, current label) for the effective model, in order."""
        self._require_open()
        effective = self._raw_effective_model()
        return [(key, effective["labels"].get(key, key)) for key in effective["region_names"]]

    @property
    def has_pending_edits(self) -> bool:
        return bool(self._mod_batches)

    def clear_modify_state(self) -> None:
        self._require_open()
        self._mod_batches = []
        self._modded_sprites = {}
        self._active = None
        self._modifications_saved = False
        # generation is not reset (v0.4.0 JS clearModifyState): a later
        # mod must never reuse a generation already seen before the clear.
        self._raster.release_except(self._roots)

    def close(self) -> None:
        if not self._closed:
            self._raster.release_except(frozenset())
            self._closed = True
