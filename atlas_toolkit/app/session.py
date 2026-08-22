"""Domain orchestration for atlas load, extract, and modify — no UI dependencies."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from atlas_toolkit.atlas.converter import auto_convert_atlas
from atlas_toolkit.core.document import AtlasDocument, Page
from atlas_toolkit.atlas.extracter import AtlasProcessor
from atlas_toolkit.atlas.modifier import AtlasModifier, parse_atlas
from atlas_toolkit.atlas.repacker import repack_multi_page
from atlas_toolkit.core.overlay import overlay_rects_for_regions
from atlas_toolkit.core.region_ops import extract_raw_sprite

if TYPE_CHECKING:
    from PIL.Image import Image

log = logging.getLogger(__name__)

PreparedMod = Tuple["Image", int, int, bool, bool]


@dataclass
class ModBatch:
    """One mod apply: region names, source file, and optional prepared image cache."""

    names: tuple[str, ...]
    path: Path
    shared_canvas: bool = False
    prepared: Optional[PreparedMod] = None


@dataclass
class ModifyViewData:
    """Modify-mode view state before bridge encoding for JS."""

    image: Image
    regions: dict[str, list[int]]
    overlay_rects: dict[str, list[int]]
    pages: list[str]
    region_pages: dict[str, str]
    active_page: Optional[str]
    modified_regions: list[str]
    extra: dict[str, object] = field(default_factory=dict)


@dataclass
class ModifyResult:
    """Result of merge/repack before bridge encodes preview for JS."""

    image: Image
    atlas_text: str
    regions: dict[str, list[int]]
    overlay_rects: dict[str, list[int]]
    modified_regions: list[str]
    extra: dict[str, object] = field(default_factory=dict)


class AtlasSession:
    """In-memory atlas workflow: load → extract / modify → save."""

    def __init__(self) -> None:
        self.atlas_path: Optional[Path] = None
        self.processor: Optional[AtlasProcessor] = None
        self.modifier: Optional[AtlasModifier] = None
        self.merged_image: Optional[Image] = None
        self.merged_atlas_text: Optional[str] = None
        self.merged_pages: Optional[List[Image]] = None
        self.pre_repack_image: Optional[Image] = None
        self.pre_repack_text: Optional[str] = None
        self.pre_repack_pages: Optional[List[Image]] = None
        self.repacked_image: Optional[Image] = None
        self.repacked_text: Optional[str] = None
        self.repacked_pages: Optional[List[Image]] = None
        self.modded_sprites: dict[str, Image] = {}
        self.mod_batches: list[ModBatch] = []
        self.modifications_saved: bool = False

    @property
    def modified_regions(self) -> set[str]:
        return set(self.modded_sprites.keys())

    @modified_regions.setter
    def modified_regions(self, value: set[str]) -> None:
        if not value:
            self.modded_sprites.clear()
            self.mod_batches.clear()

    @property
    def is_loaded(self) -> bool:
        return self.processor is not None and self.atlas_path is not None

    @staticmethod
    def required_page_names(atlas_text: str) -> list[str]:
        return AtlasDocument.parse(atlas_text).page_filenames()

    def resolve_page_images(
        self, atlas_path: Path, atlas_text: str
    ) -> dict[str, Optional[Path]]:
        """Map each required page to an on-disk path next to the atlas, if present."""
        atlas_dir = atlas_path.parent
        return {
            name: (atlas_dir / name if (atlas_dir / name).exists() else None)
            for name in self.required_page_names(atlas_text)
        }

    def load(self, atlas_path: Path, page_images: Dict[str, Path]) -> None:
        content = atlas_path.read_text(encoding="utf-8")
        loader = {name: path for name, path in page_images.items()}
        self.atlas_path = atlas_path
        self.processor = AtlasProcessor(auto_convert_atlas(content), loader)
        self.clear_modify_state()

    def clear_modify_state(self) -> None:
        self.modifier = None
        self.merged_image = None
        self.merged_atlas_text = None
        self.merged_pages = None
        self.pre_repack_image = None
        self.pre_repack_text = None
        self.pre_repack_pages = None
        self.repacked_image = None
        self.repacked_text = None
        self.repacked_pages = None
        self.modded_sprites = {}
        self.mod_batches = []
        self.modifications_saved = False

    def get_region_names(self) -> List[str]:
        if not self.processor:
            return []
        return list(self.processor.regions.keys())

    def get_preview_image(self, names: List[str]) -> Optional[Image]:
        from PIL import Image

        if not self.processor or not names:
            return None

        try:
            images: List[Image] = []
            max_w, max_h = 0, 0
            valid_names = [n for n in names if n in self.processor.regions]

            for name in valid_names:
                img = self.processor.extract_region(name)
                if img:
                    images.append(img)
                    max_w = max(max_w, img.width)
                    max_h = max(max_h, img.height)

            if not images:
                return None
            if len(images) == 1:
                return images[0]

            monitor = Image.new("RGBA", (max_w, max_h), (0, 0, 0, 0))
            for img in reversed(images):
                layer = Image.new("RGBA", monitor.size, (0, 0, 0, 0))
                layer.paste(img, (0, 0))
                monitor = Image.alpha_composite(monitor, layer)
            return monitor
        except Exception as e:
            log.error("Preview error: %s", e)
            return None

    def extract_regions(
        self, region_names: Optional[List[str]]
    ) -> List[tuple[str, Image]]:
        from PIL import Image  # noqa: F401 — ensures Image in scope for type checkers

        if not self.processor:
            return []
        target = region_names if region_names else list(self.processor.regions.keys())
        out: List[tuple[str, Image]] = []
        for name in target:
            img = self.processor.extract_region(name)
            if img:
                out.append((name, img))
        return out

    def build_modify_view(self, *, clear_modified: bool = False) -> Optional[ModifyViewData]:
        if not self.processor or not self.atlas_path:
            return None

        try:
            atlas_text = self.atlas_path.read_text(encoding="utf-8")
            base_image = self.processor.get_page_image()
            if not base_image:
                log.error("No loaded images in processor")
                return None

            if clear_modified:
                self.modded_sprites = {}
                self.mod_batches = []
                self.modifications_saved = False

            self.merged_image = None
            self.merged_atlas_text = None
            self.merged_pages = None
            self.pre_repack_image = None
            self.pre_repack_text = None
            self.pre_repack_pages = None
            self.repacked_image = None
            self.repacked_text = None
            self.repacked_pages = None

            self.modifier = AtlasModifier(
                auto_convert_atlas(atlas_text), self.atlas_path, base_image
            )

            region_bounds: dict[str, list[int]] = {}
            for name, info in self.modifier.regions.items():
                region_bounds[name] = info.bounds_with_rotate()

            pages = [p.filename for p in self.processor.pages]
            region_pages = {
                name: r.page_filename for name, r in self.processor.regions.items()
            }

            return ModifyViewData(
                image=base_image,
                regions=region_bounds,
                overlay_rects=overlay_rects_for_regions(self.modifier.regions),
                pages=pages,
                region_pages=region_pages,
                active_page=pages[0] if pages else None,
                modified_regions=sorted(self.modified_regions),
            )
        except Exception as e:
            log.error("Building modify view: %s", e)
            return None

    def enter_modify_mode(self) -> Optional[ModifyViewData]:
        return self.build_modify_view(clear_modified=False)

    def reset_modify_mode(self) -> Optional[ModifyViewData]:
        return self.build_modify_view(clear_modified=True)

    def exit_modify_mode(self) -> None:
        self.clear_modify_state()

    def _is_multi_page(self) -> bool:
        return self.processor is not None and len(self.processor.pages) > 1

    def _full_canvas_regions(self) -> set[str]:
        regions: set[str] = set()
        for batch in self.mod_batches:
            if batch.shared_canvas:
                regions.update(batch.names)
        return regions

    def _invalidate_merge_cache(self) -> None:
        self.pre_repack_image = None
        self.pre_repack_text = None
        self.pre_repack_pages = None

    def _invalidate_repack_cache(self) -> None:
        self.repacked_image = None
        self.repacked_text = None
        self.repacked_pages = None

    def _register_mod_batch(
        self, mod_path: Path, selected_names: List[str]
    ) -> Optional[ModBatch]:
        """Record one mod apply and store prepared sprites for repack rebuild."""
        if not selected_names or not self.atlas_path:
            return None
        names = tuple(selected_names)
        batch = ModBatch(names=names, path=mod_path)

        if self._is_multi_page():
            page_images, atlas_text = self._get_original_page_images_and_text()
            doc = AtlasDocument.parse(atlas_text)
            selected_set = set(selected_names)
            for page in doc.pages:
                page_img = page_images.get(page.filename)
                if page_img is None:
                    continue
                page_selected = [
                    r.name for r in page.regions if r.name in selected_set
                ]
                if not page_selected:
                    continue
                page_text = AtlasDocument(pages=[page]).serialize()
                modifier = AtlasModifier(
                    page_text, self.atlas_path, page_img.copy()
                )
                prepared = modifier._prepare_mod_image(mod_path, page_selected)
                mod_img = prepared[0]
                if prepared[4]:  # is_full_canvas — feeds full_canvas_regions on repack
                    batch.shared_canvas = True
                for name in page_selected:
                    self.modded_sprites[name] = mod_img
            self.mod_batches.append(batch)
            self.modifications_saved = False
            return batch

        modifier = self._fresh_single_page_modifier()
        if modifier is None:
            return None
        prepared = modifier._prepare_mod_image(mod_path, selected_names)
        batch.prepared = prepared
        batch.shared_canvas = prepared[4]  # is_full_canvas — feeds full_canvas_regions on repack
        mod_img = prepared[0]
        for name in selected_names:
            self.modded_sprites[name] = mod_img
        self.mod_batches.append(batch)
        self.modifications_saved = False
        return batch

    def _rebuild_single_page_merge(self) -> tuple[Image, str]:
        modifier = self._fresh_single_page_modifier()
        if modifier is None:
            raise RuntimeError("No single-page modifier")
        for batch in self.mod_batches:
            if batch.prepared is not None:
                merged, text = modifier.merge_mod_image(
                    batch.path,
                    list(batch.names),
                    prepared_mod=batch.prepared,
                )
            else:
                merged, text = modifier.merge_mod_image(
                    batch.path, list(batch.names)
                )
            modifier.adopt_merge_result(merged, text)
        return modifier.base_image, modifier.atlas_text

    def _rebuild_single_page_repack(self) -> tuple[Image, str]:
        modifier = self._fresh_single_page_modifier()
        if modifier is None:
            raise RuntimeError("No single-page modifier")
        return modifier.repack_with_modded_sprites(
            self.modded_sprites,
            full_canvas_regions=self._full_canvas_regions(),
        )

    def _rebuild_multi_page_merge(self) -> tuple[List[Image], str]:
        if not self.processor or not self.atlas_path:
            return [], ""

        page_images, atlas_text = self._get_original_page_images_and_text()

        for batch in self.mod_batches:
            selected_set = set(batch.names)
            current_doc = AtlasDocument.parse(atlas_text)
            updated_pages: list[Page] = []
            updated_images: dict[str, Image] = {}

            for page in current_doc.pages:
                page_img = page_images.get(page.filename)
                if page_img is None:
                    updated_pages.append(page)
                    continue

                page_selected = [
                    r.name for r in page.regions if r.name in selected_set
                ]
                if not page_selected:
                    updated_pages.append(page)
                    updated_images[page.filename] = page_img.copy()
                    continue

                page_text = AtlasDocument(pages=[page]).serialize()
                modifier = AtlasModifier(
                    page_text, self.atlas_path, page_img.copy()
                )
                merged_img, merged_page_text = modifier.merge_mod_image(
                    batch.path, page_selected
                )
                updated_pages.append(
                    AtlasDocument.parse(merged_page_text).pages[0]
                )
                updated_images[page.filename] = merged_img

            atlas_text = AtlasDocument(pages=updated_pages).serialize()
            page_images = {
                p.filename: updated_images[p.filename]
                for p in updated_pages
                if p.filename in updated_images
            }

        ordered_images = [
            page_images[p.filename]
            for p in AtlasDocument.parse(atlas_text).pages
            if p.filename in page_images
        ]
        return ordered_images, atlas_text

    def _rebuild_multi_page_repack(self) -> tuple[List[Image], str]:
        if not self.processor or not self.atlas_path:
            return [], ""

        page_images, atlas_text = self._get_original_page_images_and_text()
        doc = AtlasDocument.parse(atlas_text)
        sprites: dict[str, Image] = {}

        for page in doc.pages:
            page_img = page_images.get(page.filename)
            if page_img is None:
                continue
            page_text = AtlasDocument(pages=[page]).serialize()
            _, _, regions = parse_atlas(page_text)
            for name, region in regions.items():
                sprites[name] = extract_raw_sprite(page_img, region)

        for name, sprite in self.modded_sprites.items():
            if name in sprites:
                sprites[name] = sprite

        _, _, regions = parse_atlas(atlas_text)
        region_metas = {
            name: region.to_meta_dict() for name, region in regions.items()
        }
        return repack_multi_page(
            sprites,
            len(self.processor.pages),
            self._page_infos_from_processor(),
            region_metas,
            full_canvas_regions=self._full_canvas_regions(),
        )

    def _apply_active_result(
        self, repack: bool, image: Image, text: str
    ) -> ModifyResult:
        if repack:
            self.repacked_image = image
            self.repacked_text = text
            self.merged_image = image
            self.merged_atlas_text = text
            self.merged_pages = None
            self.pre_repack_image = None
            self.pre_repack_text = None
            self.pre_repack_pages = None
        else:
            self.pre_repack_image = image
            self.pre_repack_text = text
            self.merged_image = image
            self.merged_atlas_text = text
            self.merged_pages = None
            self.repacked_image = None
            self.repacked_text = None
            self.repacked_pages = None
        return self._build_modify_result(image, text)

    def _apply_active_multi_result(
        self, repack: bool, pages: List[Image], text: str
    ) -> ModifyResult:
        if repack:
            self.repacked_pages = pages
            self.repacked_text = text
            self.merged_pages = pages
            self.merged_atlas_text = text
            self.merged_image = None
            self.pre_repack_pages = None
            self.pre_repack_text = None
            self.pre_repack_image = None
        else:
            self.pre_repack_pages = pages
            self.pre_repack_text = text
            self.merged_pages = pages
            self.merged_atlas_text = text
            self.merged_image = None
            self.repacked_pages = None
            self.repacked_text = None
            self.repacked_image = None
        return self._build_multi_page_modify_result(pages, text)

    def process_mod_image(
        self, path_str: str, selected_names: List[str], repack: bool = False
    ) -> Optional[ModifyResult]:
        if not self.modifier:
            return None

        prev_batches = list(self.mod_batches)
        prev_sprites = dict(self.modded_sprites)
        try:
            mod_path = Path(path_str)
            log.debug("Processing mod image: %s", mod_path)
            if self._register_mod_batch(mod_path, selected_names) is None:
                return None

            if self._is_multi_page():
                if repack:
                    self._invalidate_merge_cache()
                    pages, text = self._rebuild_multi_page_repack()
                else:
                    self._invalidate_repack_cache()
                    pages, text = self._rebuild_multi_page_merge()
                if not pages or not text:
                    raise RuntimeError("Multi-page rebuild produced empty result")
                return self._apply_active_multi_result(repack, pages, text)

            if repack:
                self._invalidate_merge_cache()
                image, text = self._rebuild_single_page_repack()
            else:
                self._invalidate_repack_cache()
                image, text = self._rebuild_single_page_merge()
            return self._apply_active_result(repack, image, text)
        except Exception as e:
            self.mod_batches = prev_batches
            self.modded_sprites = prev_sprites
            log.error("Processing mod image: %s", e)
            return None

    def _get_original_page_images_and_text(self) -> tuple[dict[str, Image], str]:
        """Always read page images and atlas text from the loaded original."""
        if not self.processor or not self.atlas_path:
            return {}, ""

        atlas_text = auto_convert_atlas(
            self.atlas_path.read_text(encoding="utf-8")
        )
        page_images: dict[str, Image] = {}
        for page in self.processor.pages:
            img = self.processor.get_page_image(page.filename)
            if img is not None:
                page_images[page.filename] = img
        return page_images, atlas_text

    def _fresh_single_page_modifier(self) -> Optional[AtlasModifier]:
        if not self.processor or not self.atlas_path:
            return None
        atlas_text = auto_convert_atlas(
            self.atlas_path.read_text(encoding="utf-8")
        )
        base_image = self.processor.get_page_image()
        if base_image is None:
            return None
        return AtlasModifier(atlas_text, self.atlas_path, base_image.copy())

    def _page_infos_from_processor(self) -> list[dict[str, object]]:
        if not self.processor:
            return []
        return [
            {
                "page": p.filename,
                "format": p.format,
                "filter": f"{p.filter[0]}, {p.filter[1]}",
                "repeat": p.repeat,
                "pma": p.pma,
            }
            for p in self.processor.pages
        ]

    def _get_multi_page_images_and_text(self) -> tuple[dict[str, Image], str]:
        return self._get_original_page_images_and_text()

    def _build_multi_page_modify_result(
        self, pages: List[Image], atlas_text: str
    ) -> ModifyResult:
        _, _, merged_regions = parse_atlas(atlas_text)
        region_bounds: dict[str, list[int]] = {}
        region_pages: dict[str, str] = {}
        for name, region in merged_regions.items():
            region_bounds[name] = region.bounds_with_rotate()
            region_pages[name] = region.page_filename

        page_names = AtlasDocument.parse(atlas_text).page_filenames()
        from PIL import Image

        preview = pages[0] if pages else Image.new("RGBA", (1, 1))
        return ModifyResult(
            image=preview,
            atlas_text=atlas_text,
            regions=region_bounds,
            overlay_rects=overlay_rects_for_regions(merged_regions),
            modified_regions=sorted(self.modified_regions),
            extra={
                "regionPages": region_pages,
                "pages": page_names,
                "pageCount": len(pages),
                "previewPage": page_names[0] if page_names else None,
            },
        )

    def _build_modify_result(
        self, image: Image, atlas_text: str, extra: Optional[dict[str, object]] = None
    ) -> ModifyResult:
        _, _, merged_regions = parse_atlas(atlas_text)
        region_bounds: dict[str, list[int]] = {}
        for name, region in merged_regions.items():
            region_bounds[name] = region.bounds_with_rotate()

        return ModifyResult(
            image=image,
            atlas_text=atlas_text,
            regions=region_bounds,
            overlay_rects=overlay_rects_for_regions(merged_regions),
            modified_regions=sorted(self.modified_regions),
            extra=extra or {},
        )

    def get_modify_page_image(self, index: int) -> Optional[Image]:
        try:
            if self.merged_pages is not None:
                if 0 <= index < len(self.merged_pages):
                    return self.merged_pages[index]
                return None
            if self.processor and 0 <= index < len(self.processor.pages):
                return self.processor.get_page_image(
                    self.processor.pages[index].filename
                )
            return None
        except Exception as e:
            log.error("get_modify_page_preview: %s", e)
            return None

    def has_pending_modifications(self) -> bool:
        """True when unsaved modify-mode changes would be lost on exit or reload."""
        return bool(self.mod_batches) and not self.modifications_saved

    def has_merged_output(self) -> bool:
        if self.merged_atlas_text is None:
            return False
        return self.merged_pages is not None or (
            self.modifier is not None and self.merged_image is not None
        )

    def save_merged_to(self, output_dir: Path) -> None:
        if not self.has_merged_output() or not self.atlas_path:
            raise RuntimeError("No merged data to save")

        if self.merged_pages is not None:
            self._save_multi_page(output_dir)
        elif self.modifier and self.merged_image and self.merged_atlas_text:
            self.modifier.save(output_dir, self.merged_image, self.merged_atlas_text)
        else:
            raise RuntimeError("No merged data to save")

        self.modifications_saved = True

    def _save_multi_page(self, output_dir: Path) -> None:
        if not self.processor or not self.atlas_path or self.merged_pages is None:
            return
        output_dir.mkdir(parents=True, exist_ok=True)

        for i, page_img in enumerate(self.merged_pages):
            if i < len(self.processor.pages):
                page_name = self.processor.pages[i].filename
            else:
                page_name = f"page{i}.png"
            page_img.save(output_dir / Path(page_name).name)

        if self.merged_atlas_text is not None:
            (output_dir / self.atlas_path.name).write_text(
                self.merged_atlas_text, encoding="utf-8"
            )

        skel_path = self.atlas_path.with_suffix(".skel")
        if skel_path.exists():
            shutil.copy(skel_path, output_dir / skel_path.name)

    def toggle_repack(self, repack: bool) -> Optional[ModifyResult]:
        if not self.mod_batches:
            return None

        try:
            if self._is_multi_page():
                return self._toggle_repack_multi_page(repack)

            if repack:
                if self.repacked_image is None or self.repacked_text is None:
                    log.debug("Lazy-building repacked result for toggle")
                    image, text = self._rebuild_single_page_repack()
                    self.repacked_image = image
                    self.repacked_text = text
                image, text = self.repacked_image, self.repacked_text
            else:
                if self.pre_repack_image is None or self.pre_repack_text is None:
                    log.debug("Lazy-building merge result for toggle")
                    image, text = self._rebuild_single_page_merge()
                    self.pre_repack_image = image
                    self.pre_repack_text = text
                image, text = self.pre_repack_image, self.pre_repack_text

            self.merged_image = image
            self.merged_atlas_text = text
            self.merged_pages = None
            return self._build_modify_result(image, text)
        except Exception as e:
            log.error("toggle_repack: %s", e)
            return None

    def _toggle_repack_multi_page(self, repack: bool) -> Optional[ModifyResult]:
        try:
            if repack:
                if self.repacked_pages is None or not self.repacked_text:
                    log.debug("Lazy-building multi-page repacked result for toggle")
                    pages, text = self._rebuild_multi_page_repack()
                    self.repacked_pages = pages
                    self.repacked_text = text
                pages, text = self.repacked_pages, self.repacked_text
            else:
                if self.pre_repack_pages is None or not self.pre_repack_text:
                    log.debug("Lazy-building multi-page merge result for toggle")
                    pages, text = self._rebuild_multi_page_merge()
                    self.pre_repack_pages = pages
                    self.pre_repack_text = text
                pages, text = self.pre_repack_pages, self.pre_repack_text

            self.merged_pages = pages
            self.merged_atlas_text = text
            self.merged_image = None
            return self._build_multi_page_modify_result(pages, text)
        except Exception as e:
            log.error("toggle_repack multi-page: %s", e)
            return None
