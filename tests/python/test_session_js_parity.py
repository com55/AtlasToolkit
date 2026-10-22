"""Python parity against the committed real-Chromium Stage 7 oracle."""
import io
import json
from dataclasses import asdict
from pathlib import Path

from PIL import Image
import pytest

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.domain.effective_model import derive_effective_model
from atlas_toolkit.engine.extraction import AtlasProcessor
from atlas_toolkit.engine.modifier import AtlasModifier, region_meta
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

FIXTURE = Path(__file__).parents[1] / "fixtures" / "session_js_oracle.json"
if not FIXTURE.is_file():
    raise AssertionError(f"required browser oracle fixture missing: {FIXTURE}")
ORACLE = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = ORACLE["cases"]


def pixels_equal(actual_size, actual, expected_size, expected):
    """Exact pixels except RGB hidden beneath alpha zero on either side."""
    actual_size, expected_size = tuple(actual_size), tuple(expected_size)
    if actual_size != expected_size or actual_size[0] <= 0 or actual_size[1] <= 0:
        return False
    expected_length = actual_size[0] * actual_size[1] * 4
    if len(actual) != expected_length or len(expected) != expected_length:
        return False
    for offset in range(0, expected_length, 4):
        actual_alpha, expected_alpha = actual[offset + 3], expected[offset + 3]
        if actual_alpha != expected_alpha:
            return False
        if actual_alpha and actual[offset:offset + 3] != expected[offset:offset + 3]:
            return False
    return True


def _png_handle(raster, width, height, rgba):
    stream = io.BytesIO()
    Image.frombytes("RGBA", (width, height), bytes(rgba)).save(stream, format="PNG")
    return raster.decode_png(stream.getvalue())


def _source_assertions(params):
    a, b = params["sources"]["a"], params["sources"]["b"]
    assert len(a) == len(b) == 8 * 4 * 4
    variant = params["variant"]
    for y in range(4):
        left_a, left_b = a[y * 32:y * 32 + 16], b[y * 32:y * 32 + 16]
        right_a, right_b = a[y * 32 + 16:y * 32 + 32], b[y * 32 + 16:y * 32 + 32]
        if variant == "changed_visible" and y == 0:
            assert left_a[0] + 1 == left_b[0]
            assert left_a[1:] == left_b[1:]
        else:
            assert left_a == left_b
        assert right_a[:3] != right_b[:3]
        if variant == "hidden_unmasked":
            assert right_a[3::4] == right_b[3::4] == [0, 0, 0, 0]
            assert params["source_readback"]["a"][y * 32 + 16:y * 32 + 32] == params["source_readback"]["b"][y * 32 + 16:y * 32 + 32]
        else:
            assert right_a[3::4] == right_b[3::4] == [255, 255, 255, 255]
    identity = params["source_identity"]
    assert identity["same_left"] is (variant != "changed_visible")
    assert identity["different_right"] is True
    assert identity["right_alpha"] == [0 if variant == "hidden_unmasked" else 255] * 16
    assert pixels_equal((8, 4), params["source_readback"]["a"], (8, 4), params["source_readback"]["b"]) is (variant == "hidden_unmasked")


def _run_repack(case):
    params = case["params"]
    raster = PillowRasterBackend()
    base = _png_handle(raster, *params["source_size"], params["source_rgba"])
    document = AtlasDocument.parse(params["atlas"])
    modifier = AtlasModifier(document, base, raster, "p.png")
    mesh_lookup = (lambda _name: params["mesh"]) if params["mesh_enabled"] else None
    nest = {"enabled": params["nest_enabled"], "gap_distance": params["gap_distance"]}

    model = None
    if params["route"] == "rename":
        batch = params["batch"]
        metadata = {key: region_meta(region) for key, region in modifier.regions.items()}
        model = derive_effective_model(metadata, [{
            "type": "rename",
            "target_key": batch["targetKey"],
            "new_atlas_name": batch["newAtlasName"],
        }])
        result = modifier.repack_with_effective_model(
            model["region_names"], model["regions"], {}, {},
            mesh_lookup_fn=mesh_lookup, nest_options=nest,
        )
    else:
        assert params["route"] == "pixel"
        result = modifier.repack_with_modded_sprites(
            {}, mesh_lookup_fn=mesh_lookup, nest_options=nest,
        )
    return raster, result, model


