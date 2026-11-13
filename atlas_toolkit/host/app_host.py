# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""State and View Mode operations driven by the application client."""
from io import BytesIO

from PIL import Image

from atlas_toolkit.api import (
    AtlasError, InvalidRegionName, PackerOptions, RegionNotFound, SpineAtlas,
    _file_name, _from_page, _page_filenames,
)
from atlas_toolkit.alpha_mode import parse_alpha_mode
from atlas_toolkit.document import png_names_for_save


def _png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class AppHost:
    """Own the currently loaded atlas and expose byte-based View operations."""

    def __init__(self):
        self._atlas = None
        self._atlas_name = None
        self._skeleton_name = None
        self._skeleton = None
        self._pristine_regions = []
        self._page_names = []
        self._all_pma = False
        self._packed = None
        self._revision = 0
        self._saved_revision = -1
        self._pack_settings = {
            "packer": "shelf",
            "padding": 2,
            "meshAware": True,
            "splitComposite": False,
            "keepLayout": True,
            "alpha": "straight",
            "bleed": True,
        }

    def _settings(self) -> dict:
        settings = dict(self._pack_settings)
        usable_mesh = self._require_atlas().mesh_unavailable_reason is None
        requested = settings["packer"]
        fallback = requested.startswith("polygons") and not usable_mesh
        if fallback:
            settings["packer"] = requested.replace("polygons", "rectangles")
        polygon = settings["packer"].startswith("polygons")
        in_place = settings["packer"].endswith("_in_place")
        settings.update(
            meshAware=True if polygon else usable_mesh and settings["meshAware"],
            meshAwareLocked=polygon or not usable_mesh,
            splitComposite=True if in_place else settings["splitComposite"],
            splitCompositeLocked=in_place,
            keepLayout=True if in_place else settings["keepLayout"],
            keepLayoutLocked=in_place,
            polygonsAllowed=usable_mesh,
            alphaVisible=self._all_pma,
            bleedLocked=self._all_pma,  # bleed never touches pma pages
            fallbackFrom=requested if fallback else None,
        )
        return settings

    def _pack_arguments(self) -> dict:
        settings = self._settings()
        return {
            "packer": settings["packer"],
            "packer_options": PackerOptions(
                padding=settings["padding"],
                mesh_aware=None if settings["packer"].startswith("polygons") else settings["meshAware"],
                repack=None if settings["keepLayout"] else "always",
                bleed=settings["bleed"],
            ),
            "split_composite": settings["splitComposite"],
        }

    @staticmethod
    def _validate_settings(settings: dict) -> None:
        if not isinstance(settings, dict):
            raise ValueError("settings must be a dict")
        for key, value in settings.items():
            if key == "packer":
                valid = isinstance(value, str) and value in (
                    "shelf", "rectangles", "polygons", "rectangles_in_place", "polygons_in_place",
                )
            elif key == "padding":
                valid = type(value) is int and 1 <= value <= 256
            elif key in ("meshAware", "splitComposite", "keepLayout", "bleed"):
                valid = type(value) is bool
            elif key == "alpha":
                valid = isinstance(value, str) and value in ("straight", "premultiplied")
            else:
                valid = False
            if not valid:
                raise ValueError(f"invalid setting: {key}")

    def settings_need_pack(self, settings: dict) -> bool:
        """True when set_settings(settings) would pack: Edit Mode with edits
        and no cached pack for the new settings. Invalid settings answer True
        so that set_settings reports them. Never changes anything."""
        try:
            self._validate_settings(settings)
        except ValueError:
            return True
        atlas = self._atlas
        if atlas is None or self._packed is None:
            return False
        previous = self._pack_settings
        self._pack_settings = {**previous, **settings}
        try:
            return not atlas.pack_is_cached(**self._pack_arguments())
        except Exception:  # e.g. a missing skeleton: set_settings reports it
            return True
        finally:
            self._pack_settings = previous

    def set_settings(self, settings: dict) -> dict | None:
        """Store the requested settings and repack in Edit Mode. When the
        packed output is the very same as before (no edits, a setting the
        pack does not use), returns {"state", "unchanged": True} without
        page pixels."""
        self._validate_settings(settings)
        atlas = self._atlas
        previous = self._pack_settings
        self._pack_settings = {**previous, **settings}
        if atlas is None:
            return None
        try:
            if self._packed is None:
                return self._state()
            with atlas.transaction():
                packed = atlas.pack(**self._pack_arguments())
        except BaseException:
            self._pack_settings = previous
            raise
        old_text, old_pages = self._packed
        unchanged = packed[0] == old_text and packed[1].keys() == old_pages.keys() and all(
            packed[1][name] is old_pages[name] for name in old_pages
        )
        if packed[0] is not old_text:
            self._revision += 1
        self._packed = packed
        return {"state": self._state(), "unchanged": True} if unchanged else self._edit_result()

    @staticmethod
    def required_pages(atlas_text: str) -> list[str]:
        return _page_filenames(atlas_text)

    def _require_atlas(self) -> SpineAtlas:
        if self._atlas is None:
            raise AtlasError("no atlas loaded")
        return self._atlas

    def _alpha_mode(self, alpha: str) -> str | None:
        if alpha not in ("straight", "premultiplied"):
            raise ValueError("alpha must be 'straight' or 'premultiplied'")
        return alpha if self._all_pma else None

    def has_pending(self) -> bool:
        return (
            self._atlas is not None
            and self._atlas.has_changes
            and self._revision != self._saved_revision
        )

    def _state(self) -> dict:
        atlas = self._require_atlas()
        views = atlas.region_views()
        pages = list(self._page_names)
        reason = atlas.mesh_unavailable_reason
        return {
            "atlasName": self._atlas_name,
            "pages": pages,
            "isMultiPage": len(pages) > 1,
            "regions": [{"key": view.key, "label": view.label} for view in views],
            "pristineRegions": [dict(region) for region in self._pristine_regions],
            "regionPages": {view.key: view.page for view in views},
            "modifiedKeys": [view.key for view in views if view.modified],
            "hasChanges": atlas.has_changes,
            "hasPending": self.has_pending(),
            "allPma": self._all_pma,
            "settings": self._settings(),
            "mesh": {
                "available": reason is None,
                "reason": None if reason == "none" else reason,
                "skelName": self._skeleton_name,
            },
        }

    def load(
        self, atlas_name: str, atlas_text: str, pages: dict[str, bytes],
        skeleton_name: str | None, skeleton: bytes | None,
    ) -> dict:
        images = {name: Image.open(BytesIO(data)).convert("RGBA") for name, data in pages.items()}
        atlas = SpineAtlas.load(atlas_text, images, skeleton=skeleton)
        page_names = self.required_pages(atlas_text)
        all_pma = all(atlas.page_is_pma(page) for page in page_names)
        self._atlas = atlas
        self._packed = None
        self._revision += 1
        self._page_names = page_names
        self._all_pma = all_pma
        self._atlas_name = atlas_name
        self._skeleton_name = skeleton_name
        self._skeleton = skeleton
        self._pristine_regions = [{"key": view.key, "label": view.label} for view in atlas.region_views()]
        return self._state()

    def set_skeleton(self, name: str, data: bytes) -> dict:
        atlas = self._require_atlas()
        previous = self._skeleton
        if self._packed is not None:
            try:
                with atlas.transaction():
                    atlas.set_skeleton(data)
                    packed = atlas.pack(**self._pack_arguments())
            except BaseException:
                atlas.set_skeleton(previous)
                raise
            if packed[0] is not self._packed[0]:
                self._revision += 1
            self._packed = packed
        else:
            atlas.set_skeleton(data)
        self._skeleton = data
        self._skeleton_name = name
        return self._state() if self._packed is None else self._edit_result()

    def preview(
        self, names: list[str], mesh_crop: bool, force_resize: bool,
        want_hulls: bool, alpha: str,
    ) -> dict | None:
        atlas = self._require_atlas()
        if not names:
            raise ValueError("preview needs at least one region name")
        labels = self._labels(names)
        image = atlas.extract_composite(
            labels, force_resize=force_resize,
            mesh_crop=mesh_crop and atlas.mesh_unavailable_reason is None,
            alpha_mode=self._alpha_mode(alpha),
        )
        if image.width == 0 or image.height == 0:
            return None
        return {
            "width": image.width, "height": image.height, "rgba": image.tobytes(),
            "hulls": atlas.mesh_hull_layout(labels, force_resize) if want_hulls else None,
        }

    def extract(self, keys: list[str] | None, mesh_crop: bool, alpha: str) -> list[dict]:
        atlas = self._require_atlas()
        views = atlas.region_views()
        wanted = [view.key for view in views] if keys is None else keys
        names = self._labels(wanted)
        images = atlas.extract(
            names, mesh_crop=mesh_crop and atlas.mesh_unavailable_reason is None,
            alpha_mode=self._alpha_mode(alpha),
        )
        return [
            {"filename": _file_name(key), "png": _png(images[name])}
            for key, name in zip(wanted, names)
            if images[name].width > 0 and images[name].height > 0
        ]

    def save_image(self, names, mesh_crop, force_resize, alpha) -> bytes | None:
        atlas = self._require_atlas()
        image = atlas.extract_composite(
            self._labels(names), force_resize=force_resize,
            mesh_crop=mesh_crop and atlas.mesh_unavailable_reason is None,
            alpha_mode=self._alpha_mode(alpha),
        )
        if image.width == 0 or image.height == 0:
            return None
        return _png(image)

    def _labels(self, keys: list[str]) -> list[str]:
        labels = {view.key: view.label for view in self._require_atlas().region_views()}
        for key in keys:
            if key not in labels:
                raise RegionNotFound(key)
        return [labels[key] for key in keys]

    def _require_edit(self) -> SpineAtlas:
        if self._packed is None:
            raise AtlasError("not in edit mode")
        return self._require_atlas()

    @staticmethod
    def _raster(image: Image.Image) -> dict:
        return {"width": image.width, "height": image.height, "rgba": image.tobytes()}

    def _edit_result(self) -> dict:
        atlas = self._require_edit()
        text, pages = self._packed
        views = atlas.region_views(text, {name: image.size for name, image in pages.items()})
        names = self.required_pages(text)
        return {
            "image": self._raster(pages[names[0]]),
            "regions": {v.key: [v.x, v.y, v.w, v.h, v.rotate] for v in views},
            "pages": names,
            "regionPages": {v.key: v.page for v in views},
            "activePage": names[0],
            "state": self._state(),
        }

    def _edit(self, operation) -> dict:
        atlas = self._require_edit()
        with atlas.transaction():
            operation(atlas)
            packed = atlas.pack(**self._pack_arguments())
        self._packed = packed
        self._revision += 1
        return self._edit_result()

    def enter_edit(self) -> dict:
        atlas = self._require_atlas()
        with atlas.transaction():
            atlas.reset()
            packed = atlas.pack(**self._pack_arguments())
        self._packed = packed
        self._revision += 1
        return self._edit_result()

    def exit_edit(self) -> dict:
        self._require_edit().reset()
        self._packed = None
        self._revision += 1
        return self._state()

    def _page(self, index: int) -> tuple[str, Image.Image]:
        self._require_edit()
        text, pages = self._packed
        names = self.required_pages(text)
        if index < 0 or index >= len(names):
            raise IndexError(index)
        name = names[index]
        return name, pages[name]

    def page_preview(self, index: int) -> dict:
        name, image = self._page(index)
        return {"image": self._raster(image), "activePage": name, "activeIndex": index}

    def page_png(self, index: int, alpha: str) -> bytes:
        _, image = self._page(index)
        mode = parse_alpha_mode(self._alpha_mode(alpha))
        return _png(_from_page(image, mode, self._all_pma))

    def modify(self, keys: list[str], image: bytes, alpha: str) -> dict:
        self._require_edit()
        if not keys:
            raise ValueError("modify needs at least one region key")
        def operation(atlas):
            labels = self._labels(keys)
            picture = Image.open(BytesIO(image)).convert("RGBA")
            mode = self._alpha_mode(alpha)
            if len(labels) == 1:
                atlas.replace({labels[0]: picture}, alpha_mode=mode)
            else:
                atlas.replace_composite(labels, picture, alpha_mode=mode)
        return self._edit(operation)

    def add(self, name: str, image: bytes, page: str | None, alpha: str) -> dict:
        def operation(atlas):
            picture = Image.open(BytesIO(image)).convert("RGBA")
            atlas.add_region(name, picture, page=page, alpha_mode=self._alpha_mode(alpha))
        return self._edit(operation)

    def rename(self, key: str, new_name: str) -> dict:
        return self._edit(lambda atlas: atlas.rename_region(self._labels([key])[0], new_name))

    def remove(self, keys: list[str]) -> dict:
        self._require_edit()
        if not keys:
            raise ValueError("remove needs at least one region key")
        return self._edit(lambda atlas: atlas.remove_region(self._labels(keys)))

    def reset_edits(self) -> dict:
        return self._edit(lambda atlas: atlas.reset())

    def validate_name(self, name: str, exclude_key: str | None) -> dict:
        atlas = self._require_edit()
        exclude = self._labels([exclude_key])[0] if exclude_key is not None else None
        try:
            value = atlas.validate_region_name(name, exclude=exclude)
        except InvalidRegionName as error:
            return {"ok": False, "reason": error.reason}
        return {"ok": True, "value": value}

    def save_output(self) -> dict:
        self._require_edit()
        text, pages = self._packed
        names = png_names_for_save(text, len(pages), self._atlas_name)
        for name in names:
            if ("/" in name or "\\" in name or name == ".."
                    or (len(name) >= 2 and name[0].isalpha() and name[1] == ":")):
                raise AtlasError(f"unsafe page file name: {name}")
        page_order = self.required_pages(text)
        result = {
            "text": text,
            "revision": self._revision,
            "pages": [{"name": name, "png": _png(pages[page])}
                      for name, page in zip(names, page_order)],
        }
        return result

    def mark_saved(self, revision: int | None = None) -> dict:
        self._require_edit()
        if revision is None or revision == self._revision:
            self._saved_revision = self._revision
        return self._state()
