"""Parity tests for AtlasModifier._pack_and_emit against the real
www/js/atlas-modifier.js (plain Node, via conftest.py's run_packing_oracle
fixture), plus pinned no-Node cases.

Sprite pixel content is chosen per case: two sprites given the same tint
are byte-identical and must dedup onto one placement, which is observable
through region_bounds without ever comparing digest strings across
runtimes."""
import hashlib
import io

import pytest
from PIL import Image

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.engine.modifier import AtlasModifier, region_meta
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

PAGE_TEXT = "\n".join([
    "p.png", "size: 64,64", "format: RGBA8888", "filter: Linear, Linear", "repeat: none",
    "alpha", "  bounds: 0, 0, 20, 12", "  offsets: 3, 4, 40, 30",
    "beta", "  bounds: 20, 0, 20, 12", "  offsets: 3, 4, 40, 30",
    "gamma", "  bounds: 0, 12, 9, 17",
])
INT_TEXT = "\n".join([
    "p.png", "size: 64,64",
    "10", "  bounds: 0, 0, 20, 12",
    "2", "  bounds: 20, 0, 8, 30",
    "a", "  bounds: 0, 12, 9, 17",
])

DISTINCT = {
    "alpha": {"w": 20, "h": 12, "content": "A"},
    "beta": {"w": 20, "h": 12, "content": "B"},
    "gamma": {"w": 9, "h": 17, "content": "C"},
}
DEDUPED = {
    "alpha": {"w": 20, "h": 12, "content": "SAME"},
    "beta": {"w": 20, "h": 12, "content": "SAME"},
    "gamma": {"w": 9, "h": 17, "content": "C"},
}
MISSING = {
    "alpha": {"w": 20, "h": 12, "content": "A"},
    "gamma": {"w": 9, "h": 17, "content": "C"},
}
INT_SPRITES = {
    "10": {"w": 20, "h": 12, "content": "TEN"},
    "2": {"w": 8, "h": 30, "content": "TWO"},
    "a": {"w": 9, "h": 17, "content": "A"},
}

CASES = {
    "pack_distinct": ("packAndEmit", [PAGE_TEXT, 64, 64, "p.png", DISTINCT, None, None]),
    "pack_dedup": ("packAndEmit", [PAGE_TEXT, 64, 64, "p.png", DEDUPED, None, None]),
    "pack_full_canvas": ("packAndEmit", [PAGE_TEXT, 64, 64, "p.png", DISTINCT, ["alpha"], None]),
    "pack_missing_sprite": ("packAndEmit", [PAGE_TEXT, 64, 64, "p.png", MISSING, None, None]),
    "pack_nest_gap4": (
        "packAndEmit",
        [PAGE_TEXT, 64, 64, "p.png", DISTINCT, None, {"enabled": True, "gapDistance": 4}],
    ),
    "pack_nest_gap8": (
        "packAndEmit",
        [PAGE_TEXT, 64, 64, "p.png", DISTINCT, None, {"enabled": True, "gapDistance": 8}],
    ),
    "pack_nest_disabled": (
        "packAndEmit",
        [PAGE_TEXT, 64, 64, "p.png", DISTINCT, None, {"enabled": False, "gapDistance": 4}],
    ),
    "pack_integer_like_names": ("packAndEmit", [INT_TEXT, 64, 64, "p.png", INT_SPRITES, None, None]),
}

PY_ARGS = {
    "pack_distinct": (PAGE_TEXT, DISTINCT, None, None),
    "pack_dedup": (PAGE_TEXT, DEDUPED, None, None),
    "pack_full_canvas": (PAGE_TEXT, DISTINCT, {"alpha"}, None),
    "pack_missing_sprite": (PAGE_TEXT, MISSING, None, None),
    "pack_nest_gap4": (PAGE_TEXT, DISTINCT, None, {"enabled": True, "gap_distance": 4}),
    "pack_nest_gap8": (PAGE_TEXT, DISTINCT, None, {"enabled": True, "gap_distance": 8}),
    "pack_nest_disabled": (PAGE_TEXT, DISTINCT, None, {"enabled": False, "gap_distance": 4}),
    "pack_integer_like_names": (INT_TEXT, INT_SPRITES, None, None),
}


class _RecordingRaster(PillowRasterBackend):
    """Records paste positions, comparable with the JS oracle's recorded
    drawImage positions."""

    def __init__(self) -> None:
        super().__init__()
        self.pastes: list[list[int]] = []

    def paste(self, dest, src, x, y):
        self.pastes.append([x, y])
        super().paste(dest, src, x, y)


def _sprite(raster: PillowRasterBackend, w: int, h: int, content: str):
    """A solid image whose colour is a deterministic function of
    `content`, so two sprites sharing a content tag are byte-identical and
    dedup together -- the same property the JS oracle's FakeCanvas gives
    its synthetic getImageData bytes."""
    # Hashed, not summed: an additive checksum is anagram-invariant, so two
    # future content tags that are permutations of each other would produce
    # byte-identical sprites and dedup together by accident.
    digest = hashlib.sha256(content.encode()).digest()
    colour = (digest[0], digest[1], digest[2], 255)
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), colour).save(buf, format="PNG")
    return raster.decode_png(buf.getvalue())


