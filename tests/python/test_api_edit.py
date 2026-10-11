"""SpineAtlas edits: recorded, validated at the call, addressed by current name."""
import io

import pytest
from PIL import Image

from atlas_toolkit.api import (
    AtlasError, InvalidRegionName, MultiPageNotSupported, RegionNotFound, SpineAtlas,
)

ATLAS = (
    "page.png\nsize: 16,8\n"
    "Head\n bounds: 0,0,6,5\n"
    "Body\n bounds: 6,0,8,6\n"
    "Arm\n bounds: 0,5,5,3\n"
)
TWO_PAGES = ATLAS + "\nsecond.png\nsize: 4,4\nTail\n bounds: 0,0,4,4\n"


def page(width=16, height=8):
    return Image.frombytes(
        "RGBA", (width, height),
        bytes(v for y in range(height) for x in range(width) for v in (x * 15 % 256, y * 30 % 256, 100, 255)),
    )


def sprite(width=3, height=3):
    return Image.new("RGBA", (width, height), (1, 2, 3, 255))


def names(atlas):
    return [(r.name, r.original_name) for r in atlas.regions]


def test_rename_swap_resolves_by_current_name():
    atlas = SpineAtlas.load(ATLAS, page())
    atlas.rename_region("Body", "X")
    atlas.rename_region("Head", "Body")
    assert names(atlas) == [("Body", "Head"), ("X", "Body"), ("Arm", "Arm")]
    assert atlas.region("Body").renamed
    original_head = SpineAtlas.load(ATLAS, page()).extract("Head")
    assert atlas.extract("Body").tobytes() == original_head.tobytes()
    atlas.remove_region("Body")
    assert names(atlas) == [("X", "Body"), ("Arm", "Arm")]
    with pytest.raises(RegionNotFound):
        atlas.region("Head")


def test_add_rename_back_and_to_removed_names():
    atlas = SpineAtlas.load(ATLAS, page())
    atlas.add_region("New", sprite())
    assert atlas.region("New").added and atlas.region("New").bounds is None
    atlas.rename_region("New", "Newer")
    assert names(atlas)[-1] == ("Newer", None)
    atlas.rename_region("Head", "H2")
    atlas.rename_region("H2", "Head")
    assert not atlas.region("Head").renamed
    atlas.remove_region("Arm")
    atlas.rename_region("Body", "Arm")
    assert names(atlas) == [("Head", "Head"), ("Arm", "Body"), ("Newer", None)]
    assert atlas.has_changes


def test_invalid_names():
    atlas = SpineAtlas.load(ATLAS, page())
    for bad in ["Body", " Body ", "", "a#b", "x.png", "a:b"]:
        with pytest.raises(InvalidRegionName):
            atlas.rename_region("Head", bad)
        with pytest.raises(InvalidRegionName):
            atlas.add_region(bad, sprite())
    assert not atlas.has_changes
    atlas.add_region("  Spaced  ", sprite())
    assert names(atlas)[-1] == ("Spaced", None)


def test_pending_added_regions_refuse_replace_and_extract():
    atlas = SpineAtlas.load(ATLAS, page())
    atlas.add_region("New", sprite())
    before = names(atlas)
    for call in (
        lambda: atlas.replace({"Head": sprite(6, 5), "New": sprite()}),
        lambda: atlas.replace_composite(["Head", "New"], sprite(6, 5)),
        lambda: atlas.extract("New"),
        lambda: atlas.extract_composite(["New"]),
    ):
        with pytest.raises(AtlasError):
            call()
    assert names(atlas) == before
    assert list(atlas.extract_all()) == ["Head", "Body", "Arm"]
    atlas.remove_region("New")
    assert names(atlas) == [("Head", "Head"), ("Body", "Body"), ("Arm", "Arm")]