def test_fixture_is_complete_browser_generated_schema():
    assert ORACLE["schema"] == 1
    assert len(CASES) == 44
    assert sum(case["kind"] == "extract" for case in CASES.values()) == 13
    assert sum(case["kind"] == "preview" for case in CASES.values()) == 1
    assert sum(case["kind"] == "repack" for case in CASES.values()) == 16
    assert sum(case["kind"] == "session_read" for case in CASES.values()) == 3
    assert sum(case["kind"] == "session_pixel" for case in CASES.values()) == 2
    assert sum(case["kind"] == "session_structural" for case in CASES.values()) == 5
    assert sum(case["kind"] == "session_mesh_nest" for case in CASES.values()) == 2
    assert sum(case["kind"] == "session_multipage" for case in CASES.values()) == 2


def test_pixel_comparator_literal_contract_and_invalid_lengths():
    assert pixels_equal((1, 1), bytes([1, 2, 3, 0]), (1, 1), bytes([9, 8, 7, 0]))
    assert not pixels_equal((1, 1), bytes([1, 2, 3, 0]), (1, 1), bytes([1, 2, 3, 1]))
    assert not pixels_equal((1, 1), bytes([1, 2, 3, 1]), (1, 1), bytes([9, 2, 3, 1]))
    assert not pixels_equal((1, 1), bytes([1, 2, 3, 255]), (1, 2), bytes([1, 2, 3, 255]))
    assert not pixels_equal((0, 0), b"", (0, 0), b"")
    assert not pixels_equal((1, 1), b"", (1, 1), b"")
    assert not pixels_equal((1, 1), bytes(3), (1, 1), bytes(4))


EXTRACTION_CASES = [key for key, case in CASES.items() if case["kind"] == "extract"]


@pytest.mark.parametrize("case_id", EXTRACTION_CASES, ids=EXTRACTION_CASES)
def test_extraction_case_matches_browser_pixels(case_id):
    case = CASES[case_id]
    params, expected = case["params"], case["output"]
    raster = PillowRasterBackend()
    source = _png_handle(raster, *params["source_size"], params["source_rgba"])
    document = AtlasDocument.parse(params["atlas"])
    processor = AtlasProcessor(document, {"p.png": source}, raster)
    if params["mesh"]:
        processor.set_mesh_mask_data({"r": params["mesh"]}, True, False)
    actual = processor.extract_region("r")
    assert actual is not None
    assert pixels_equal(raster.size(actual), raster.get_pixels(actual),
                        (expected["width"], expected["height"]), bytes(expected["rgba"])), case_id


def test_opaque_reverse_preview_matches_browser():
    case = next(case for case in CASES.values() if case["kind"] == "preview")
    params, expected = case["params"], case["output"]
    raster = PillowRasterBackend()
    source = _png_handle(raster, *params["source_size"], params["source_rgba"])
    processor = AtlasProcessor(AtlasDocument.parse(params["atlas"]), {"p.png": source}, raster)
    actual = processor.get_preview_image(params["names"])
    assert actual is not None
    assert pixels_equal(raster.size(actual), raster.get_pixels(actual),
                        (expected["width"], expected["height"]), bytes(expected["rgba"]))


REPACK_CASES = [key for key, case in CASES.items() if case["kind"] == "repack"]


@pytest.mark.parametrize("case_id", REPACK_CASES, ids=REPACK_CASES)
def test_repack_case_matches_browser_pixels_geometry_and_text(case_id):
    case = CASES[case_id]
    params, expected = case["params"], case["output"]
    _source_assertions(params)
    raster, actual, model = _run_repack(case)
    assert pixels_equal(raster.size(actual.image), raster.get_pixels(actual.image),
                        (expected["width"], expected["height"]), bytes(expected["rgba"])), case_id
    assert actual.region_bounds == expected["bounds"], case_id
    assert list(actual.region_bounds) == expected["region_order"], case_id
    assert actual.document.serialize() == expected["atlas_text"], case_id
    if model is None:
        assert expected["effective_order"] is None
    else:
        assert model["region_names"] == expected["effective_order"], case_id
        labels = [region.atlas_name for region in actual.document.pages[0].regions]
        assert labels == expected["labels"], case_id
        assert actual.region_bounds["b"]  # stable internal key survives Rename to bee

    alpha = raster.get_pixels(actual.image)[3::4]
    assert any(value == 255 for value in alpha), case_id
    if params["variant"] in ("masked", "hidden_unmasked"):
        assert any(value == 0 for value in alpha), case_id


