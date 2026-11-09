"""State and View Mode operations driven by the application client."""
from io import BytesIO

from PIL import Image

from atlas_toolkit.api import AtlasError, SpineAtlas, _file_name, _page_filenames


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
        self._pristine_regions = []
        self._page_names = []
        self._all_pma = False

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
            "hasPending": atlas.has_changes,
            "allPma": self._all_pma,
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
        self._page_names = page_names
        self._all_pma = all_pma
        self._atlas_name = atlas_name
        self._skeleton_name = skeleton_name
        self._pristine_regions = [{"key": view.key, "label": view.label} for view in atlas.region_views()]
        return self._state()

    def set_skeleton(self, name: str, data: bytes) -> dict:
        self._require_atlas().set_skeleton(data)
        self._skeleton_name = name
        return self._state()

    def preview(
        self, names: list[str], mesh_crop: bool, force_resize: bool,
        want_hulls: bool, alpha: str,
    ) -> dict:
        atlas = self._require_atlas()
        if not names:
            raise ValueError("preview needs at least one region name")
        image = atlas.extract_composite(
            names, force_resize=force_resize,
            mesh_crop=mesh_crop and atlas.mesh_unavailable_reason is None,
            alpha_mode=self._alpha_mode(alpha),
        )
        return {
            "width": image.width, "height": image.height, "rgba": image.tobytes(),
            "hulls": atlas.mesh_hull_layout(names, force_resize) if want_hulls else None,
        }

    def extract(self, keys: list[str] | None, mesh_crop: bool, alpha: str) -> list[dict]:
        atlas = self._require_atlas()
        views = atlas.region_views()
        labels = {view.key: view.label for view in views}
        wanted = [view.key for view in views] if keys is None else keys
        names = [labels[key] for key in wanted]
        images = atlas.extract(
            names, mesh_crop=mesh_crop and atlas.mesh_unavailable_reason is None,
            alpha_mode=self._alpha_mode(alpha),
        )
        return [{"filename": _file_name(key), "png": _png(images[name])} for key, name in zip(wanted, names)]

    def save_image(self, names, mesh_crop, force_resize, alpha) -> bytes:
        atlas = self._require_atlas()
        image = atlas.extract_composite(
            names, force_resize=force_resize,
            mesh_crop=mesh_crop and atlas.mesh_unavailable_reason is None,
            alpha_mode=self._alpha_mode(alpha),
        )
        return _png(image)
