"""Public read/export behavior for the Stage 7 session slice."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from atlas_toolkit.raster.pillow_backend import PillowRasterBackend


def png(width: int, height: int, color: tuple[int, int, int, int]) -> bytes:
    stream = io.BytesIO()
    Image.new("RGBA", (width, height), color).save(stream, format="PNG")
    return stream.getvalue()


ATLAS = """first.png
size: 4, 3
10
 bounds: 0, 0, 2, 1
same
 bounds: 2, 0, 2, 1
second.png
size: 2, 5
same
 bounds: 0, 1, 2, 2
"""


def session():
    from atlas_toolkit.engine.session import AtlasSession

    return AtlasSession.load(
        ATLAS,
        {"second.png": png(2, 5, (0, 0, 255, 255)), "first.png": png(4, 3, (255, 0, 0, 255))},
        PillowRasterBackend(),
    )


def decoded(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image.convert("RGBA")


def test_load_snapshot_extract_preview_save_and_close_are_pristine():
    loaded = session()
    snapshot = loaded.snapshot()
    assert snapshot.pages == ("first.png", "second.png")
    assert snapshot.is_multi_page is True
    assert [(item.key, item.label, item.page_filename) for item in snapshot.regions] == [
        ("10", "10", "first.png"), ("same", "same", "first.png"), ("same#2", "same#2", "second.png"),
    ]
    assert snapshot.modified_keys == ()
    assert not snapshot.has_pending_modifications and not snapshot.has_output
    assert snapshot.generation == 0
    assert snapshot.mesh_available is False and snapshot.mesh_mask_enabled is False
    assert snapshot.mesh_aware_repack_enabled is False
    assert snapshot.nest_regions_enabled is False and snapshot.nest_gap_distance == 4
    assert snapshot.modify_region_bounds == {"10": (0, 0, 2, 1, 0), "same": (2, 0, 2, 1, 0), "same#2": (0, 1, 2, 2, 0)}
    with pytest.raises(AttributeError):
        snapshot.generation = 1
    with pytest.raises(AttributeError):
        snapshot.regions[0].key = "changed"
    snapshot.modify_region_bounds["10"] = (99, 99, 99, 99, 0)
    assert loaded.snapshot().modify_region_bounds["10"] == (0, 0, 2, 1, 0)

    assert loaded.extract_regions([]) == {}
    extracted = loaded.extract_regions(["missing", "same#2", "10"])
    assert list(extracted) == ["same#2", "10"]
    assert decoded(extracted["same#2"]).size == (2, 2)
    assert decoded(loaded.get_preview_png()).size == (4, 3)
    assert decoded(loaded.get_preview_png(["same", "10"])).size == (2, 1)
    text, pages = loaded.save()
    assert text == "first.png\nsize: 4,3\n10\n  bounds: 0, 0, 2, 1\nsame\n  bounds: 2, 0, 2, 1\n\nsecond.png\nsize: 2,5\nsame\n  bounds: 0, 1, 2, 2\n"
    assert list(pages) == ["first.png", "second.png"]
    assert decoded(pages["first.png"]).getpixel((0, 0)) == (255, 0, 0, 255)
    assert loaded.snapshot().has_output is False
    loaded.clear_modify_state()
    loaded.close()
    assert decoded(pages["second.png"]).size == (2, 5)
    loaded.close()
    for method in (loaded.snapshot, loaded.save, loaded.clear_modify_state, lambda: loaded.extract_regions([]), loaded.get_preview_png):
        with pytest.raises(RuntimeError, match="closed"):
            method()


def test_load_converts_once_validates_all_files_before_decode_and_ignores_extras(monkeypatch):
    import atlas_toolkit.engine.session as module

    calls: list[str] = []
    original = module.AtlasDocument.parse

    def parse(text):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(module.AtlasDocument, "parse", staticmethod(parse))
    with pytest.raises(ValueError, match="a.png.*b.png"):
        module.AtlasSession.load("a.png\nsize: 1,1\nb.png\nsize: 1,1", {}, PillowRasterBackend())
    assert calls == ["a.png\nsize: 1,1\nb.png\nsize: 1,1"]

    loaded = module.AtlasSession.load(
        "p.png\nsize: 1,1\nlegacy\n xy: 0, 0\n size: 1, 1",
        {"p.png": png(1, 1, (1, 2, 3, 4)), "bad.png": b"bad"},
        PillowRasterBackend(),
    )
    assert calls == [
        "a.png\nsize: 1,1\nb.png\nsize: 1,1",
        "p.png\nsize: 1,1\nlegacy\n  rotate: false\n  bounds: 0, 0, 1, 1",
    ]
    loaded.snapshot()
    loaded.save()
    assert len(calls) == 2
    loaded.close()


def test_no_pages_and_scaled_snapshot_bounds_preserve_raw_saved_metadata():
    from atlas_toolkit.engine.session import AtlasSession

    with pytest.raises(ValueError, match="page"):
        AtlasSession.load("region\n bounds: 0,0,1,1", {}, PillowRasterBackend())
    loaded = AtlasSession.load("p.png\nsize: 4, 6\nr\n bounds: 1, 2, 2, 2", {"p.png": png(8, 3, (1, 2, 3, 255))}, PillowRasterBackend())
    assert loaded.snapshot().modify_region_bounds == {"r": (2, 1, 4, 1, 0)}
    text, _ = loaded.save()
    assert "size: 4,6" in text and "bounds: 1, 2, 2, 2" in text
    loaded.close()


def _pattern_png(width, height, seed):
    rgba = bytes(
        channel
        for y in range(height)
        for x in range(width)
        for channel in ((seed + x * 31) % 256, (seed + y * 47) % 256, (seed + x + y) % 256, 255)
    )
    stream = io.BytesIO()
    Image.frombytes("RGBA", (width, height), rgba).save(stream, format="PNG")
    return stream.getvalue()


PIXEL_ATLAS = """p.png
size: 8, 2
a
 bounds: 0, 0, 2, 2
