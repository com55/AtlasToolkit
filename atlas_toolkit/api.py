"""Public library API: SpineAtlas, a PIL-in/PIL-out atlas object.

Edits are recorded on a deferred AtlasSession and packed once by build();
see docs/superpowers/specs/2026-10-11-python-stateless-api-design.md.
"""
from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from PIL import Image

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.domain.converter import auto_convert_atlas
from atlas_toolkit.domain.packing import normalize_gap
from atlas_toolkit.domain.nest_frontier import CanvasTooLargeError
from atlas_toolkit.engine.session import AtlasSession, RegionNameError, RemoveAllRegionsError
from atlas_toolkit.mesh.lookup import build_mesh_lookup
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

__all__ = [
    "SpineAtlas", "Region", "AtlasError", "RegionNotFound", "DuplicateRegionName",
    "InvalidRegionName", "MultiPageNotSupported", "MissingPageImage", "SkeletonUnavailable",
    "CanvasTooLarge",
]

ImageLike = Union[Image.Image, str, os.PathLike]


class AtlasError(Exception):
    """Base class for every SpineAtlas error."""


class RegionNotFound(AtlasError, KeyError):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    def __str__(self) -> str:
        return f"no region named {self.name!r}"


class DuplicateRegionName(AtlasError, ValueError):
    def __init__(self, names: list[str]) -> None:
        super().__init__(f"atlas has duplicate region names: {', '.join(names)}")
        self.names = names


