"""SpineAtlas: open/load, data accessors, extraction and file export."""
import ntpath
import posixpath
import subprocess
import sys

import pytest
from PIL import Image

import atlas_toolkit.api as api
from atlas_toolkit.api import (
    AtlasError, DuplicateRegionName, MissingPageImage, RegionNotFound, SkeletonUnavailable, SpineAtlas,
)
from atlas_toolkit.engine.session import AtlasSession
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

ATLAS = (
    "page.png\nsize: 16,8\n"
    "Head\n bounds: 0,0,6,5\n"
    "Body\n bounds: 6,0,8,6\n"
    "Arm\n bounds: 0,5,5,3\n offsets: 1,0,7,4\n"
)
TWO_PAGES = ATLAS + "\nsecond.png\nsize: 4,4\nTail\n bounds: 0,0,4,4\n"
HALF_MESH = {"uvs": [0, 0, 0.5, 0, 0.5, 1, 0, 1], "triangles": [0, 1, 2, 0, 2, 3], "hull_length": 4}


def page(width=16, height=8):
    return Image.frombytes(
        "RGBA", (width, height),
        bytes(v for y in range(height) for x in range(width) for v in (x * 15 % 256, y * 30 % 256, 100, 255)),
    )


def reference_session(text=ATLAS, pages=None):
    return AtlasSession.load_images(text, pages or {"page.png": page()}, PillowRasterBackend())


def same(a, b):
    return a.size == b.size and a.convert("RGBA").tobytes() == b.convert("RGBA").tobytes()


def test_load_regions_and_data():
    atlas = SpineAtlas.load(ATLAS, page())
    assert [r.name for r in atlas.regions] == ["Head", "Body", "Arm"]
    arm = atlas.region("Arm")
    assert (arm.original_name, arm.page, arm.bounds, arm.rotate, arm.offsets) == ("Arm", "page.png", (0, 5, 5, 3), 0, (1, 0, 7, 4))
    assert not arm.renamed and not arm.added
    assert atlas.atlas_text == ATLAS
    assert not atlas.has_changes
    assert list(atlas.pages) == ["page.png"] and same(atlas.pages["page.png"], page())


def test_extract_matches_session():
    atlas = SpineAtlas.load(ATLAS, {"page.png": page()})
    ref = reference_session().extract_images(["Head", "Arm"])
    assert same(atlas.extract("Head"), ref["Head"])
    got = atlas.extract(["Arm", "Head"])
    assert list(got) == ["Arm", "Head"] and same(got["Arm"], ref["Arm"])
    assert list(atlas.extract_all()) == ["Head", "Body", "Arm"]
    composite = reference_session().get_preview_image(["Head", "Body"], True)
    assert same(atlas.extract_composite(["Head", "Body"], force_resize=True), composite)


def test_extract_without_names_and_unknown_names():
    atlas = SpineAtlas.load(ATLAS, page())
    with pytest.raises(TypeError):
        atlas.extract()
    with pytest.raises(RegionNotFound):
        atlas.extract("Nope")
    with pytest.raises(KeyError):
        atlas.region("Nope")


def test_isolation_of_inputs_and_outputs():
    source = page()
    atlas = SpineAtlas.load(ATLAS, source)
    source.putpixel((0, 0), (1, 1, 1, 1))
    out = atlas.extract("Head")
    assert out.getpixel((0, 0)) == (0, 0, 100, 255)
    out.putpixel((0, 0), (2, 2, 2, 2))
    atlas.pages["page.png"].putpixel((0, 0), (3, 3, 3, 3))
    assert atlas.extract("Head").getpixel((0, 0)) == (0, 0, 100, 255)


def test_input_modes_rgb_and_palette():
    rgb = page().convert("RGB")
    assert SpineAtlas.load(ATLAS, rgb).pages["page.png"].mode == "RGBA"
    assert rgb.mode == "RGB"
    palette = Image.new("P", (16, 8), 1)
    palette.putpalette([0, 0, 0, 10, 200, 30] + [0] * 762)
    palette.putpixel((0, 0), 0)
    palette.info["transparency"] = 0
    head = SpineAtlas.load(ATLAS, palette).extract("Head")
    assert head.getpixel((0, 0))[3] == 0 and head.getpixel((1, 0)) == (10, 200, 30, 255)


def test_duplicate_names_rejected_but_hash_names_allowed():
    with pytest.raises(DuplicateRegionName) as info:
        SpineAtlas.load(ATLAS + "Head\n bounds: 0,0,1,1\n", page())
    assert info.value.names == ["Head"]
    assert isinstance(info.value, ValueError)
    ok = SpineAtlas.load(ATLAS + "a#b\n bounds: 0,0,1,1\n", page())
    assert ok.region("a#b").name == "a#b"


def test_missing_pages_and_single_image_rules():
    with pytest.raises(MissingPageImage) as info:
        SpineAtlas.load(TWO_PAGES, {"page.png": page()})
    assert info.value.pages == ["second.png"]
    with pytest.raises(MissingPageImage):
        SpineAtlas.load(TWO_PAGES, page())
    atlas = SpineAtlas.load(TWO_PAGES, {"page.png": page(), "second.png": page(4, 4), "extra.png": page()})
    assert list(atlas.pages) == ["page.png", "second.png"]
    assert same(atlas.extract("Tail"), page(4, 4))