def test_masked_and_hidden_pair_dedup_but_controls_do_not():
    for case in CASES.values():
        if case["kind"] != "repack":
            continue
        params, bounds = case["params"], case["output"]["bounds"]
        same_bounds = bounds["a"] == bounds["b"]
        assert same_bounds is (params["variant"] in ("masked", "hidden_unmasked"))
        if params["route"] == "rename":
            assert case["output"]["effective_order"] == ["a", "b"]
            assert case["output"]["labels"] == ["a", "bee"]


def _assert_png_matches_record(payload, expected):
    with Image.open(io.BytesIO(payload)) as image:
        image = image.convert("RGBA")
        assert pixels_equal(image.size, image.tobytes(),
                            (expected["width"], expected["height"]), bytes(expected["rgba"]))


@pytest.mark.parametrize("case_id", ["session_two_pages", "session_scaled", "session_old_format"])
def test_session_read_export_matches_browser(case_id):
    # Removing conversion, choosing upload-map order, using raw bounds or labels,
    # or exporting scaled metadata breaks these real public-API comparisons.
    from atlas_toolkit.engine.session import AtlasSession

    params, expected = CASES[case_id]["params"], CASES[case_id]["output"]
    sources = {"extra.png": b"ignored corrupt extra"}
    for source in params["sources"]:
        stream = io.BytesIO()
        Image.frombytes("RGBA", (source["width"], source["height"]),
                        bytes(source["rgba"])).save(stream, format="PNG")
        sources[source["filename"]] = stream.getvalue()
    session = AtlasSession.load(params["atlas"], sources, PillowRasterBackend())
    try:
        assert json.loads(json.dumps(asdict(session.snapshot()))) == expected["snapshot"]
        extracts = session.extract_regions(params["names"])
        assert list(extracts) == list(expected["extracts"])
        for key, payload in extracts.items():
            _assert_png_matches_record(payload, expected["extracts"][key])
        assert session.extract_regions([]) == {}
        assert session.get_preview_png([]) == session.get_preview_png(["missing"]) == b""
        _assert_png_matches_record(session.get_preview_png(), expected["default_preview"])
        _assert_png_matches_record(session.get_preview_png(params["selection"]), expected["selection_preview"])
        text, pages = session.save()
        assert text == expected["saved_text"]
        assert list(pages) == list(expected["saved_pages"])
        for filename, payload in pages.items():
            _assert_png_matches_record(payload, expected["saved_pages"][filename])
        assert json.loads(json.dumps(asdict(session.snapshot()))) == expected["after_save"]
        session.clear_modify_state()
        assert json.loads(json.dumps(asdict(session.snapshot()))) == expected["after_clear"]
    finally:
        session.close()
    for filename, payload in pages.items():
        _assert_png_matches_record(payload, expected["saved_pages"][filename])


def _record_png(record):
    stream = io.BytesIO()
    Image.frombytes("RGBA", (record["width"], record["height"]),
                    bytes(record["rgba"])).save(stream, format="PNG")
    return stream.getvalue()