b
 bounds: 2, 0, 2, 2
c
 bounds: 4, 0, 2, 2
d
 bounds: 6, 0, 2, 2
"""


def _pixel_session(raster=None):
    from atlas_toolkit.engine.session import AtlasSession

    backend = raster or PillowRasterBackend()
    return AtlasSession.load(
        PIXEL_ATLAS,
        {"p.png": _pattern_png(8, 2, 11)},
        backend,
    ), backend


def _saved_region_pixels(saved_pages, result, name):
    page_name = result.region_pages[name]
    x, y, width, height, _rotate = result.regions[name]
    return decoded(saved_pages[page_name]).crop((x, y, x + width, y + height)).tobytes()


def test_sequential_overlapping_mods_are_latest_wins_and_saved_pixels_are_real():
    loaded, raster = _pixel_session()
    first_selection = ["a", "b"]
    first = loaded.apply_mod_image(_pattern_png(2, 2, 70), first_selection)
    first_selection[:] = ["d"]
    latest_b = _pattern_png(2, 2, 170)
    second = loaded.apply_mod_image(latest_b, ["b", "c"])

    assert loaded.snapshot().generation == 2
    assert loaded.snapshot().modified_keys == ("a", "b", "c")
    _text, saved = loaded.save()
    assert _saved_region_pixels(saved, second, "a") == decoded(_pattern_png(2, 2, 70)).tobytes()
    assert _saved_region_pixels(saved, second, "b") == decoded(latest_b).tobytes()
    assert _saved_region_pixels(saved, second, "c") == decoded(latest_b).tobytes()
    assert _saved_region_pixels(saved, second, "d") == decoded(_pattern_png(8, 2, 11)).crop((6, 0, 8, 2)).tobytes()
    loaded.close()


def test_full_canvas_union_preserves_historical_reset_and_untouched_offsets(monkeypatch):
    from atlas_toolkit.engine.modifier import AtlasModifier
    from atlas_toolkit.engine.session import AtlasSession

    atlas = """p.png
size: 8, 4
a
 bounds: 0, 0, 2, 2
 offsets: 0, 0, 160, 80
b
 bounds: 2, 0, 2, 2
 offsets: 7, 5, 160, 80