def test_open_finds_siblings_and_accepts_overrides(tmp_path):
    (tmp_path / "a.atlas").write_text(ATLAS, encoding="utf-8")
    page().save(tmp_path / "page.png")
    assert same(SpineAtlas.open(tmp_path / "a.atlas").extract("Body"), reference_session().extract_images(["Body"])["Body"])
    flipped = page().transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    flipped.save(tmp_path / "other.png")
    by_path = SpineAtlas.open(str(tmp_path / "a.atlas"), images={"page.png": tmp_path / "other.png"})
    assert same(by_path.pages["page.png"], flipped)
    single = SpineAtlas.open(tmp_path / "a.atlas", images=flipped)
    assert same(single.pages["page.png"], flipped)
    with pytest.raises(TypeError):
        SpineAtlas.load(ATLAS, {"page.png": 42})


def test_declared_size_space_differs_from_image():
    big = page(32, 16)
    atlas = SpineAtlas.load(ATLAS, big)
    assert atlas.region("Head").bounds == (0, 0, 6, 5)
    ref = reference_session(pages={"page.png": big}).extract_images(["Head"])["Head"]
    assert atlas.extract("Head").size == (12, 10)
    assert same(atlas.extract("Head"), ref)


def test_extract_to_files(tmp_path):
    atlas = SpineAtlas.load(ATLAS + "a b\n bounds: 0,0,2,2\na/b\n bounds: 0,0,2,2\na*b\n bounds: 0,0,2,2\n", page())
    atlas.extract_to("Head", tmp_path / "one" / "h.png")
    assert same(Image.open(tmp_path / "one" / "h.png"), atlas.extract("Head"))
    atlas.extract_to(["Head", "a b"], tmp_path / "many")
    assert sorted(p.name for p in (tmp_path / "many").iterdir()) == ["Head.png", "a b.png"]
    with pytest.raises(AtlasError):
        atlas.extract_to(["a/b", "a*b"], tmp_path / "clash")
    assert not (tmp_path / "clash").exists()
    with pytest.raises(AtlasError):
        atlas.extract_all_to(tmp_path / "all")
    assert not (tmp_path / "all").exists()
    atlas.extract_composite_to(["Head", "Body"], tmp_path / "c.png", force_resize=True)
    assert same(Image.open(tmp_path / "c.png"), atlas.extract_composite(["Head", "Body"], force_resize=True))


def test_skeleton_reasons_and_mesh_crop(monkeypatch):
    from skel_mesh_parser import UnsupportedVersionError

    assert SpineAtlas.load(ATLAS, page())._mesh_reason == "none"
    with pytest.raises(SkeletonUnavailable) as info:
        SpineAtlas.load(ATLAS, page()).extract("Head", mesh_crop=True)
    assert info.value.reason == "none"
    cases = [
        (lambda data: (_ for _ in ()).throw(UnsupportedVersionError("9.9")), "unsupported-version"),
        (lambda data: (_ for _ in ()).throw(ValueError("bad")), "parse-error"),
        (lambda data: {"attachments": {"Head": {"type": "Region"}}}, "no-mesh-attachments"),
    ]
    for parse, reason in cases:
        monkeypatch.setattr(api, "_parse_skeleton", parse)
        atlas = SpineAtlas.load(ATLAS, page(), skeleton=b"x")
        assert atlas.extract("Head").size == (6, 5)
        with pytest.raises(SkeletonUnavailable) as info:
            atlas.extract("Head", mesh_crop=True)
        assert info.value.reason == reason
    monkeypatch.setattr(api, "_parse_skeleton", lambda data: {"attachments": {"Head": {"type": "Mesh", **HALF_MESH}}})
    atlas = SpineAtlas.load(ATLAS, page(), skeleton=b"x")
    cropped = atlas.extract("Head", mesh_crop=True)
    assert cropped.getpixel((1, 2))[3] == 255 and cropped.getpixel((5, 2))[3] == 0
    ref = reference_session()
    ref.set_mesh_data({"Head": HALF_MESH})
    ref.set_mesh_mask_enabled(True)
    assert same(cropped, ref.extract_images(["Head"])["Head"])
    assert atlas.extract("Head").getpixel((5, 2))[3] == 255  # mask is per call


def test_lazy_import():
    code = (
        "import sys, atlas_toolkit\n"
        "assert 'PIL' not in sys.modules and 'atlas_toolkit.engine' not in sys.modules\n"
        "atlas_toolkit.SpineAtlas; atlas_toolkit.RegionNotFound\n"
        "assert 'PIL' in sys.modules and 'atlas_toolkit.engine.session' in sys.modules\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_non_ascii_names_keep_their_letters_in_file_names(tmp_path):
    atlas = SpineAtlas.load(ATLAS + "頭\n bounds: 0,0,2,2\ncafé\n bounds: 2,0,2,2\n", page())
    atlas.extract_to(["頭", "café", "Head"], tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(["頭.png", "café.png", "Head.png"])
    assert same(Image.open(tmp_path / "頭.png"), atlas.extract("頭"))


def test_extract_to_refuses_names_that_differ_only_in_case_on_windows(tmp_path, monkeypatch):
    atlas = SpineAtlas.load(ATLAS + "head\n bounds: 6,5,2,2\n", page())
    monkeypatch.setattr(api.os.path, "normcase", posixpath.normcase)
    atlas.extract_to(["Head", "head"], tmp_path / "posix")  # distinct files on a case-sensitive system
    monkeypatch.setattr(api.os.path, "normcase", ntpath.normcase)
    with pytest.raises(AtlasError):
        atlas.extract_to(["Head", "head"], tmp_path / "win")
    assert not (tmp_path / "win").exists()


def test_empty_composite_and_page_less_atlas_are_refused():
    atlas = SpineAtlas.load(ATLAS, page())
    with pytest.raises(TypeError):
        atlas.extract_composite([])
    for pages in (page(), {"page.png": page()}):
        with pytest.raises(AtlasError):
            SpineAtlas.load("Head\n bounds: 0,0,1,1\n", pages)