@pytest.mark.parametrize("case_id", [
    "session_structural_numeric", "session_structural_scaled",
    "session_structural_last_remove_shelf", "session_structural_last_remove_nest",
    "session_cross_slice",
])
def test_structural_transaction_sequence_matches_real_browser(case_id):
    from atlas_toolkit.engine.session import AtlasSession

    params, expected = CASES[case_id]["params"], CASES[case_id]["output"]
    raster = PillowRasterBackend()
    session = AtlasSession.load(params["atlas"], {"p.png": _record_png(params["source"])}, raster)
    # Existing processor seam exercises both packers without adding Task5 APIs.
    session._processor.set_nest_options(params["nest"], 4)
    try:
        for step in expected["steps"]:
            action, result, saved = step["action"], None, None
            op = action["op"]
            if op == "add":
                assert session.add_region(_record_png(params["sources"][action["source"]]), action["name"]) is None
            elif op == "rename":
                assert session.rename_region(action["key"], action["name"]) is None
            elif op == "remove":
                assert session.remove_regions([action["key"]]) is None
            elif op == "remove_last":
                assert step["rejected"] is True
                before = session.snapshot()
                with pytest.raises(ValueError, match="^Repack output must have positive dimensions\\.$"):
                    session.remove_regions([action["key"]])
                assert session.snapshot() == before
            elif op == "apply":
                source = params["mod"] if action["source"] == "mod" else params["sources"][action["source"]]
                result = session.apply_mod_image(_record_png(source), action["names"])
            elif op == "repack":
                result = session.repack()
            elif op == "data":
                session.set_mesh_data({"a": params["mesh"]} if action["value"] else None)
            elif op == "crop":
                result = session.set_mesh_mask_enabled(action["value"])
            elif op == "mesh":
                result = session.set_mesh_aware_repack_enabled(action["value"])
            elif op == "nest":
                result = session.set_nest_regions_enabled(action["value"])
            elif op == "gap":
                result = session.set_nest_gap_distance(action["value"])
            elif op == "save":
                saved = session.save()
            elif op == "clear":
                session.clear_modify_state()
            else:
                assert op == "initial"
            assert json.loads(json.dumps(asdict(session.snapshot()))) == step["snapshot"], (case_id, action)
            if step.get("result") is not None:
                want = step["result"]
                snapshot = session.snapshot()
                assert {key: list(bounds) for key, bounds in snapshot.modify_region_bounds.items()} == want["regions"]
                assert {region.key: region.page_filename for region in snapshot.regions} == want["region_pages"]
                assert list(snapshot.pages) == want["pages"]
                if op in {"add", "rename", "remove"}:
                    assert result is None  # Python structural methods publish state and return None.
                else:
                    assert result is not None, (case_id, action)
                    assert {key: list(bounds) for key, bounds in result.regions.items()} == want["regions"]
                    assert result.region_pages == want["region_pages"]
                    assert list(result.pages) == want["pages"]
                    image = want["image"]
                    assert pixels_equal(raster.size(result.image), raster.get_pixels(result.image),
                                        (image["width"], image["height"]), bytes(image["rgba"]))
                _assert_png_matches_record(session.get_preview_png(), want["image"])
            if step["saved"] is not None:
                text, pages = saved
                assert text == step["saved"]["text"]
                assert list(pages) == list(step["saved"]["pages"])
                for key, payload in pages.items():
                    _assert_png_matches_record(payload, step["saved"]["pages"][key])
            _assert_png_matches_record(session.get_preview_png(), step["default_preview"])
            _assert_png_matches_record(session.get_preview_png(params["selection"]), step["selection_preview"])
            extracts = session.extract_regions(step["read_names"])
            assert list(extracts) == list(step["extracts"])
            for key, payload in extracts.items():
                _assert_png_matches_record(payload, step["extracts"][key])
    finally:
        session.close()


@pytest.mark.parametrize("case_id", ["session_mesh_nest_pixel", "session_mesh_nest_structural"])
def test_mesh_nest_sequence_matches_real_browser(case_id):
    from atlas_toolkit.engine.session import AtlasSession

    params, expected = CASES[case_id]["params"], CASES[case_id]["output"]
    raster = PillowRasterBackend()
    session = AtlasSession.load(params["atlas"], {"p.png": _record_png(params["source"])}, raster)
    setters = {
        "data": "set_mesh_data", "crop": "set_mesh_mask_enabled",
        "mesh": "set_mesh_aware_repack_enabled", "nest": "set_nest_regions_enabled",
        "gap": "set_nest_gap_distance",
    }
    try:
        for step in expected["steps"]:
            action, result, saved = step["action"], None, None
            op = action["op"]
            if op in setters:
                result = getattr(session, setters[op])(action["value"])
            elif op == "pixel":
                result = session.apply_mod_image(_record_png(params["mod"]), ["a"])
            elif op == "structural":
                assert session.rename_region("a", "renamed") is None
            elif op == "repack":
                result = session.repack()
            elif op == "save":
                saved = session.save()
            elif op == "clear":
                session.clear_modify_state()
            else:
                assert op == "initial"
            assert json.loads(json.dumps(asdict(session.snapshot()))) == step["snapshot"], (case_id, action)
            if step["result"] is not None:
                want = step["result"]
                if op != "structural":  # Python structural API returns None by contract.
                    assert result is not None
                    assert {key: list(bounds) for key, bounds in result.regions.items()} == want["regions"]
                    assert result.region_pages == want["region_pages"]
                    assert list(result.pages) == want["pages"]
                    record = want["image"]
                    assert pixels_equal(raster.size(result.image), raster.get_pixels(result.image),
                                        (record["width"], record["height"]), bytes(record["rgba"]))
            else:
                assert result is None
            if step["saved"] is not None:
                text, pages = saved
                assert text == step["saved"]["text"]
                assert list(pages) == list(step["saved"]["pages"])
                for key, payload in pages.items():
                    _assert_png_matches_record(payload, step["saved"]["pages"][key])
            _assert_png_matches_record(session.get_preview_png(), step["default_preview"])
            _assert_png_matches_record(session.get_preview_png(params["selection"]), step["selection_preview"])
            extracts = session.extract_regions(params["names"])
            assert list(extracts) == list(step["extracts"])
            for key, payload in extracts.items():
                _assert_png_matches_record(payload, step["extracts"][key])
    finally:
        session.close()