def test_remove_all_and_zero_area_are_refused_at_the_call():
    atlas = SpineAtlas.load(ATLAS, page())
    atlas.add_region("New", sprite())
    with pytest.raises(AtlasError):
        atlas.remove_region(["Head", "Body", "Arm", "New"])
    assert len(atlas.regions) == 4
    for image in (Image.new("RGBA", (0, 5)), Image.new("RGBA", (5, 0))):
        for call in (
            lambda: atlas.add_region("Z", image),
            lambda: atlas.replace({"Head": image}),
            lambda: atlas.replace_composite(["Head", "Body"], image),
        ):
            with pytest.raises(AtlasError):
                call()
    assert len(atlas.regions) == 4


def test_unknown_names_raise_region_not_found():
    atlas = SpineAtlas.load(ATLAS, page())
    for call in (
        lambda: atlas.replace({"Nope": sprite()}),
        lambda: atlas.replace({"Head": sprite(6, 5), "Nope": sprite()}),
        lambda: atlas.rename_region("Nope", "X"),
        lambda: atlas.remove_region(["Head", "Nope"]),
    ):
        with pytest.raises(RegionNotFound):
            call()
    assert not atlas.has_changes


def test_failing_replace_records_nothing():
    atlas = SpineAtlas.load(ATLAS, page())
    buffer = io.BytesIO()
    page(6, 5).save(buffer, format="PNG")
    truncated = Image.open(io.BytesIO(buffer.getvalue()[:60]))  # lazily decoded, fails on load
    for bad in (Image.new("RGBA", (0, 3)), truncated, b"not an image"):
        with pytest.raises((AtlasError, OSError, TypeError)):
            atlas.replace({"Head": sprite(6, 5), "Body": bad})
    with pytest.raises(TypeError):
        atlas.replace([("Head", sprite(6, 5))])
    assert not atlas.has_changes
    atlas.add_region("New", sprite())
    batches = len(atlas._session._mod_batches)
    with pytest.raises(AtlasError):
        atlas.replace({"Head": sprite(6, 5), "New": sprite()})
    assert len(atlas._session._mod_batches) == batches


def test_multi_page_edits_raise_at_the_call():
    atlas = SpineAtlas.load(TWO_PAGES, {"page.png": page(), "second.png": page(4, 4)})
    for call in (
        lambda: atlas.replace({"Head": sprite(6, 5)}),
        lambda: atlas.replace_composite(["Head"], sprite(6, 5)),
        lambda: atlas.add_region("N", sprite()),
        lambda: atlas.rename_region("Head", "H"),
        lambda: atlas.remove_region("Head"),
    ):
        with pytest.raises(MultiPageNotSupported):
            call()
    assert atlas.extract("Tail").size == (4, 4)


def test_edit_images_are_copied():
    atlas = SpineAtlas.load(ATLAS, page())
    added, replaced = page(3, 3), page(6, 5)
    want_added, want_replaced = added.copy(), replaced.copy()
    atlas.add_region("New", added)
    atlas.replace({"Head": replaced})
    added.putpixel((0, 0), (9, 9, 9, 9))
    replaced.putpixel((0, 0), (9, 9, 9, 9))
    out = atlas.build()
    assert out.extract("New").tobytes() == want_added.tobytes()
    # belt-and-braces: prepare_mod_image clones the source, so only the add path can expose a missing copy
    assert out.extract("Head").tobytes() == want_replaced.tobytes()


def test_rejected_zero_area_edit_leaves_the_session_packable_with_nest():
    # A zero-area image recorded in deferred mode would make every later pack fail;
    # the API must refuse it at the call so packing still works afterwards.
    atlas = SpineAtlas.load(ATLAS, page())
    atlas._session.set_nest_regions_enabled(True)
    atlas.replace({"Head": sprite(6, 5)})
    for image in (Image.new("RGBA", (0, 3)), Image.new("RGBA", (3, 0))):
        with pytest.raises(AtlasError):
            atlas.add_region("Z", image)
        with pytest.raises(AtlasError):
            atlas.replace({"Body": image})
    text, pages = atlas._session.pack_pending()
    assert "Z" not in text.splitlines()
    assert pages["page.png"].width > 0
