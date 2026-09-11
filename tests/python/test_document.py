from pathlib import Path

from atlas_toolkit.document import AtlasDocument, Region

FIXTURES_DIR = Path(__file__).parent.parent / "browser" / "fixtures"

# Shared fixture text, ported verbatim from tests/atlas-document.test.js's
# FIXTURE constant. Covers: a region with an explicit-but-redundant
# offsets: line equal to (0,0,w,h) (must drop on serialize), a region with
# genuinely non-default offsets, a region rotated 90 ("true") and one
# rotated 180, a region with an explicit index:, two regions sharing the
# literal name "dagger" (dedup -> "dagger#2"), an unknown region key
# ("customKey") round-tripping via extra_pairs, and a second page.
FIXTURE = """page1.png
size: 100,100
filter: Linear, Linear
pma: true
armR
bounds: 0, 0, 10, 10
offsets: 0, 0, 10, 10
armL
index: 3
bounds: 10, 0, 8, 8
offsets: 1, 1, 12, 12
head
bounds: 0, 10, 20, 20
rotate: 90
foot
bounds: 20, 10, 5, 5
rotate: 180
dagger
bounds: 30, 10, 4, 4
customKey: foo, bar
dagger
bounds: 34, 10, 4, 4

page2.png
size: 50,50
regionA
bounds: 0, 0, 5, 5
regionB
bounds: 5, 0, 5, 5
"""

FIXTURE_SERIALIZED = "\n".join([
    "page1.png",
    "size: 100,100",
    "filter: Linear, Linear",
    "pma: true",
    "armR",
    "  bounds: 0, 0, 10, 10",
    "armL",
    "  index: 3",
    "  bounds: 10, 0, 8, 8",
    "  offsets: 1, 1, 12, 12",
    "head",
    "  rotate: true",
    "  bounds: 0, 10, 20, 20",
    "foot",
    "  rotate: 180",
    "  bounds: 20, 10, 5, 5",
    "dagger",
    "  bounds: 30, 10, 4, 4",
    "  customkey: foo, bar",
    "dagger",
    "  bounds: 34, 10, 4, 4",
    "",
    "page2.png",
    "size: 50,50",
    "regionA",
    "  bounds: 0, 0, 5, 5",
    "regionB",
    "  bounds: 5, 0, 5, 5",
])


def test_parse_serialize_round_trip_matches_hand_computed_output():
    doc = AtlasDocument.parse(FIXTURE)
    assert doc.serialize() == FIXTURE_SERIALIZED


def test_default_offsets_region_drops_offsets_line_even_with_explicit_redundant_one():
    doc = AtlasDocument.parse(FIXTURE)
    armR = next(r for r in doc.pages[0].regions if r.name == "armR")
    assert armR.offsets == [0, 0, 10, 10]
    assert "offsets: 0, 0, 10, 10" not in doc.serialize()


def test_non_default_offsets_region_keeps_its_offsets_line():
    doc = AtlasDocument.parse(FIXTURE)
    assert "  offsets: 1, 1, 12, 12" in doc.serialize()


def test_rotate_90_serializes_as_true_and_180_as_literal_180():
    doc = AtlasDocument.parse(FIXTURE)
    head = next(r for r in doc.pages[0].regions if r.name == "head")
    foot = next(r for r in doc.pages[0].regions if r.name == "foot")
    assert head.rotate == 90
    assert foot.rotate == 180
    text = doc.serialize()
    assert "  rotate: true" in text
    assert "  rotate: 180" in text


def test_explicit_index_round_trips_and_default_is_omitted():
    doc = AtlasDocument.parse(FIXTURE)
    armL = next(r for r in doc.pages[0].regions if r.name == "armL")
    assert armL.index == 3
    armR = next(r for r in doc.pages[0].regions if r.name == "armR")
    assert armR.index == -1
    text = doc.serialize()
    assert "  index: 3" in text
    assert "index: -1" not in text


def test_region_name_collision_numbering():
    doc = AtlasDocument.parse(FIXTURE)
    daggers = [r for r in doc.pages[0].regions if r.atlas_name == "dagger"]
    assert len(daggers) == 2
    assert daggers[0].name == "dagger"
    assert daggers[1].name == "dagger#2"
    assert daggers[0].atlas_name == "dagger"
    assert daggers[1].atlas_name == "dagger"
    text = doc.serialize()
    # Both serialize back out under the literal line "dagger", not "dagger#2"
    # -- count exact-line matches (mirrors the JS test's /^dagger$/gm count).
    assert text.split("\n").count("dagger") == 2
    assert "dagger#2" not in text


