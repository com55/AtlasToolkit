"""SpineAtlas and the fast nest packer: fast_nest opt-out, the canvas
limit as a public error, and output that extracts the same as legacy."""
import pytest
from PIL import Image

import atlas_toolkit
import atlas_toolkit.api as api
import atlas_toolkit.engine.modifier as modifier
from atlas_toolkit.api import AtlasError, CanvasTooLarge, SpineAtlas
from atlas_toolkit.domain import nest_frontier
from atlas_toolkit.engine.session import AtlasSession

ATLAS = (
    "page.png\nsize: 16,8\n"
    "Head\n bounds: 0,0,6,5\n"
    "Body\n bounds: 6,0,8,6\n"
    "Arm\n bounds: 0,5,5,3\n offsets: 1,0,7,4\n"
)
LEFT = {"type": "Mesh", "uvs": [0, 0, 0.5, 0, 0.5, 1, 0, 1], "triangles": [0, 1, 2, 0, 2, 3], "hull_length": 4}
RIGHT = {"type": "Mesh", "uvs": [0.5, 0, 1, 0, 1, 1, 0.5, 1], "triangles": [0, 1, 2, 0, 2, 3], "hull_length": 4}
MESHES = {"Head": LEFT, "Body": RIGHT}
NO_MATCH = {"Elsewhere": LEFT}  # the skeleton has a mesh, but no region uses it


def page():
    return Image.frombytes(
        "RGBA", (16, 8), bytes(v for y in range(8) for x in range(16) for v in (x * 15, y * 30, 100, 255)),
    )


def sprite(width, height, seed):
    return Image.frombytes(
        "RGBA", (width, height),
        bytes(v for y in range(height) for x in range(width) for v in ((x + seed) * 20 % 256, y * 40, seed, 255)),
    )


def edited(monkeypatch, attachments=MESHES):
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": attachments})
    atlas = SpineAtlas.load(ATLAS, page(), skeleton=b"x")
    atlas.replace({"Head": sprite(6, 5, 1), "Arm": sprite(5, 3, 7)})
    atlas.add_region("Twin", sprite(5, 3, 7))  # same pixels as the new Arm: a dedup alias
    return atlas


@pytest.fixture
def packers(monkeypatch):
    calls = []
    for name in ("nest_pack", "nest_pack_frontier"):
        real = getattr(modifier, name)
        monkeypatch.setattr(
            modifier, name,
            lambda *args, _name=name, _real=real, **kwargs: calls.append(_name) or _real(*args, **kwargs),
        )
    return calls


def reopened(tmp_path, monkeypatch, atlas, attachments=MESHES, **options):
    folder = tmp_path / ("fast" if options.get("fast_nest", True) else "legacy")
    atlas.save(folder / "out.atlas", nest=True, mesh_aware=True, **options)
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": attachments})
    return SpineAtlas.open(folder / "out.atlas", skeleton=b"x")


def test_fast_nest_picks_the_packer(monkeypatch, packers):
    atlas = edited(monkeypatch)
    atlas.build(nest=True, mesh_aware=True)
    atlas.build(nest=True, mesh_aware=True, fast_nest=False)
    assert atlas._session._fast_mesh_nest is True  # restored after a fast_nest=False build
    atlas.build(nest=True, fast_nest=True)
    assert packers == ["nest_pack_frontier", "nest_pack", "nest_pack"]


def test_fast_nest_is_in_the_build_cache_key_only_where_it_matters(monkeypatch):
    atlas = edited(monkeypatch)
    packs = []
    real = AtlasSession.pack_pending
    monkeypatch.setattr(AtlasSession, "pack_pending", lambda self: packs.append(1) or real(self))
    atlas.build(nest=True, mesh_aware=True)
    atlas.build(nest=True, mesh_aware=True, fast_nest=False)
    assert len(packs) == 2
    atlas.build(nest=True)
    atlas.build(nest=True, fast_nest=False)
    assert len(packs) == 3


def test_saved_output_extracts_the_same_as_legacy(tmp_path, monkeypatch, packers):
    fast = reopened(tmp_path, monkeypatch, edited(monkeypatch))
    legacy = reopened(tmp_path, monkeypatch, edited(monkeypatch), fast_nest=False)
    assert packers == ["nest_pack_frontier", "nest_pack"]
    assert [r.name for r in fast.regions] == [r.name for r in legacy.regions]
    for region in fast.regions:
        assert region.offsets == legacy.region(region.name).offsets, region.name
        crop = region.name in MESHES
        a = fast.extract(region.name, mesh_crop=crop)
        b = legacy.extract(region.name, mesh_crop=crop)
        assert (a.size, a.tobytes()) == (b.size, b.tobytes()), region.name
    head = fast.extract("Head", mesh_crop=True)
    assert head.getpixel((5, 2))[3] == 0  # outside the left-half mesh: removed
    assert head.getpixel((1, 2))[3] == 255  # inside it: kept


def test_mesh_aware_without_any_mesh_matches_legacy_extracts(tmp_path, monkeypatch, packers):
    fast = reopened(tmp_path, monkeypatch, edited(monkeypatch, NO_MATCH), attachments=NO_MATCH)
    legacy = reopened(tmp_path, monkeypatch, edited(monkeypatch, NO_MATCH), attachments=NO_MATCH, fast_nest=False)
    assert packers == ["nest_pack_frontier", "nest_pack"]
    for region in fast.regions:
        a, b = fast.extract(region.name), legacy.extract(region.name)
        assert (a.size, a.tobytes()) == (b.size, b.tobytes()), region.name


def test_canvas_limit_is_a_public_error_and_changes_nothing(monkeypatch):
    atlas = edited(monkeypatch)
    good = atlas.build(nest=True)
    cache = atlas._build_cache
    names = [r.name for r in atlas.regions]
    monkeypatch.setattr(nest_frontier, "MAX_CANVAS", 8)
    with pytest.raises(CanvasTooLarge) as raised:
        atlas.build(nest=True, mesh_aware=True)
    assert isinstance(raised.value, AtlasError)
    assert raised.value.limit == 8
    assert "fast_nest=False" in str(raised.value)
    assert atlas._build_cache is cache
    assert atlas.has_changes and [r.name for r in atlas.regions] == names
    snap = atlas._session.snapshot()
    assert (snap.nest_regions_enabled, snap.nest_gap_distance, snap.mesh_aware_repack_enabled) == (False, 4, False)
    assert atlas._session._fast_mesh_nest is True
    assert atlas.build(nest=True, mesh_aware=True, fast_nest=False).atlas_text
    assert atlas.build(nest=True).atlas_text == good.atlas_text


def test_canvas_too_large_is_exported():
    assert atlas_toolkit.CanvasTooLarge is CanvasTooLarge
    assert "CanvasTooLarge" in api.__all__


def test_repeated_builds_reuse_the_mesh_cache(monkeypatch):
    atlas = edited(monkeypatch)
    from atlas_toolkit.raster.pillow_backend import PillowRasterBackend
    calls = []
    real = PillowRasterBackend._rasterize_alpha
    monkeypatch.setattr(PillowRasterBackend, "_rasterize_alpha", lambda self, *a: calls.append(1) or real(self, *a))
    atlas.build(nest=True, mesh_aware=True)
    first = len(calls)
    atlas.replace({"Head": sprite(6, 5, 2)})  # new edit: the build cache is dropped, the mesh cache is not
    atlas.build(nest=True, mesh_aware=True)
    assert first > 0 and len(calls) == first