def _python_result(text, specs, full_canvas_regions, nest_options):
    raster = _RecordingRaster()
    document = AtlasDocument.parse(text)
    modifier = AtlasModifier(document, raster.create(64, 64), raster, "p.png")
    sprites = {
        name: _sprite(raster, spec["w"], spec["h"], spec["content"])
        for name, spec in specs.items()
    }
    from atlas_toolkit.engine.modifier import js_object_key_order

    region_names = js_object_key_order(modifier.region_names)
    regions = {name: region_meta(modifier.regions[name]) for name in region_names}
    result = modifier._pack_and_emit(
        sprites, region_names, regions, full_canvas_regions, nest_options
    )
    canvas_w, canvas_h = raster.size(result.image)
    return {
        "canvas_w": canvas_w,
        "canvas_h": canvas_h,
        "draws": raster.pastes,
        "region_bounds": result.region_bounds,
        "atlas_text": result.document.serialize(),
    }


@pytest.fixture(scope="module")
def js_results(run_packing_oracle):
    case_ids = list(CASES)
    return dict(zip(case_ids, run_packing_oracle([CASES[c] for c in case_ids]), strict=True))


@pytest.mark.parametrize("case_id", list(CASES))
def test_matches_real_js(case_id, js_results):
    assert _python_result(*PY_ARGS[case_id]) == js_results[case_id]


def test_identical_sprites_collapse_onto_one_placement():
    result = _python_result(*PY_ARGS["pack_dedup"])
    bounds = result["region_bounds"]
    assert bounds["alpha"] == bounds["beta"]
    # Two pastes, not three: the deduped sprite is drawn once.
    assert len(result["draws"]) == 2
    assert (result["canvas_w"], result["canvas_h"]) == (20, 24)


def test_distinct_sprites_each_get_their_own_placement():
    result = _python_result(*PY_ARGS["pack_distinct"])
    bounds = result["region_bounds"]
    assert bounds["alpha"] != bounds["beta"]
    assert len(result["draws"]) == 3


def test_full_canvas_regions_lose_their_offsets():
    result = _python_result(*PY_ARGS["pack_full_canvas"])
    # alpha's offsets reset to the default, which the serializer omits;
    # beta keeps its pristine offsets.
    assert "alpha\n  bounds: 0, 0, 20, 12\nbeta" in result["atlas_text"]
    assert "beta\n  bounds: 0, 12, 20, 12\n  offsets: 3, 4, 40, 30" in result["atlas_text"]


def test_a_region_without_a_sprite_is_dropped():
    result = _python_result(*PY_ARGS["pack_missing_sprite"])
    assert set(result["region_bounds"]) == {"alpha", "gamma"}
    assert "beta" not in result["atlas_text"]


def test_integer_like_names_pack_in_js_key_order():
    # "2" is listed second in the atlas text but packs first.
    result = _python_result(*PY_ARGS["pack_integer_like_names"])
    assert result["region_bounds"]["2"][:2] == [0, 0]
    assert result["atlas_text"].index("\n2\n") < result["atlas_text"].index("\n10\n")


def test_nest_gap_changes_the_layout():
    gap4 = _python_result(*PY_ARGS["pack_nest_gap4"])
    gap8 = _python_result(*PY_ARGS["pack_nest_gap8"])
    assert gap4["region_bounds"] != gap8["region_bounds"]


def test_disabled_nest_options_use_the_shelf_packer():
    assert _python_result(*PY_ARGS["pack_nest_disabled"]) == _python_result(*PY_ARGS["pack_distinct"])


def test_emitted_offsets_do_not_alias_the_input_document():
    raster = _RecordingRaster()
    document = AtlasDocument.parse(PAGE_TEXT)
    modifier = AtlasModifier(document, raster.create(64, 64), raster, "p.png")
    sprites = {
        name: _sprite(raster, spec["w"], spec["h"], spec["content"])
        for name, spec in DISTINCT.items()
    }
    regions = {name: region_meta(modifier.regions[name]) for name in modifier.region_names}
    result = modifier._pack_and_emit(sprites, modifier.region_names, regions, None)
    emitted = result.document.pages[0].regions[0]
    assert emitted.offsets == [3, 4, 40, 30]
    emitted.offsets[0] = 999
    assert modifier.regions["alpha"].offsets == [3, 4, 40, 30]


def test_pma_is_preserved_rather_than_dropped():
    # DELIBERATE DEVIATION from the oracle, ruled by the maintainer: real JS
    # loses `pma: true` on every repack (_parseScoped never forwards it once
    # a target page is set, which is always). This port keeps it, so there
    # is deliberately no parity case for a pma page -- one would fail for
    # the right reason. See _page_info's docstring.
    raster = _RecordingRaster()
    document = AtlasDocument.parse(PAGE_TEXT.replace("size: 64,64", "size: 64,64\npma: true"))
    modifier = AtlasModifier(document, raster.create(64, 64), raster, "p.png")
    sprites = {
        name: _sprite(raster, spec["w"], spec["h"], spec["content"])
        for name, spec in DISTINCT.items()
    }
    regions = {name: region_meta(modifier.regions[name]) for name in modifier.region_names}
    result = modifier._pack_and_emit(sprites, modifier.region_names, regions, None)
    assert result.document.pages[0].pma is True
    assert "pma: true" in result.document.serialize()
