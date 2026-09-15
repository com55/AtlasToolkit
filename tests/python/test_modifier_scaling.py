"""Parity tests for engine/modifier.py's page-size reconciliation and
AtlasModifier's page-scoped construction against the real
www/js/atlas-modifier.js (plain Node, via conftest.py's
run_modifier_oracle fixture), plus pinned no-Node tests for the
object-identity / non-mutation guarantees the oracle can't see."""
import pytest

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.engine.modifier import AtlasModifier
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

TWO_PAGE = (
    "a.png\nsize: 10,10\n"
    "arm\n  bounds: 0, 0, 4, 4\n  offsets: 0, 0, 4, 4\n"
    "leg\n  rotate: 45\n  bounds: 0, 0, 2, 2\n  offsets: 1, 1, 3, 3\n"
    "b.png\nsize: 10,10\n"
    "arm\n  bounds: 1, 1, 2, 2"
)
NO_SIZE = "a.png\narm\n  bounds: 1, 2, 3, 4"
NON_UNIFORM = "a.png\nsize: 32,32\nhero\n  rotate: true\n  bounds: 2, 3, 6, 5\n  offsets: 1, 3, 10, 13"
# Non-default values for every non-geometric field the repack stage reads
# off scoped regions (atlasName via a deduped key, index, split, pad,
# extraPairs) -- rescaling must carry all of them through unchanged.
META_FIELDS = (
    "a.png\nsize: 10,10\n"
    "hero\n  bounds: 0, 0, 2, 2\n"
    "hero\n  index: 3\n  bounds: 1, 1, 4, 4\n  split: 1, 2, 3, 4\n  pad: 0, 1, 0, 1\n  color: ff0000, 12"
)

CASES = {
    "ms_noop_second_page": ("modifierScoped", [TWO_PAGE, 10, 10, "b.png"]),
    "ms_noop_keeps_explicit_default_offsets": ("modifierScoped", [TWO_PAGE, 10, 10, "a.png"]),
    "ms_upscale_nulls_default_offsets_and_odd_rotate": ("modifierScoped", [TWO_PAGE, 20, 20, "a.png"]),
    "ms_upscale_second_page_dedup_key": ("modifierScoped", [TWO_PAGE, 20, 20, "b.png"]),
    "ms_downscale_bankers_ties": ("modifierScoped", [TWO_PAGE, 5, 5, "b.png"]),
    "ms_target_page_absent": ("modifierScoped", [TWO_PAGE, 20, 20, "missing.png"]),
    "ms_zero_declared_size": ("modifierScoped", [NO_SIZE, 20, 20, "a.png"]),
    "ms_non_uniform_rotated": ("modifierScoped", [NON_UNIFORM, 64, 48, "a.png"]),
    "ms_upscale_preserves_meta_fields": ("modifierScoped", [META_FIELDS, 20, 20, "a.png"]),
}


def _modifier(text: str, real_w: int, real_h: int, target_page: str) -> AtlasModifier:
    raster = PillowRasterBackend()
    return AtlasModifier(AtlasDocument.parse(text), raster.create(real_w, real_h), raster, target_page)


def _python_result(op: str, args: list):
    if op == "modifierScoped":
        modifier = _modifier(*args)
        return {
            "region_names": modifier.region_names,
            "regions": {
                name: {
                    "bounds": region.bounds,
                    "offsets": region.offsets,
                    "rotate": region.rotate,
                    "page": region.page_filename,
                    "atlas_name": region.atlas_name,
                    "index": region.index,
                    "split": region.split,
                    "pad": region.pad,
                    "extra_pairs": [[key, values] for key, values in region.extra_pairs],
                }
                for name, region in modifier.regions.items()
            },
            "page_sizes": [[page.filename, page.size] for page in modifier.document.pages],
        }
    raise ValueError(f"unknown op: {op}")


@pytest.fixture(scope="module")
def js_results(run_modifier_oracle):
    case_ids = list(CASES)
    return dict(zip(case_ids, run_modifier_oracle([CASES[c] for c in case_ids]), strict=True))


@pytest.mark.parametrize("case_id", list(CASES))
def test_matches_real_js(case_id, js_results):
    op, args = CASES[case_id]
    assert _python_result(op, args) == js_results[case_id]


def test_noop_returns_the_same_document_object():
    raster = PillowRasterBackend()
    document = AtlasDocument.parse(TWO_PAGE)
    modifier = AtlasModifier(document, raster.create(10, 10), raster, "a.png")
    assert modifier.document is document
    assert modifier.regions["arm"].offsets == [0, 0, 4, 4]


def test_scaling_does_not_mutate_the_input_document():
    raster = PillowRasterBackend()
    document = AtlasDocument.parse(TWO_PAGE)
    AtlasModifier(document, raster.create(20, 20), raster, "a.png")
    assert document.pages[0].size == [10, 10]
    assert document.pages[0].regions[0].bounds == [0, 0, 4, 4]
    assert document.pages[0].regions[0].offsets == [0, 0, 4, 4]
    assert document.pages[0].regions[1].rotate == 45


def test_non_uniform_scale_pinned():
    # sx = 2, sy = 1.5: y 3 -> 4.5 -> 4, h 5 -> 7.5 -> 8, off_y 3 -> 4.5 -> 4,
    # orig_h 13 -> 19.5 -> 20 (banker's rounding on every tie).
    modifier = _modifier(NON_UNIFORM, 64, 48, "a.png")
    hero = modifier.regions["hero"]
    assert hero.bounds == [4, 4, 12, 8]
    assert hero.offsets == [2, 4, 20, 20]
    assert hero.rotate == 90
    assert modifier.document.pages[0].size == [64, 48]