"""
    raster = PillowRasterBackend()
    loaded = AtlasSession.load(atlas, {"p.png": _pattern_png(8, 4, 23)}, raster)
    prepared_flags = []
    original_prepare = AtlasModifier.prepare_mod_image

    def record_prepare(self, source, names):
        prepared = original_prepare(self, source, names)
        prepared_flags.append((prepared.is_full_canvas, prepared.shared_canvas_mod))
        return prepared

    monkeypatch.setattr(AtlasModifier, "prepare_mod_image", record_prepare)
    loaded.apply_mod_image(_pattern_png(160, 80, 41), ["a"])
    second = loaded.apply_mod_image(_pattern_png(80, 43, 89), ["a"])
    assert prepared_flags == [(True, False), (False, False)]

    text, saved = loaded.save()
    from atlas_toolkit.document import AtlasDocument

    output = AtlasDocument.parse(text)
    regions = output.regions_by_key()
    assert regions["a"].offsets is None
    assert regions["b"].offsets == [7, 5, 160, 80]
    saved_b = decoded(saved[second.region_pages["b"]])
    bx, by, bw, bh, _ = second.regions["b"]
    assert saved_b.crop((bx, by, bx + bw, by + bh)).tobytes() == (
        decoded(_pattern_png(8, 4, 23)).crop((2, 0, 4, 2)).tobytes()
    )
    loaded.close()


def test_generation_saved_state_preview_and_repack_prepare_once(monkeypatch):
    from atlas_toolkit.engine.modifier import AtlasModifier

    loaded, raster = _pixel_session()
    assert loaded.snapshot().generation == 0
    assert not loaded.snapshot().has_pending_modifications
    selection = ["a"]
    loaded.apply_mod_image(_pattern_png(2, 2, 33), selection)
    selection[:] = ["d"]
    assert loaded.snapshot().generation == 1
    assert loaded.snapshot().has_pending_modifications

    calls = []
    original_prepare = AtlasModifier.prepare_mod_image

    def count_prepare(self, source, names):
        calls.append(tuple(names))
        return original_prepare(self, source, names)

    monkeypatch.setattr(AtlasModifier, "prepare_mod_image", count_prepare)
    first_result = loaded.repack()
    first_result_size = raster.size(first_result.image)
    # repack() reruns the same batches: generation is unchanged (v0.4.0 JS).
    assert loaded.snapshot().generation == 1
    text, pages = loaded.save()
    assert not loaded.snapshot().has_pending_modifications
    preview = decoded(loaded.get_preview_png())
    assert preview.tobytes() == decoded(pages["p.png"]).tobytes()
    assert preview.tobytes() == decoded(raster.encode_png(first_result.image)).tobytes()
    pristine = loaded.extract_regions(["a"])["a"]
    assert decoded(pristine).tobytes() == decoded(_pattern_png(8, 2, 11)).crop((0, 0, 2, 2)).tobytes()

    second_result = loaded.repack()
    assert loaded.snapshot().generation == 1
    assert not loaded.snapshot().has_pending_modifications
    assert len(calls) == 0
    assert raster.size(second_result.image) == first_result_size
    with pytest.raises(KeyError):
        raster.size(first_result.image)
    loaded.apply_mod_image(_pattern_png(2, 2, 51), ["c"])
    assert loaded.snapshot().has_pending_modifications
    assert text and pages["p.png"]
    loaded.close()

    empty, empty_raster = _pixel_session()
    zero_batch_result = empty.repack()
    assert empty.snapshot().has_output
    assert not empty.snapshot().has_pending_modifications
    assert empty_raster.size(zero_batch_result.image)[0] > 0
    empty.close()


def test_result_metadata_is_frozen_defensive_and_borrowed_image_lifetime():
    loaded, raster = _pixel_session()
    result = loaded.apply_mod_image(_pattern_png(2, 2, 61), ["a"])
    original_regions = dict(result.regions)
    original_pages = dict(result.region_pages)
    with pytest.raises((AttributeError, TypeError)):
        result.image = None
    original_snapshot = loaded.snapshot()
    result.regions["a"] = (99, 99, 1, 1, 0)
    result.region_pages["a"] = "wrong.png"
    after_mutation = loaded.snapshot()
    assert after_mutation.modify_region_bounds == original_snapshot.modify_region_bounds
    assert next(region for region in after_mutation.regions if region.key == "a").page_filename == original_pages["a"]
    saved_text, saved_before_repack = loaded.save()
    assert set(saved_before_repack) == set(original_snapshot.pages)
    from atlas_toolkit.document import AtlasDocument

    saved_doc = AtlasDocument.parse(saved_text)
    assert tuple(saved_doc.regions_by_key()["a"].bounds_with_rotate()) == original_regions["a"]

    borrowed = loaded.repack()
    saved_size = raster.size(borrowed.image)
    _text, saved = loaded.save()
    saved_bytes = saved["p.png"]
    replacement = loaded.repack()
    with pytest.raises(KeyError):
        raster.size(borrowed.image)
    raster.size(replacement.image)
    loaded.clear_modify_state()
    loaded.close()
    assert decoded(saved_bytes).size == saved_size


def test_guards_empty_unknown_mult_page_and_closed_session():
    from atlas_toolkit.engine.session import AtlasSession

    loaded, _raster = _pixel_session()
    before = loaded.snapshot()
    assert loaded.apply_mod_image(b"not a png", []) is None
    assert loaded.snapshot() == before
    unknown = loaded.apply_mod_image(_pattern_png(2, 2, 73), ["unknown"])
    assert unknown is not None
    assert loaded.snapshot().generation == 1
    assert loaded.snapshot().modified_keys == ()
    loaded.close()
    for operation in (
        lambda: loaded.apply_mod_image(b"", []),
        loaded.repack,
    ):
        with pytest.raises(RuntimeError, match="closed"):
            operation()

    class RecordingRaster(PillowRasterBackend):
        def __init__(self):
            super().__init__()
            self.decode_calls = 0
            self.create_calls = 0

        def decode_png(self, data):
            self.decode_calls += 1
            return super().decode_png(data)

        def create(self, width, height):
            self.create_calls += 1
            return super().create(width, height)

    backend = RecordingRaster()
    multi = AtlasSession.load(
        ATLAS,
        {"first.png": png(4, 3, (1, 2, 3, 255)), "second.png": png(2, 5, (4, 5, 6, 255))},
        backend,
    )
    decodes_after_load = backend.decode_calls
    creates_after_load = backend.create_calls
    with pytest.raises(NotImplementedError):
        multi.apply_mod_image(b"not a png", ["10"])
    assert backend.decode_calls == decodes_after_load
    assert backend.create_calls == creates_after_load
    with pytest.raises(NotImplementedError):
        multi.repack()
    assert backend.decode_calls == decodes_after_load
    assert backend.create_calls == creates_after_load
    assert multi.apply_mod_image(b"not a png", []) is None
    multi.close()


def test_repack_reuses_prepared_mod_and_clear_close_preserve_foreign_handles(monkeypatch):
    from atlas_toolkit.engine.modifier import AtlasModifier

    loaded, raster = _pixel_session()
    foreign = raster.create(1, 1)
    raster.get_pixels(foreign)
    pristine = decoded(loaded.extract_regions(["a"])["a"]).tobytes()
    calls = []
    original_prepare = AtlasModifier.prepare_mod_image

    def count_prepare(self, source, names):
        calls.append(tuple(names))
        return original_prepare(self, source, names)

    monkeypatch.setattr(AtlasModifier, "prepare_mod_image", count_prepare)
    loaded.apply_mod_image(_pattern_png(2, 2, 101), ["a"])
    first = loaded.repack()
    first_pixels = raster.get_pixels(first.image)
    second = loaded.repack()
    assert len(calls) == 1
    assert raster.get_pixels(second.image) == first_pixels
    assert decoded(loaded.extract_regions(["a"])["a"]).tobytes() == pristine
    loaded.clear_modify_state()
    # clear keeps the counter so a later mod never reuses a seen generation.
    assert loaded.snapshot().generation == 1
    assert decoded(loaded.extract_regions(["a"])["a"]).tobytes() == pristine
    loaded.close()
    assert raster.size(foreign) == (1, 1)


def test_structural_add_rename_remove_saved_pixels_and_pristine_extraction():
    from atlas_toolkit.document import AtlasDocument
    from atlas_toolkit.engine.session import AtlasSession

    atlas = """p.png
