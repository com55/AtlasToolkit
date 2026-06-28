"""Domain orchestration for atlas load, extract, and modify — no UI dependencies."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from atlas_toolkit.atlas.converter import auto_convert_atlas
from atlas_toolkit.core.document import AtlasDocument
from atlas_toolkit.atlas.extracter import AtlasProcessor
from atlas_toolkit.atlas.modifier import AtlasModifier, parse_atlas
from atlas_toolkit.atlas.repacker import repack_multi_page
from atlas_toolkit.core.overlay import overlay_rects_for_regions
from atlas_toolkit.core.region_ops import extract_raw_sprite

if TYPE_CHECKING:
    from PIL.Image import Image

log = logging.getLogger(__name__)


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
    """Result of merge/repack before bridge encodes image to base64."""

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
        self.modified_regions: set[str] = set()

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
        self.modified_regions = set()

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
                self.modified_regions = set()

            self.merged_image = None
            self.merged_atlas_text = None
            self.merged_pages = None
            self.pre_repack_image = None
            self.pre_repack_text = None

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

    def process_mod_image(
        self, path_str: str, selected_names: List[str], repack: bool = False
    ) -> Optional[ModifyResult]:
        if not self.modifier:
            return None

        if self.processor and len(self.processor.pages) > 1:
            return self._process_mod_multi_page(path_str, selected_names)

        try:
            mod_path = Path(path_str)
            log.debug("Processing mod image: %s", mod_path)

            merged_image, merged_atlas_text = self.modifier.merge_mod_image(
                mod_path, selected_names
            )

            self.pre_repack_image = merged_image
            self.pre_repack_text = merged_atlas_text

            if repack:
                log.debug("Running repack...")
                merged_image, merged_atlas_text = self.modifier.repack(
                    merged_image, merged_atlas_text
                )

            self.merged_image = merged_image
            self.merged_atlas_text = merged_atlas_text
            self.modified_regions.update(selected_names)
            self.modifier.adopt_merge_result(
                self.pre_repack_image, self.pre_repack_text
            )

            return self._build_modify_result(merged_image, merged_atlas_text)
        except Exception as e:
            log.error("Processing mod image: %s", e)
            return None

    def _extract_sprites_from_merged_pages(self) -> dict[str, Image]:
        if not self.merged_pages or not self.merged_atlas_text:
            return {}

        page_names = AtlasDocument.parse(self.merged_atlas_text).page_filenames()
        page_images = {
            name: self.merged_pages[i]
            for i, name in enumerate(page_names)
            if i < len(self.merged_pages)
        }

        _, _, regions = parse_atlas(self.merged_atlas_text)
        sprites: dict[str, Image] = {}
        for name, region in regions.items():
            page_img = page_images.get(region.page_filename)
            if page_img is not None:
                sprites[name] = extract_raw_sprite(page_img, region)
        return sprites

    def _process_mod_multi_page(
        self, path_str: str, selected_names: List[str]
    ) -> Optional[ModifyResult]:
        if not self.processor:
            return None

        try:
            from PIL import Image

            if self.merged_pages and self.merged_atlas_text:
                all_sprites = self._extract_sprites_from_merged_pages()
            else:
                all_sprites = {}
                for name in self.processor.regions:
                    sprite = self.processor.extract_region(name)
                    if sprite is not None:
                        all_sprites[name] = sprite

            mod_img = Image.open(Path(path_str)).convert("RGBA")
            for name in selected_names:
                if name in all_sprites:
                    all_sprites[name] = mod_img

            page_infos: list[dict[str, object]] = [
                {
                    "page": p.filename,
                    "format": p.format,
                    "filter": f"{p.filter[0]}, {p.filter[1]}",
                    "repeat": p.repeat,
                    "pma": p.pma,
                }
                for p in self.processor.pages
            ]
            region_metas: dict[str, dict[str, object]] = {
                name: r.to_meta_dict() for name, r in self.processor.regions.items()
            }

            pages, atlas_text = repack_multi_page(
                all_sprites, len(self.processor.pages), page_infos, region_metas
            )

            self.merged_pages = pages
            self.merged_atlas_text = atlas_text
            self.merged_image = None
            self.pre_repack_image = None
            self.pre_repack_text = None
            self.modified_regions.update(selected_names)

            _, _, merged_regions = parse_atlas(atlas_text)
            region_bounds: dict[str, list[int]] = {}
            region_pages: dict[str, str] = {}
            for name, region in merged_regions.items():
                region_bounds[name] = region.bounds_with_rotate()
                region_pages[name] = region.page_filename

            preview = pages[0] if pages else Image.new("RGBA", (1, 1))
            return ModifyResult(
                image=preview,
                atlas_text=atlas_text,
                regions=region_bounds,
                overlay_rects=overlay_rects_for_regions(merged_regions),
                modified_regions=sorted(self.modified_regions),
                extra={
                    "regionPages": region_pages,
                    "pages": [str(pi["page"]) for pi in page_infos],
                    "pageCount": len(pages),
                    "previewPage": str(page_infos[0]["page"]) if page_infos else None,
                },
            )
        except Exception as e:
            log.error("Processing multi-page mod image: %s", e)
            return None

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
        if not self.modifier or not self.pre_repack_image or not self.pre_repack_text:
            return None

        try:
            if repack:
                log.debug("Applying repack...")
                image, text = self.modifier.repack(
                    self.pre_repack_image, self.pre_repack_text
                )
            else:
                log.debug("Reverting to pre-repack merge result")
                image = self.pre_repack_image
                text = self.pre_repack_text

            self.merged_image = image
            self.merged_atlas_text = text
            self.modifier.adopt_merge_result(
                self.pre_repack_image, self.pre_repack_text
            )
            return self._build_modify_result(image, text)
        except Exception as e:
            log.error("toggle_repack: %s", e)
            return None