def test_multi_page_atlas_parses_correct_page_region_association():
    doc = AtlasDocument.parse(FIXTURE)
    assert len(doc.pages) == 2
    assert doc.pages[0].filename == "page1.png"
    assert doc.pages[1].filename == "page2.png"
    assert len(doc.pages[0].regions) == 6
    assert len(doc.pages[1].regions) == 2
    for region in doc.pages[0].regions:
        assert region.page_filename == "page1.png"
    for region in doc.pages[1].regions:
        assert region.page_filename == "page2.png"


def test_unknown_region_key_round_trips_as_lowercased_extra_pair():
    doc = AtlasDocument.parse(FIXTURE)
    dagger = next(r for r in doc.pages[0].regions if r.name == "dagger")
    assert dagger.extra_pairs == [("customkey", ["foo", "bar"])]
    assert "  customkey: foo, bar" in doc.serialize()


def test_unknown_page_key_round_trips():
    text = "page1.png\nsize: 10,10\nmystery: 1, 2, 3\nregion\nbounds: 0, 0, 5, 5\n"
    doc = AtlasDocument.parse(text)
    assert doc.pages[0].extra_pairs == [("mystery", ["1", "2", "3"])]
    assert "mystery: 1, 2, 3" in doc.serialize()


def test_region_helper_methods_bounds_and_bounds_with_rotate():
    r = Region(
        name="leg", atlas_name="leg", page_filename="page1.png",
        x=1, y=2, w=3, h=4, rotate=90, index=7,
        split=[1, 1, 1, 1], pad=None, extra_pairs=[("foo", ["bar"])],
    )
    assert r.bounds == [1, 2, 3, 4]
    assert r.bounds_with_rotate() == [1, 2, 3, 4, 90]
    assert r.to_meta_dict() == {
        "atlasName": "leg", "index": 7,
        "split": [1, 1, 1, 1], "pad": None,
        "extraPairs": [("foo", ["bar"])],
    }


def test_png_case_sensitive_page_marker():
    text = "weird.PNG\nnotreallyapage\npage1.png\nsize: 10,10\nregion\nbounds: 0, 0, 1, 1\n"
    doc = AtlasDocument.parse(text)
    # "weird.PNG" does not end in exactly ".png" (case-sensitive), so it's
    # not a page header, nor a region (no page open yet).
    assert len(doc.pages) == 1
    assert doc.pages[0].filename == "page1.png"


def test_empty_document_parses_to_zero_pages():
    doc = AtlasDocument.parse("")
    assert doc.pages == []
    assert doc.serialize() == ""


def test_legacy_xy_size_region_keys_still_supported():
    text = "page1.png\nsize: 100,100\nleg\nxy: 3, 4\nsize: 20, 30\n"
    doc = AtlasDocument.parse(text)
    leg = doc.pages[0].regions[0]
    assert (leg.x, leg.y, leg.w, leg.h) == (3, 4, 20, 30)


def test_malformed_known_region_keys_fall_through_to_extra_pairs():
    text = (
        "page1.png\nsize: 100,100\nleg\nbounds: 0, 0, 10, 10\n"
        "offsets: 1, 2\nsplit: 1, 2\npad: 1, 2\n"
    )
    doc = AtlasDocument.parse(text)
    leg = doc.pages[0].regions[0]
    assert leg.offsets is None
    assert leg.split is None
    assert leg.pad is None
    assert leg.extra_pairs == [
        ("offsets", ["1", "2"]),
        ("split", ["1", "2"]),
        ("pad", ["1", "2"]),
    ]


def test_malformed_guarded_page_key_filter_silently_dropped():
    text = "page1.png\nsize: 100,100\nfilter: Nearest\nleg\nbounds: 0, 0, 10, 10\n"
    doc = AtlasDocument.parse(text)
    page = doc.pages[0]
    assert page.filter == ["Nearest", "Nearest"]
    assert page.extra_pairs == []


def test_fixture_opaque_transparent_matches_live_js_oracle_output():
    text = (FIXTURES_DIR / "opaque-transparent" / "fixture-opaque.atlas").read_text()
    got = AtlasDocument.parse(text).serialize()
    expected = "\n".join([
        "fixture-opaque.png",
        "size: 32,10",
        "plain",
        "  bounds: 0, 0, 10, 10",
        "withOffsets",
        "  bounds: 10, 0, 8, 6",
        "  offsets: 2, 2, 16, 10",
        "dupeA",
        "  bounds: 20, 0, 6, 6",
        "dupeB",
        "  bounds: 26, 0, 6, 6",
    ])
    assert got == expected


def test_fixture_tie_rounding_matches_live_js_oracle_output():
    text = (FIXTURES_DIR / "tie-rounding" / "fixture-tie.atlas").read_text()
    got = AtlasDocument.parse(text).serialize()
    expected = "\n".join([
        "fixture-tie.png",
        "size: 14,14",
        "tieRegion",
        "  bounds: 0, 0, 6, 6",
        "  offsets: 0, 0, 7, 7",
    ])
    assert got == expected