size: 4,2
a
 bounds: 0,0,2,2
b
 bounds: 2,0,2,2
"""
    red = png(4, 2, (220, 10, 10, 255))
    green = png(1, 1, (10, 220, 10, 255))
    loaded = AtlasSession.load(atlas, {"p.png": red}, PillowRasterBackend())
    loaded.add_region(green, " new ")
    assert loaded.snapshot().generation == 1
    assert [(r.key, r.label) for r in loaded.snapshot().regions] == [
        ("a", "a"), ("b", "b"), ("new", "new"),
    ]
    assert loaded.extract_regions(["new"]) == {}
    loaded.rename_region("a", "renamed")
    snap = loaded.snapshot()
    assert snap.generation == 2 and "a" in snap.modify_region_bounds
    assert [(r.key, r.label) for r in snap.regions] == [
        ("a", "renamed"), ("b", "b"), ("new", "new"),
    ]
    assert decoded(loaded.extract_regions(["a"])["a"]).tobytes() == (
        decoded(red).crop((0, 0, 2, 2)).tobytes()
    )
    loaded.remove_regions(["b"])
    assert loaded.snapshot().generation == 3
    assert [r.key for r in loaded.snapshot().regions] == ["a", "new"]
    assert decoded(loaded.extract_regions(["b"])["b"]).tobytes() == (
        decoded(red).crop((2, 0, 4, 2)).tobytes()
    )
    text, pages = loaded.save()
    output = AtlasDocument.parse(text)
    assert [(r.name, r.atlas_name) for r in output.pages[0].regions] == [
        ("renamed", "renamed"), ("new", "new"),
    ]
    saved = decoded(pages["p.png"])
    for region in output.pages[0].regions:
        expected = decoded(red).crop((0, 0, 2, 2)) if region.atlas_name == "renamed" else decoded(green)
        actual = saved.crop((region.x, region.y, region.x + region.w, region.y + region.h))
        assert actual.getpixel((0, 0)) == expected.getpixel((0, 0))
    loaded.clear_modify_state()
    assert [(r.key, r.label) for r in loaded.snapshot().regions] == [("a", "a"), ("b", "b")]
    assert loaded.snapshot().generation == 3 and not loaded.snapshot().has_output
    loaded.close()


def test_invalid_add_checks_validation_before_decode():
    import pytest
    from atlas_toolkit.engine.session import AtlasSession

    class CountingRaster(PillowRasterBackend):
        def __init__(self):
            super().__init__()
            self.decode_calls = 0

        def decode_png(self, data):
            self.decode_calls += 1
            return super().decode_png(data)

    raster = CountingRaster()
    loaded = AtlasSession.load(
        "p.png\nsize: 2,2\na\n bounds: 0,0,1,1",
        {"p.png": png(2, 2, (1, 2, 3, 255))}, raster,
    )
    for candidate in ("", "bad:name", "two\nlines", "bad.PNG", "literal#key"):
        before, calls = loaded.snapshot(), raster.decode_calls
        with pytest.raises(ValueError):
            loaded.add_region(b"corrupt", candidate)
        assert raster.decode_calls == calls
        assert loaded.snapshot() == before
    loaded.close()


def test_reserved_add_keys_and_pending_rename_collisions():
    import pytest
    from atlas_toolkit.engine.session import AtlasSession

    atlas = """p.png
size: 3,1
a
 bounds: 0,0,1,1
a_2
 bounds: 1,0,1,1
"""
    loaded = AtlasSession.load(atlas, {"p.png": png(3, 1, (3, 4, 5, 255))}, PillowRasterBackend())
    with pytest.raises(ValueError, match="already in use"):
        loaded.add_region(png(1, 1, (7, 8, 9, 255)), " a ")
    loaded.rename_region("a", " a ")
    loaded.rename_region("a", "renamed")
    with pytest.raises(ValueError, match="already in use"):
        loaded.rename_region("a_2", "renamed")
    loaded.add_region(png(1, 1, (7, 8, 9, 255)), "a")
    assert [(r.key, r.label) for r in loaded.snapshot().regions] == [
        ("a", "renamed"), ("a_2", "a_2"), ("a_3", "a"),
    ]
    loaded.remove_regions(["a_3"])
    loaded.add_region(png(1, 1, (7, 8, 9, 255)), "a")
    assert [r.key for r in loaded.snapshot().regions] == ["a", "a_2", "a_4"]
    loaded.close()


def test_unknown_targets_and_multikey_remove_commit_once():
    from atlas_toolkit.engine.session import AtlasSession

    atlas = """p.png
size: 3,1
a
 bounds: 0,0,1,1
b
 bounds: 1,0,1,1
c
 bounds: 2,0,1,1