@pytest.mark.parametrize("case_id", ["session_pixel_overlap", "session_pixel_full_canvas"])
def test_pixel_transaction_sequence_matches_real_browser(case_id):
    from atlas_toolkit.engine.session import AtlasSession

    params, expected = CASES[case_id]["params"], CASES[case_id]["output"]
    raster = PillowRasterBackend()
    session = AtlasSession.load(params["atlas"], {"p.png": _record_png(params["source"])}, raster)
    try:
        for step in expected["steps"]:
            action, result, saved = step["action"], None, None
            if action["op"] == "apply":
                mod = params["mods"][action["mod"]]
                result = session.apply_mod_image(_record_png(mod), mod["names"])
            elif action["op"] == "repack":
                result = session.repack()
            elif action["op"] == "save":
                saved = session.save()
            elif action["op"] == "empty":
                assert session.apply_mod_image(b"not a PNG", []) is None
            elif action["op"] == "clear":
                session.clear_modify_state()
            else:
                assert action["op"] == "initial"
            assert json.loads(json.dumps(asdict(session.snapshot()))) == step["snapshot"], (case_id, action)
            if step["result"] is not None:
                want = step["result"]
                assert result is not None
                assert {key: list(bounds) for key, bounds in result.regions.items()} == want["regions"]
                assert result.region_pages == want["region_pages"]
                assert list(result.pages) == want["pages"]
                image = want["image"]
                assert pixels_equal(raster.size(result.image), raster.get_pixels(result.image),
                                    (image["width"], image["height"]), bytes(image["rgba"]))
            if step["saved"] is not None:
                text, pages = saved
                assert text == step["saved"]["text"]
                assert list(pages) == list(step["saved"]["pages"])
                for key, payload in pages.items():
                    _assert_png_matches_record(payload, step["saved"]["pages"][key])
            _assert_png_matches_record(session.get_preview_png(), step["default_preview"])
            _assert_png_matches_record(session.get_preview_png(params["selection"]), step["selection_preview"])
            extracts = session.extract_regions(list(step["extracts"]))
            assert list(extracts) == list(step["extracts"])
            for key, payload in extracts.items():
                _assert_png_matches_record(payload, step["extracts"][key])
    finally:
        session.close()


@pytest.mark.parametrize("case_id", ["session_multipage_shelf", "session_multipage_nest"])
def test_multi_page_replace_matches_real_browser(case_id):
    """Spec 2026-10-21 §3.10: deferred multi-page replace equals the app's
    _rebuildMultiPageRepack -- text after parse/serialize, pages as pixels."""
    from atlas_toolkit.engine.session import AtlasSession

    params, expected = CASES[case_id]["params"], CASES[case_id]["output"]
    session = AtlasSession.load(
        params["atlas"], {source["filename"]: _record_png(source) for source in params["sources"]},
        PillowRasterBackend(), defer_repack=True,
    )
    try:
        if params["nest"]:
            session.set_nest_regions_enabled(True)
            session.set_nest_gap_distance(0)
        for mod in params["mods"]:
            session.apply_mod_image(_record_png(mod), mod["names"])
        text, pages = session.pack_pending()
        assert AtlasDocument.parse(text).serialize() == AtlasDocument.parse(expected["text"]).serialize()
        assert list(pages) == list(expected["pages"])
        for filename, image in pages.items():
            want = expected["pages"][filename]
            image = image.convert("RGBA")
            assert pixels_equal(image.size, image.tobytes(), (want["width"], want["height"]), bytes(want["rgba"])), filename
    finally:
        session.close()