class InvalidRegionName(AtlasError, ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class MultiPageNotSupported(AtlasError, NotImplementedError):
    pass


class MissingPageImage(AtlasError):
    def __init__(self, pages: list[str], message: str | None = None) -> None:
        super().__init__(message or f"missing page images: {', '.join(pages)}")
        self.pages = pages


class SkeletonUnavailable(AtlasError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"mesh data unavailable: {reason}")
        self.reason = reason


class CanvasTooLarge(AtlasError, ValueError):
    """The fast nest packer (nest + mesh_aware) would need a canvas side
    over its limit. build(..., fast_nest=False) uses the legacy packer,
    which has no limit."""

    def __init__(self, width: int, height: int, limit: int) -> None:
        super().__init__(
            f"packing needs a {width}x{height} canvas, over the {limit} px per side limit; "
            "build with fast_nest=False to use the legacy packer, which has no limit"
        )
        self.width = width
        self.height = height
        self.limit = limit


@dataclass(frozen=True)
class Region:
    name: str
    original_name: str | None
    page: str
    bounds: tuple[int, int, int, int] | None
    rotate: int
    offsets: tuple[int, int, int, int] | None

    @property
    def renamed(self) -> bool:
        return self.original_name is not None and self.name != self.original_name

    @property
    def added(self) -> bool:
        return self.original_name is None


def _parse_skeleton(data: bytes) -> dict:
    from skel_mesh_parser import parse_skeleton

    return parse_skeleton(data)


def _mesh_state(skeleton: bytes | None) -> tuple[str | None, dict | None]:
    """(unavailable reason, lookup) -- the app's reasons (atlas-api.js)."""
    if skeleton is None:
        return "none", None
    from skel_mesh_parser import UnsupportedVersionError

    try:
        lookup = build_mesh_lookup(_parse_skeleton(skeleton))
    except UnsupportedVersionError:
        return "unsupported-version", None
    except Exception:
        return "parse-error", None
    if not lookup:
        return "no-mesh-attachments", None
    return None, lookup


def _open_image(value: object) -> Image.Image:
    """A PIL image as given (the backend copies it), or a path read as RGBA."""
    if isinstance(value, Image.Image):
        return value
    if isinstance(value, (str, os.PathLike)):
        with Image.open(value) as img:
            return img.convert("RGBA")
    raise TypeError(f"expected a PIL.Image or a path, got {type(value).__name__}")


def _edit_image(value: object) -> Image.Image:
    """An edit image, decoded and copied now: a lazily loaded or broken file
    fails here, before anything is recorded, and later changes to the
    caller's object never reach the edit."""
    image = _open_image(value).convert("RGBA")
    if image.width == 0 or image.height == 0:
        raise AtlasError("image has zero width or height")
    return image


def _skeleton_bytes(value: object) -> bytes | None:
    if value is None or isinstance(value, bytes):
        return value
    if isinstance(value, (str, os.PathLike)):
        return Path(value).read_bytes()
    raise TypeError(f"expected bytes or a path for skeleton, got {type(value).__name__}")


def _file_name(name: str) -> str:
    r"""The app's extract file name (atlas-api.js extract_files), except that
    \w here is Unicode: non-ASCII letters are kept (頭 -> 頭.png) where the
    app's ASCII \w maps them to '_'. Deliberate divergence -- with ASCII \w
    every CJK-named atlas would collide on _.png."""
    return re.sub(r"[^\w.\- ]", "_", name) + ".png"


def _page_filenames(atlas_text: str) -> list[str]:
    return list(dict.fromkeys(AtlasDocument.parse(auto_convert_atlas(atlas_text)).page_filenames()))


def _page_mapping(filenames: list[str], images: object, fallback_dir: Path | None) -> dict[str, Image.Image]:
    if not filenames:
        raise AtlasError("atlas text declares no pages")
    if isinstance(images, (Image.Image, str, os.PathLike)):
        if len(filenames) != 1:
            raise MissingPageImage(
                filenames, "a single image needs a one-page atlas; pass {page name: image}",
            )
        return {filenames[0]: _open_image(images)}
    if images is not None and not isinstance(images, Mapping):
        raise TypeError(f"expected an image or a mapping of page images, got {type(images).__name__}")
    given = dict(images or {})
    pages: dict[str, Image.Image] = {}
    missing: list[str] = []
    for filename in filenames:
        if filename in given:
            pages[filename] = _open_image(given[filename])
        elif fallback_dir is not None and (fallback_dir / filename).is_file():
            pages[filename] = _open_image(fallback_dir / filename)
        else:
            missing.append(filename)
    if missing:
        raise MissingPageImage(missing)
    return pages


class SpineAtlas:
    """One Spine atlas: its text, page images and pending edits."""

    def __init__(self, *args, **kwargs) -> None:
        raise TypeError("use SpineAtlas.open() or SpineAtlas.load()")

    @classmethod
    def open(
        cls, atlas_path: str | os.PathLike, *, images: object = None, skeleton: object = None,
    ) -> "SpineAtlas":
        path = Path(atlas_path)
        text = path.read_text(encoding="utf-8-sig")
        pages = _page_mapping(_page_filenames(text), images, path.parent)
        if skeleton is None:
            sibling = path.with_suffix(".skel")
            skel = sibling.read_bytes() if sibling.is_file() else None
        else:
            skel = _skeleton_bytes(skeleton)
        return cls._create(text, pages, skel, None)

    @classmethod
    def load(cls, atlas_text: str, pages: object, *, skeleton: object = None) -> "SpineAtlas":
        return cls._create(
            atlas_text, _page_mapping(_page_filenames(atlas_text), pages, None),
            _skeleton_bytes(skeleton), None,
        )

    @classmethod
    def _create(
        cls, atlas_text: str, pages: dict[str, Image.Image], skeleton: bytes | None,
        allowed: frozenset[str] | None,
    ) -> "SpineAtlas":
        document = AtlasDocument.parse(auto_convert_atlas(atlas_text))
        counts = Counter(
            region.atlas_name or region.name for page in document.pages for region in page.regions
        )
        duplicates = sorted(name for name, count in counts.items() if count > 1)
        if duplicates:
            raise DuplicateRegionName(duplicates)
        self = object.__new__(cls)
        self._text = atlas_text
        self._document = document
        self._pristine = document.regions_by_key()
        self._pristine_keys = frozenset(self._pristine)
        self._session = AtlasSession.load_images(
            atlas_text, pages, PillowRasterBackend(), defer_repack=True,
        )
        self._skeleton = skeleton
        self._allowed = allowed
        self._mesh_reason, lookup = _mesh_state(skeleton)
        if lookup is not None:
            self._session.set_mesh_data(
                {name: mesh for name, mesh in lookup.items() if allowed is None or name in allowed}
            )
        self._build_cache = None
        self._refresh_names()
        return self

    # -- names -------------------------------------------------------------

    def _refresh_names(self) -> None:
        self._labels = self._session.region_labels()
        self._keys = {label: key for key, label in self._labels}

    def _key(self, name: str) -> str:
        try:
            return self._keys[name]
        except (KeyError, TypeError):
            raise RegionNotFound(name) from None

    def _built_key(self, name: str) -> str:
        """Key of a region that exists in this atlas's pages (not a pending add)."""
        key = self._key(name)
        if key not in self._pristine_keys:
            raise AtlasError(f"{name!r} was added since this atlas was built; call build() first")
        return key

    # -- data --------------------------------------------------------------

    @property
    def atlas_text(self) -> str:
        return self._text

    @property
    def pages(self) -> dict[str, Image.Image]:
        return self._session.export_pages()

    @property
    def has_changes(self) -> bool:
        return self._session.has_pending_edits

    @property
    def regions(self) -> tuple[Region, ...]:
        default_page = self._document.pages[0].filename
        out = []
        for key, label in self._labels:
            region = self._pristine.get(key)
            if region is None:
                out.append(Region(label, None, default_page, None, 0, None))
            else:
                out.append(Region(
                    label, region.atlas_name or region.name, region.page_filename,
                    (region.x, region.y, region.w, region.h), region.rotate,
                    tuple(region.offsets) if region.offsets else None,
                ))
        return tuple(out)

    def region(self, name: str) -> Region:
        key = self._key(name)
        index = [k for k, _ in self._labels].index(key)
        return self.regions[index]

    # -- mesh --------------------------------------------------------------

    def _require_mesh(self) -> None:
        if self._mesh_reason is not None:
            raise SkeletonUnavailable(self._mesh_reason)

    @contextmanager
    def _mesh_crop(self, enabled: bool):
        if not enabled:
            yield
            return
        self._require_mesh()
        self._session.set_mesh_mask_enabled(True)
        try:
            yield
        finally:
            self._session.set_mesh_mask_enabled(False)

    # -- extract -----------------------------------------------------------

    def extract(self, names: str | list[str] | None = None, *, mesh_crop: bool = False):
        if names is None:
            raise TypeError("extract() needs a name or a list of names; use extract_all() for every region")
        single = isinstance(names, str)
        wanted = [names] if single else list(names)
        keys = [self._built_key(name) for name in wanted]
        with self._mesh_crop(mesh_crop):
            images = self._session.extract_images(keys)
        out = {name: images[key] for name, key in zip(wanted, keys)}
        return out[names] if single else out

    def extract_all(self, *, mesh_crop: bool = False) -> dict[str, Image.Image]:
        names = [label for key, label in self._labels if key in self._pristine_keys]
        return self.extract(names, mesh_crop=mesh_crop)

    def extract_composite(
        self, names: str | list[str], *, force_resize: bool = False, mesh_crop: bool = False,
    ) -> Image.Image:
        wanted = [names] if isinstance(names, str) else list(names)
        if not wanted:
            raise TypeError("extract_composite() needs at least one region name")
        keys = [self._built_key(name) for name in wanted]
        with self._mesh_crop(mesh_crop):
            return self._session.get_preview_image(keys, force_resize)

    def extract_to(self, names: str | list[str], path: str | os.PathLike, *, mesh_crop: bool = False) -> None:
        if isinstance(names, str):
            image = self.extract(names, mesh_crop=mesh_crop)
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, format="PNG")
            return
        self._write_many(list(names), Path(path), mesh_crop)

    def extract_all_to(self, directory: str | os.PathLike, *, mesh_crop: bool = False) -> None:
        names = [label for key, label in self._labels if key in self._pristine_keys]
        self._write_many(names, Path(directory), mesh_crop)

    def extract_composite_to(
        self, names: str | list[str], path: str | os.PathLike, *,
        force_resize: bool = False, mesh_crop: bool = False,
    ) -> None:
        image = self.extract_composite(names, force_resize=force_resize, mesh_crop=mesh_crop)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="PNG")

    def _write_many(self, names: list[str], directory: Path, mesh_crop: bool) -> None:
        files = [_file_name(name) for name in names]
        clashes = sorted(name for name, count in Counter(os.path.normcase(f) for f in files).items() if count > 1)
        if clashes:
            raise AtlasError(f"region names map to the same file name: {', '.join(clashes)}")
        images = self.extract(names, mesh_crop=mesh_crop)
        directory.mkdir(parents=True, exist_ok=True)
        for name, file_name in zip(names, files):
            images[name].save(directory / file_name, format="PNG")

    # -- edits -------------------------------------------------------------

    def _require_editable(self) -> None:
        if len(self._document.pages) > 1:
            raise MultiPageNotSupported("editing multi-page atlases is not supported")

    def _edited(self) -> None:
        self._build_cache = None
        self._refresh_names()

    def replace(self, images: Mapping[str, ImageLike]) -> None:
        """Replace each named region's pixels. Every name and image is checked
        before anything is recorded, so a failing call records nothing."""
        self._require_editable()
        if not isinstance(images, Mapping):
            raise TypeError(f"expected a mapping of region name to image, got {type(images).__name__}")
        entries = [(self._built_key(name), _edit_image(image)) for name, image in images.items()]
        if not entries:
            return
        try:
            for key, image in entries:
                self._session.apply_mod_image(image, [key])
        finally:
            self._edited()

    def replace_composite(self, names: list[str], image: ImageLike) -> None:
        """Replace several regions with one image. Each region's whole canvas
        becomes the image at the image's own size (offsets are dropped), as in
        the app; the regions share that one sprite."""
        self._require_editable()
        keys = [self._built_key(name) for name in ([names] if isinstance(names, str) else names)]
        picture = _edit_image(image)
        if not keys:
            return
        self._session.apply_mod_image(picture, keys)
        self._edited()

    def add_region(self, name: str, image: ImageLike) -> None:
        self._require_editable()
        picture = _edit_image(image)
        try:
            self._session.add_region(picture, name)
        except RegionNameError as error:
            raise InvalidRegionName(error.reason) from None
        self._edited()

    def rename_region(self, name: str, new_name: str) -> None:
        self._require_editable()
        key = self._key(name)
        try:
            self._session.rename_region(key, new_name)
        except RegionNameError as error:
            raise InvalidRegionName(error.reason) from None
        self._edited()

    def remove_region(self, names: str | list[str]) -> None:
        self._require_editable()
        keys = [self._key(name) for name in ([names] if isinstance(names, str) else names)]
        if not keys:
            return
        try:
            self._session.remove_regions(keys)
        except RemoveAllRegionsError as error:
            raise AtlasError(str(error)) from None
        self._edited()

    # -- result ------------------------------------------------------------

    def _child_allowed(self) -> frozenset[str]:
        """Names the built atlas may look up in the skeleton: pristine regions
        whose current name is still their own (spec §3, mesh across renames)."""
        return frozenset(
            label for key, label in self._labels
            if key in self._pristine_keys and label == key
            and (self._allowed is None or key in self._allowed)
        )

    def _pack(self, nest: bool, gap: int | None, mesh_aware: bool, fast: bool | None) -> tuple[str, dict]:
        session = self._session
        try:
            session.set_fast_mesh_nest_enabled(fast is not False)
            session.set_nest_regions_enabled(nest)
            if nest:
                session.set_nest_gap_distance(gap)
            session.set_mesh_aware_repack_enabled(mesh_aware)
            return session.pack_pending()
        except CanvasTooLargeError as error:
            raise CanvasTooLarge(error.width, error.height, error.limit) from None
        finally:
            session.set_nest_regions_enabled(False)
            session.set_nest_gap_distance(4)
            session.set_mesh_aware_repack_enabled(False)
            session.set_fast_mesh_nest_enabled(True)

    def build(
        self, *, nest: bool = False, gap: int = 4, mesh_aware: bool = False, fast_nest: bool = True,
    ) -> "SpineAtlas":
        """Pack the recorded edits into a new SpineAtlas; this one is unchanged.

        `fast_nest` matters only when `nest` and `mesh_aware` are both on: True
        (default) uses the fast packer, whose layouts differ from the app's
        and whose canvas is capped at 10000 px per side (CanvasTooLarge);
        False uses the legacy packer, identical to the app and uncapped."""
        if mesh_aware:
            self._require_mesh()
        if not self.has_changes:
            return SpineAtlas._create(self._text, self._session.export_pages(), self._skeleton, self._allowed)
        fast = bool(fast_nest) if nest and mesh_aware else None
        options = (bool(nest), normalize_gap(gap) if nest else None, bool(mesh_aware), fast)
        if self._build_cache is None or self._build_cache[0] != options:
            text, pages = self._pack(*options)
            self._build_cache = (options, text, pages, self._child_allowed())
        _, text, pages, allowed = self._build_cache
        return SpineAtlas._create(text, pages, self._skeleton, allowed)

    def save(
        self, atlas_path: str | os.PathLike, *, nest: bool = False, gap: int = 4, mesh_aware: bool = False, fast_nest: bool = True,
    ) -> None:
        target = self.build(nest=nest, gap=gap, mesh_aware=mesh_aware, fast_nest=fast_nest)
        text, pngs = target._session.save()
        path = Path(atlas_path)
        root = path.parent.resolve()
        page_paths = {filename: root / filename for filename in pngs}
        for filename, page_path in page_paths.items():
            if not page_path.resolve().is_relative_to(root):
                raise AtlasError(f"page file name {filename!r} points outside the atlas folder")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        for filename, data in pngs.items():
            page_paths[filename].parent.mkdir(parents=True, exist_ok=True)
            page_paths[filename].write_bytes(data)