"""
    loaded = AtlasSession.load(atlas, {"p.png": png(3, 1, (4, 5, 6, 255))}, PillowRasterBackend())
    loaded.rename_region("unknown", "ignored")
    assert loaded.snapshot().generation == 1
    assert loaded.snapshot().modified_keys == ()
    loaded.remove_regions(["a", "a", "unknown", "b"])
    snapshot = loaded.snapshot()
    assert snapshot.generation == 2
    assert [r.key for r in snapshot.regions] == ["c"]
    assert snapshot.modified_keys == ()
    loaded.close()


def test_numeric_add_order_and_real_dedup_identity():
    from atlas_toolkit.document import AtlasDocument
    from atlas_toolkit.engine.session import AtlasSession

    atlas = """p.png
size: 6,2
10
 bounds: 0,0,2,2
2
 bounds: 2,0,2,2
a
 bounds: 4,0,2,2
"""
    page = Image.new("RGBA", (6, 2))
    page.paste((200, 10, 10, 255), (0, 0, 2, 2))
    page.paste((200, 10, 10, 255), (2, 0, 4, 2))
    page.paste((10, 20, 200, 255), (4, 0, 6, 2))
    stream = io.BytesIO()
    page.save(stream, format="PNG")
    loaded = AtlasSession.load(atlas, {"p.png": stream.getvalue()}, PillowRasterBackend())
    loaded.add_region(png(1, 1, (20, 200, 20, 255)), "1")
    text, _pages = loaded.save()
    output = AtlasDocument.parse(text)
    # Re-sorting the effective list moves numeric Add key "1" to the front.
    assert [r.name for r in output.pages[0].regions] == ["2", "10", "a", "1"]
    assert output.regions_by_key()["2"].bounds_with_rotate() == output.regions_by_key()["10"].bounds_with_rotate()
    assert [r.key for r in loaded.snapshot().regions] == ["2", "10", "a", "1"]
    loaded.close()


def test_nonuniform_scaled_structural_rename_keeps_raw_key_and_metadata():
    from atlas_toolkit.document import AtlasDocument
    from atlas_toolkit.engine.session import AtlasSession

    atlas = """p.png
size: 4,4
a
 bounds: 1,1,2,2
 offsets: 0,0,4,4
 index: 7
 split: 1,2,3,4
 pad: 5,6,7,8
 custom: keep,these
"""
    loaded = AtlasSession.load(atlas, {"p.png": png(8, 4, (11, 22, 33, 255))}, PillowRasterBackend())
    pristine = decoded(loaded.extract_regions(["a"])["a"])
    assert loaded.snapshot().modify_region_bounds["a"][2:4] == (4, 2)
    loaded.rename_region("a", "renamed")
    text, _pages = loaded.save()
    output = AtlasDocument.parse(text)
    region = output.pages[0].regions[0]
    assert (region.name, region.atlas_name) == ("renamed", "renamed")
    assert region.index == 7 and region.split == [1, 2, 3, 4]
    assert region.pad == [5, 6, 7, 8]
    assert region.extra_pairs == [("custom", ["keep", "these"])]
    assert decoded(loaded.extract_regions(["a"])["a"]).tobytes() == pristine.tobytes()
    assert [(r.key, r.label) for r in loaded.snapshot().regions] == [("a", "renamed")]
    loaded.close()


def test_historical_full_canvas_reset_survives_structural_rebuild():
    from atlas_toolkit.document import AtlasDocument
    from atlas_toolkit.engine.session import AtlasSession

    atlas = """p.png
size: 8,4
a
 bounds: 0,0,2,2
 offsets: 0,0,160,80
b
 bounds: 2,0,2,2
 offsets: 7,5,160,80
"""
    loaded = AtlasSession.load(atlas, {"p.png": png(8, 4, (1, 2, 3, 255))}, PillowRasterBackend())
    loaded.apply_mod_image(png(160, 80, (40, 50, 60, 255)), ["a"])
    loaded.rename_region("a", "renamed")
    text, pages = loaded.save()
    output = AtlasDocument.parse(text)
    regions = {r.atlas_name: r for r in output.pages[0].regions}
    assert regions["renamed"].offsets is None
    assert regions["b"].offsets == [7, 5, 160, 80]
    image = decoded(pages["p.png"])
    b = regions["b"]
    assert image.crop((b.x, b.y, b.x + b.w, b.y + b.h)).getpixel((0, 0)) == (1, 2, 3, 255)
    loaded.close()


# Stage 7 Task 5: session mesh/nest integration tests (append to test_session.py).
_TASK5_LEFT_HALF = {
    "uvs": [0, 0, 0.5, 0, 0, 1, 0.5, 1],
    "triangles": [0, 1, 2, 1, 3, 2],
}


def _task5_mesh_session():
    from atlas_toolkit.engine.session import AtlasSession

    atlas = """p.png
size: 16,2
a
 bounds: 0,0,4,2
b
 bounds: 4,0,4,2
c
 bounds: 8,0,4,2
d
 bounds: 12,0,4,2
"""
    raster = PillowRasterBackend()
    loaded = AtlasSession.load(atlas, {"p.png": _pattern_png(16, 2, 11)}, raster)
    return loaded, raster


def test_task5_session_mesh_defaults_closed_guards_and_boolean_coercion():
    loaded, _raster = _task5_mesh_session()
    assert loaded.snapshot().mesh_available is False
    assert loaded.snapshot().mesh_mask_enabled is False
    assert loaded.snapshot().mesh_aware_repack_enabled is False
    assert loaded.snapshot().nest_regions_enabled is False
    assert loaded.snapshot().nest_gap_distance == 4
    loaded.close()

    for operation in (
        lambda: loaded.set_mesh_data(None),
        lambda: loaded.set_mesh_mask_enabled(False),
        lambda: loaded.set_mesh_aware_repack_enabled(False),
        lambda: loaded.set_nest_regions_enabled(False),
        lambda: loaded.set_nest_gap_distance(4),
    ):
        with pytest.raises(RuntimeError, match="closed"):
            operation()

    loaded, _raster = _task5_mesh_session()
    loaded.set_mesh_data({"unknown": _TASK5_LEFT_HALF})
    loaded.set_mesh_mask_enabled("enabled")
    loaded.set_mesh_aware_repack_enabled(1)
    loaded.set_nest_regions_enabled([])
    assert loaded.snapshot().mesh_available is True
    assert loaded.snapshot().mesh_mask_enabled is True
    assert loaded.snapshot().mesh_aware_repack_enabled is True
    assert loaded.snapshot().nest_regions_enabled is False
    loaded.close()


def test_task5_mesh_data_is_defensive_and_does_not_rebuild_or_change_saved_output():
    loaded, raster = _task5_mesh_session()
    first = loaded.apply_mod_image(_pattern_png(4, 2, 91), ["a"])
    assert first is not None
    loaded.save()
    before_snapshot = loaded.snapshot()
    before_active = loaded._active.image
    before_active_pixels = raster.get_pixels(before_active)
    before_preview = loaded.get_preview_png()
    before_extract = loaded.extract_regions(["a"])["a"]

    lookup = {"a": {"uvs": list(_TASK5_LEFT_HALF["uvs"]), "triangles": list(_TASK5_LEFT_HALF["triangles"])}}
    loaded.set_mesh_data(lookup)
    lookup["a"]["uvs"][0] = 0.75
    lookup["a"]["triangles"].clear()
    lookup.clear()
    after_attach = loaded.snapshot()
    assert after_attach.mesh_available is True
    assert after_attach.generation == before_snapshot.generation
    assert loaded._active.image == before_active
    assert raster.get_pixels(before_active) == before_active_pixels
    assert loaded.get_preview_png() == before_preview
    assert loaded.extract_regions(["a"])["a"] == before_extract
    assert loaded._modifications_saved is True

    # The mutated caller mapping must not replace the copied geometry.
    loaded.set_mesh_mask_enabled(True)
    copied_geometry_alpha = list(decoded(loaded.extract_regions(["a"])["a"]).tobytes()[3::4])
    assert copied_geometry_alpha == [255, 255, 0, 0, 255, 255, 0, 0]
    assert loaded.snapshot().generation == before_snapshot.generation
    assert loaded._active.image == before_active
    assert raster.get_pixels(before_active) == before_active_pixels

    loaded.set_mesh_data({})
    assert loaded.snapshot().mesh_available is False
    loaded.set_mesh_data({"a": _TASK5_LEFT_HALF})
    assert loaded.snapshot().mesh_available is True
    loaded.set_mesh_data(None)
    assert loaded.snapshot().mesh_available is False
    assert loaded.snapshot().generation == before_snapshot.generation
    assert loaded._active.image == before_active
    assert raster.get_pixels(before_active) == before_active_pixels
    assert loaded._modifications_saved is True
    loaded.close()


def test_task5_crop_mask_is_independent_and_restores_original_on_disable():
    loaded, _raster = _task5_mesh_session()
    loaded.set_mesh_data({"a": _TASK5_LEFT_HALF})
    original = decoded(loaded.extract_regions(["a"])["a"])
    assert original.size == (4, 2)
    loaded.set_mesh_mask_enabled(True)
    masked = decoded(loaded.extract_regions(["a"])["a"])
    assert masked.size == (4, 2)
    assert [masked.getpixel((x, y))[3] for y in range(2) for x in range(4)] == [255, 255, 0, 0] * 2
    assert decoded(loaded.get_preview_png(["a"])).tobytes() == masked.tobytes()
    assert loaded.snapshot().mesh_aware_repack_enabled is False
    loaded.set_mesh_mask_enabled(False)
    restored = decoded(loaded.extract_regions(["a"])["a"])
    assert restored.tobytes() == original.tobytes()

    # Lookup misses leave regions as ordinary rectangles.
    loaded.set_mesh_mask_enabled(True)
    loaded.set_mesh_data({"unknown": _TASK5_LEFT_HALF})
    assert decoded(loaded.extract_regions(["a"])["a"]).tobytes() == original.tobytes()
    loaded.set_mesh_data({"a": _TASK5_LEFT_HALF})
    loaded.apply_mod_image(_pattern_png(4, 2, 123), ["a"])
    loaded.save()
    before = loaded.snapshot()
    active = loaded._active.image
    loaded.set_mesh_mask_enabled(False)
    assert loaded.snapshot().generation == before.generation
    assert loaded.snapshot().has_output is True
    assert loaded._active.image == active
    assert loaded._modifications_saved is True
    loaded.close()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" 7 ", 7), (2.9, 2), (0, 4), (-1, 4), (None, 4), ("bad", 4),
        (float("nan"), 4), (float("inf"), 4), (False, 4), (True, 1), (300, 256),
    ],
)
def test_task5_session_gap_uses_canonical_normalizer(value, expected):
    loaded, _raster = _task5_mesh_session()
    loaded.set_nest_gap_distance(value)
    assert loaded.snapshot().nest_gap_distance == expected
    assert loaded._processor.get_nest_options()["gap_distance"] == expected
    loaded.close()


@pytest.mark.parametrize(
    "setter",
    ["set_mesh_aware_repack_enabled", "set_nest_regions_enabled", "set_nest_gap_distance"],
)
def test_task5_no_batch_setting_changes_never_rebuild_even_with_active_output(setter):
    loaded, raster = _task5_mesh_session()
    initial = loaded.repack()
    before_snapshot = loaded.snapshot()
    before_pixels = raster.get_pixels(initial.image)
    before_live = {handle: raster.get_pixels(handle) for handle in raster._images}
    argument = {
        "set_mesh_aware_repack_enabled": True,
        "set_nest_regions_enabled": True,
        "set_nest_gap_distance": " 7 ",
    }[setter]
    result = getattr(loaded, setter)(argument)
    assert result is None
    assert loaded.snapshot().generation == before_snapshot.generation
    assert loaded.snapshot().has_output is True
    assert loaded._active.image == initial.image
    assert raster.get_pixels(initial.image) == before_pixels
    assert {handle: raster.get_pixels(handle) for handle in raster._images} == before_live
    loaded.close()


@pytest.mark.parametrize(
    "setter", ["set_mesh_aware_repack_enabled", "set_nest_regions_enabled", "set_nest_gap_distance"],
)
@pytest.mark.parametrize("route", ["pixel", "rename"])
def test_task5_rerun_setters_repack_pixel_and_structural_batches_once(setter, route):
    from atlas_toolkit.engine.session import RepackResult

    loaded, _raster = _task5_mesh_session()
    if route == "pixel":
        loaded.apply_mod_image(_pattern_png(4, 2, 151), ["a"])
    else:
        loaded.rename_region("a", "renamed")
    loaded.save()
    before = loaded.snapshot()
    batch_count = len(loaded._mod_batches)
    # Exercise a same-value call: the contract still requires a rebuild.
    arguments = {
        "set_mesh_aware_repack_enabled": False,
        "set_nest_regions_enabled": False,
        "set_nest_gap_distance": 4,
    }
    result = getattr(loaded, setter)(arguments[setter])
    assert isinstance(result, RepackResult)
    after = loaded.snapshot()
    assert after.generation == before.generation
    assert len(loaded._mod_batches) == batch_count
    assert after.has_pending_modifications is False
    assert loaded._modifications_saved is True
    loaded.close()


def test_task5_attach_does_not_repack_pending_batch_and_clear_keeps_options():
    loaded, _raster = _task5_mesh_session()
    loaded.apply_mod_image(_pattern_png(4, 2, 199), ["a"])
    before = loaded.snapshot()
    active = loaded._active.image
    loaded.set_mesh_data({"a": _TASK5_LEFT_HALF})
    assert loaded.snapshot().generation == before.generation
    assert loaded._active.image == active
    assert len(loaded._mod_batches) == 1
    loaded.set_mesh_mask_enabled(True)
    loaded.set_mesh_aware_repack_enabled(True)
    loaded.set_nest_regions_enabled(True)
    loaded.set_nest_gap_distance("7")
    loaded.clear_modify_state()
    snapshot = loaded.snapshot()
    assert snapshot.generation == before.generation and snapshot.has_output is False
    assert snapshot.mesh_available is True
    assert snapshot.mesh_mask_enabled is True
    assert snapshot.mesh_aware_repack_enabled is True
    assert snapshot.nest_regions_enabled is True
    assert snapshot.nest_gap_distance == 7
    loaded.close()



def test_task5_no_batch_settings_on_pristine_and_multi_page_sessions_do_not_allocate():
    loaded, raster = _task5_mesh_session()
    pristine_snapshot = loaded.snapshot()
    pristine_live = {handle: raster.get_pixels(handle) for handle in raster._images}
    assert loaded.set_mesh_data({"unknown": _TASK5_LEFT_HALF}) is None
    assert loaded.set_mesh_mask_enabled(True) is None
    assert loaded.set_mesh_aware_repack_enabled(True) is None
    assert loaded.set_nest_regions_enabled(True) is None
    assert loaded.set_nest_gap_distance(" 7 ") is None
    assert loaded.snapshot().generation == pristine_snapshot.generation
    assert loaded.snapshot().has_output is False
    assert loaded._modifications_saved is False
    assert {handle: raster.get_pixels(handle) for handle in raster._images} == pristine_live
    loaded.close()

    multi = session()
    multi_raster = multi._raster
    multi_snapshot = multi.snapshot()
    multi_live = multi_raster.checkpoint()
    assert multi.set_mesh_data({"unknown": _TASK5_LEFT_HALF}) is None
    assert multi.set_mesh_mask_enabled(True) is None
    assert multi.set_mesh_aware_repack_enabled(True) is None
    assert multi.set_nest_regions_enabled(True) is None
    assert multi.set_nest_gap_distance(" 7 ") is None
    assert multi.snapshot().generation == multi_snapshot.generation
    assert multi.snapshot().has_output is False
    assert multi_raster.checkpoint() == multi_live
    multi.close()


def test_task5_repack_mesh_gate_skips_pma_and_added_keys():
    from atlas_toolkit.engine.session import AtlasSession

    raster = PillowRasterBackend()
    pma_atlas = """p.png
size: 4,2
pma: true
a
 bounds: 0,0,4,2
"""
    pma_source = _pattern_png(4, 2, 37)
    pma = AtlasSession.load(pma_atlas, {"p.png": pma_source}, raster)
    pma.set_mesh_data({"a": _TASK5_LEFT_HALF})
    assert pma.snapshot().mesh_available is True
    pma.set_mesh_mask_enabled(True)
    pma.set_mesh_aware_repack_enabled(True)
    assert decoded(pma.extract_regions(["a"])["a"]).tobytes()[3::4] == bytes([255] * 8)
    pma.apply_mod_image(pma_source, ["a"])
    result = pma.repack()
    _text, saved = pma.save()
    assert _saved_region_pixels(saved, result, "a")[3::4] == bytes([255] * 8)
    pma.close()

    added, _added_raster = _task5_mesh_session()
    added.set_mesh_data({"new": _TASK5_LEFT_HALF})
    assert added.set_mesh_aware_repack_enabled(True) is None
    new_pixels = _pattern_png(4, 2, 73)
    added.add_region(new_pixels, "new")
    result = added.repack()
    _text, saved = added.save()
    assert _saved_region_pixels(saved, result, "new") == decoded(new_pixels).tobytes()
    added.close()


def test_task5_toggle_repack_reuses_mod_add_and_pristine_sources_without_handle_growth(monkeypatch):
    from atlas_toolkit.engine.modifier import AtlasModifier

    loaded, raster = _task5_mesh_session()
    mod_pixels = _pattern_png(4, 2, 101)
    add_pixels = _pattern_png(4, 2, 151)
    prepare_calls = []
    original_prepare = AtlasModifier.prepare_mod_image

    def record_prepare(self, source, names):
        prepare_calls.append(tuple(names))
        return original_prepare(self, source, names)

    monkeypatch.setattr(AtlasModifier, "prepare_mod_image", record_prepare)
    loaded.apply_mod_image(mod_pixels, ["a"])
    loaded.add_region(add_pixels, "new")
    loaded.set_mesh_data({"a": _TASK5_LEFT_HALF, "new": _TASK5_LEFT_HALF})
    pristine = {handle: raster.get_pixels(handle) for handle in loaded._roots}
    mod_batch, add_batch = loaded._mod_batches
    durable = {
        mod_batch["source"]: raster.get_pixels(mod_batch["source"]),
        mod_batch["prepared"].image: raster.get_pixels(mod_batch["prepared"].image),
        add_batch["source"]: raster.get_pixels(add_batch["source"]),
    }
    live_count = len(raster._images)

    for nest_enabled in (False, True):
        assert loaded.set_nest_regions_enabled(nest_enabled) is not None
        for enabled in (True, False, True, False):
            result = loaded.set_mesh_aware_repack_enabled(enabled)
            assert result is not None

            x, y, width, height, rotate = result.regions["a"]
            unrotated = loaded._raster.crop_rotate(result.image, x, y, width, height, rotate)
            try:
                mod_output = raster.get_pixels(unrotated)
            finally:
                loaded._raster.dispose(unrotated)
            if enabled:
                assert mod_output[3::4] == bytes([255, 255, 0, 0, 255, 255, 0, 0])
            else:
                assert mod_output == decoded(mod_pixels).tobytes()

            ax, ay, aw, ah, arotate = result.regions["new"]
            unrotated_add = loaded._raster.crop_rotate(result.image, ax, ay, aw, ah, arotate)
            try:
                assert raster.get_pixels(unrotated_add) == decoded(add_pixels).tobytes()
            finally:
                loaded._raster.dispose(unrotated_add)

            assert len(raster._images) == live_count
            assert {handle: raster.get_pixels(handle) for handle in loaded._roots} == pristine
            assert {handle: raster.get_pixels(handle) for handle in durable} == durable
            assert prepare_calls == [("a",)]
    loaded.close()



@pytest.mark.parametrize("route", ["pixel", "rename"])
def test_task5_repack_mask_cuts_non_square_pixels_and_toggle_restores_them(route):
    loaded, raster = _task5_mesh_session()
    loaded.set_mesh_data({"a": _TASK5_LEFT_HALF})
    if route == "pixel":
        loaded.apply_mod_image(_pattern_png(4, 2, 211), ["a"])
    else:
        loaded.rename_region("a", "renamed")

    result = loaded.set_mesh_aware_repack_enabled(True)
    assert result is not None
    x, y, width, height, _rotate = result.regions["a"]
    pixels = raster.get_pixels(result.image)
    alpha = [
        pixels[((y + row) * raster.size(result.image)[0] + x + column) * 4 + 3]
        for row in range(height)
        for column in range(width)
    ]
    assert 0 in alpha and 255 in alpha

    # Nest on/off keeps the real repack route active for both pending-batch kinds.
    assert loaded.set_nest_regions_enabled(True) is not None
    assert loaded.set_nest_regions_enabled(False) is not None
    result = loaded.set_mesh_aware_repack_enabled(False)
    assert result is not None
    x, y, width, height, _rotate = result.regions["a"]
    pixels = raster.get_pixels(result.image)
    alpha = [
        pixels[((y + row) * raster.size(result.image)[0] + x + column) * 4 + 3]
        for row in range(height)
        for column in range(width)
    ]
    assert alpha and set(alpha) == {255}
    loaded.close()
