"""Ownership facade and failure cleanup for the Stage 7 session slice."""
from __future__ import annotations

import io

import pytest
from PIL import Image, UnidentifiedImageError

from atlas_toolkit.raster.base import ImageHandle
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend


def png() -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (2, 2), (50, 60, 70, 255)).save(output, format="PNG")
    return output.getvalue()


class RecordingRaster(PillowRasterBackend):
    def __init__(self):
        super().__init__()
        self.disposals: list[ImageHandle] = []
        self.fail: str | None = None

    def dispose(self, handle):
        self.disposals.append(handle)
        return super().dispose(handle)

    def encode_png(self, handle):
        if self.fail == "encode":
            raise RuntimeError("encode failure")
        return super().encode_png(handle)


class FaultRaster(RecordingRaster):
    """Real Pillow calls; injected failures occur before allocation/mutation."""

    def __init__(self):
        super().__init__()
        self.fail_method: str | None = None
        self.fail_at = 1
        self.calls: dict[str, int] = {}
        self.stores: list[ImageHandle] = []

    def _store(self, image):
        handle = super()._store(image)
        self._next_handle += 12  # Deliberately spaced by 13, not contiguous IDs.
        self.stores.append(handle)
        return handle

    def _before(self, method):
        self.calls[method] = self.calls.get(method, 0) + 1
        if self.fail_method == method and self.calls[method] == self.fail_at:
            raise RuntimeError(f"{method} failure")

    def decode_png(self, data):
        self._before("decode")
        return super().decode_png(data)

    def encode_png(self, handle):
        self._before("encode")
        return super().encode_png(handle)

    def crop_rotate(self, handle, x, y, w, h, rotate):
        self._before("crop")
        return super().crop_rotate(handle, x, y, w, h, rotate)

    def paste(self, dest, src, x, y):
        self._before("paste")
        return super().paste(dest, src, x, y)


TWO = """p.png
size: 2,2
a
 bounds: 0,0,1,1
b
 bounds: 1,0,1,1
q.png
size: 2,2
z
 bounds: 0,0,1,1"""

PATTERN = bytes((255, 0, 0, 255, 0, 255, 0, 255,
                 0, 0, 255, 255, 255, 255, 0, 255))


def patterned_png():
    output = io.BytesIO()
    Image.frombytes("RGBA", (2, 2), PATTERN).save(output, format="PNG")
    return output.getvalue()


def two_pngs():
    return {"p.png": patterned_png(), "q.png": png()}


def decoded_pixels(data):
    image = Image.open(io.BytesIO(data)).convert("RGBA")
    return image.size, image.tobytes()


def live_pixels(raster):
    # Exact keys and bytes, including any foreign images on the shared backend.
    return {handle: raster.get_pixels(handle) for handle in raster._images}


def test_session_owns_only_its_roots_and_read_temporaries_are_released():
    from atlas_toolkit.engine.session import AtlasSession

    raster = RecordingRaster()
    foreign = raster.decode_png(png())
    left = AtlasSession.load("p.png\nsize: 2,2\nr\n bounds: 0,0,1,1", {"p.png": png()}, raster)
    right = AtlasSession.load("q.png\nsize: 2,2\ns\n bounds: 0,0,1,1", {"q.png": png()}, raster)
    baseline = set(raster._images)
    pristine = live_pixels(raster)
    survivor = right.get_preview_png()
    for _ in range(3):
        left.extract_regions(["r"])
        left.get_preview_png(["r"])
        left.save()
        assert set(raster._images) == baseline
        assert live_pixels(raster) == pristine
    left.close()
    left.close()
    assert set(raster._images) == {foreign} | set(right._roots)
    assert live_pixels(raster) == {h: pristine[h] for h in raster._images}
    assert decoded_pixels(right.get_preview_png()) == decoded_pixels(survivor)
    right.close()
    right.close()
    assert live_pixels(raster) == {foreign: pristine[foreign]}


def test_facade_tracks_allocations_and_dispose_is_idempotent_for_owned_only():
    from atlas_toolkit.engine._images import _SessionRaster

    backend = RecordingRaster()
    facade = _SessionRaster(backend)
    foreign = backend.decode_png(png())
    image = facade.decode_png(png())
    cloned = facade.clone(image)
    assert facade.checkpoint() == frozenset({image, cloned})
    facade.dispose(image)
    facade.dispose(image)
    facade.dispose(foreign)
    assert backend.disposals.count(image) == 1
    assert foreign in backend._images
    facade.release_except(frozenset({cloned}))
    assert facade.checkpoint() == frozenset({cloned})
    facade.dispose(cloned)


def test_failed_encode_keeps_session_reusable_and_no_scratch_leaks():
    from atlas_toolkit.engine.session import AtlasSession

    raster = RecordingRaster()
    loaded = AtlasSession.load("p.png\nsize: 2,2\nr\n bounds: 0,0,1,1", {"p.png": png()}, raster)
    roots = set(raster._images)
    raster.fail = "encode"
    with pytest.raises(RuntimeError, match="encode failure"):
        loaded.extract_regions(["r"])
    assert set(raster._images) == roots
    with pytest.raises(RuntimeError, match="encode failure"):
        loaded.save()
    assert set(raster._images) == roots
    raster.fail = None
    assert loaded.extract_regions(["r"])
    loaded.close()


def test_corrupt_second_page_releases_decoded_first_and_preserves_foreign():
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    foreign = raster.decode_png(patterned_png())
    before = live_pixels(raster)
    with pytest.raises(UnidentifiedImageError):
        AtlasSession.load(TWO, {"p.png": png(), "q.png": b"corrupt"}, raster)
    assert len(raster.stores) == 2  # Foreign plus the successfully decoded page.
    assert raster.disposals == [raster.stores[-1]]
    assert live_pixels(raster) == before == {foreign: PATTERN}


def test_processor_constructor_failure_releases_all_decoded_roots(monkeypatch):
    import atlas_toolkit.engine.session as module

    raster = FaultRaster()
    foreign = raster.decode_png(patterned_png())
    before = live_pixels(raster)

    def broken_processor(document, page_images, raster):
        assert list(page_images) == ["p.png", "q.png"]
        raise RuntimeError("constructor failure")

    monkeypatch.setattr(module, "AtlasProcessor", broken_processor)
    with pytest.raises(RuntimeError, match="constructor failure"):
        module.AtlasSession.load(TWO, two_pngs(), raster)
    assert len(raster.stores) == 3
    assert set(raster.disposals) == set(raster.stores) - {foreign}
    assert len(raster.disposals) == 2
    assert live_pixels(raster) == before


def test_missing_second_filename_is_rejected_before_corrupt_first_is_decoded():
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    foreign = raster.decode_png(patterned_png())
    raster.calls.clear()
    with pytest.raises(ValueError, match=r"q\.png"):
        AtlasSession.load(TWO, {"p.png": b"corrupt"}, raster)
    assert raster.calls.get("decode", 0) == 0
    assert raster.stores == [foreign]
    assert raster.disposals == []
    assert live_pixels(raster) == {foreign: PATTERN}


@pytest.mark.parametrize(
    "operation,method,call_number",
    [
        ("extract", "crop", 2),
        ("selection", "paste", 2),
        ("extract", "encode", 2),
        ("selection", "encode", 1),
        ("default", "encode", 1),
        ("save", "encode", 2),
    ],
)
def test_read_failures_preserve_exact_live_pixels_snapshot_and_allow_retry(
    operation, method, call_number,
):
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    foreign = raster.decode_png(patterned_png())
    loaded = AtlasSession.load(TWO, two_pngs(), raster)
    operations = {
        "extract": lambda: loaded.extract_regions(["a", "b"]),
        "selection": lambda: loaded.get_preview_png(["a", "b"]),
        "default": loaded.get_preview_png,
        "save": loaded.save,
    }
    before = live_pixels(raster)
    snapshot = loaded.snapshot()
    raster.calls.clear()
    raster.fail_method, raster.fail_at = method, call_number
    try:
        with pytest.raises(RuntimeError, match=f"{method} failure"):
            operations[operation]()
        assert raster.calls[method] == call_number
        assert live_pixels(raster) == before
        assert loaded.snapshot() == snapshot
        assert loaded._modifications_saved is False
        raster.fail_method = None
        assert operations[operation]()
        assert live_pixels(raster) == before
        assert loaded.snapshot() == snapshot
        assert loaded.snapshot().has_output is False
        assert loaded._modifications_saved is (operation == "save")
    finally:
        raster.fail_method = None
        loaded.close()
    assert live_pixels(raster) == {foreign: PATTERN}


def test_extract_regions_disposes_each_crop_before_the_next_name():
    from atlas_toolkit.engine.session import AtlasSession

    class PeakRaster(FaultRaster):
        def __init__(self):
            super().__init__()
            self.peak_live = 0
            self.encode_count = 0

        def _store(self, image):
            handle = super()._store(image)
            self.peak_live = max(self.peak_live, len(self._images))
            return handle

        def encode_png(self, handle):
            self.encode_count += 1
            return super().encode_png(handle)

    raster = PeakRaster()
    loaded = AtlasSession.load(SINGLE, single_pngs(), raster)
    baseline = len(raster._images)
    raster.peak_live = baseline
    try:
        result = loaded.extract_regions(["a"] * 20 + ["b", "missing", "a"])
        assert list(result) == ["a", "b"]
        assert raster.encode_count == 22
        assert raster.peak_live <= baseline + 1
        assert len(raster._images) == baseline
    finally:
        loaded.close()


def test_later_failed_save_preserves_true_saved_flag_and_can_retry():
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    loaded = AtlasSession.load(TWO, two_pngs(), raster)
    loaded.save()
    before, snapshot = live_pixels(raster), loaded.snapshot()
    raster.calls.clear()
    raster.fail_method, raster.fail_at = "encode", 2
    try:
        with pytest.raises(RuntimeError, match="encode failure"):
            loaded.save()
        assert raster.calls["encode"] == 2
        assert loaded._modifications_saved is True
        assert live_pixels(raster) == before
        assert loaded.snapshot() == snapshot
        raster.fail_method = None
        assert loaded.save()
        assert loaded._modifications_saved is True
        assert loaded.snapshot() == snapshot
        assert loaded.snapshot().has_output is False
        assert live_pixels(raster) == before
    finally:
        raster.fail_method = None
        loaded.close()
    assert raster._images == {}


@pytest.mark.parametrize("previously_saved", [False, True])
def test_serializer_failure_preserves_saved_flag(monkeypatch, previously_saved):
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    loaded = AtlasSession.load(TWO, two_pngs(), raster)
    if previously_saved:
        loaded.save()
    before, snapshot = live_pixels(raster), loaded.snapshot()
    serialize = loaded._document.serialize

    def broken_serialize():
        raise RuntimeError("serializer failure")

    monkeypatch.setattr(loaded._document, "serialize", broken_serialize)
    raster.calls.clear()
    try:
        with pytest.raises(RuntimeError, match="serializer failure"):
            loaded.save()
        assert raster.calls.get("encode", 0) == 0
        assert loaded._modifications_saved is previously_saved
        assert live_pixels(raster) == before
        assert loaded.snapshot() == snapshot
        monkeypatch.setattr(loaded._document, "serialize", serialize)
        assert loaded.save()
        assert loaded._modifications_saved is True
        assert loaded.snapshot() == snapshot
        assert loaded.snapshot().has_output is False
        assert live_pixels(raster) == before
    finally:
        loaded.close()
    assert raster._images == {}


def test_facade_all_six_allocators_and_nonallocating_pixel_forwards():
    from atlas_toolkit.engine._images import _SessionRaster

    backend = FaultRaster()
    facade = _SessionRaster(backend)
    foreign = backend.decode_png(png())
    foreign_pixels = backend.get_pixels(foreign)
    source = facade.decode_png(patterned_png())
    dest = facade.create(2, 2)
    cropped = facade.crop_rotate(source, 1, 0, 1, 2, 0)
    rotated = facade.rotate_image(source, 180)
    cloned = facade.clone(source)
    mask = facade.rasterize_mesh_mask(
        [0, 0, 2 / 3, 0, 2 / 3, 1, 0, 1], [0, 1, 2, 0, 2, 3], 3, 2,
    )
    owned = frozenset({source, dest, cropped, rotated, cloned, mask})
    assert facade.checkpoint() == owned
    assert set(backend.stores) == owned | {foreign}
    assert len(backend.stores) == 7
    assert any(b - a != 1 for a, b in zip(backend.stores, backend.stores[1:]))
    assert facade.get_pixels(dest) == bytes(16)
    assert facade.size(cropped) == (1, 2)
    assert facade.get_pixels(cropped) == PATTERN[4:8] + PATTERN[12:16]
    assert facade.get_pixels(rotated) == b"".join(PATTERN[i:i + 4] for i in (12, 8, 4, 0))
    assert facade.get_pixels(cloned) == PATTERN
    stores_before = list(backend.stores)
    facade.paste(dest, cropped, 1, 0)
    assert facade.get_pixels(dest) == bytes(4) + PATTERN[4:8] + bytes(4) + PATTERN[12:16]
    facade.composite_destination_in(cloned, mask, src_rect=(1, 0, 2, 2))
    expected = PATTERN[:7] + b"\x00" + PATTERN[8:15] + b"\x00"
    assert facade.get_pixels(cloned) == expected
    assert facade.read_alpha_mask(cloned) == [True, False, True, False]
    facade.composite_destination_in(source, cloned)
    assert facade.get_pixels(source) == expected
    assert facade.size(source) == (2, 2)
    assert decoded_pixels(facade.encode_png(source)) == ((2, 2), expected)
    assert facade.checkpoint() == owned
    assert backend.stores == stores_before  # None of the forwards allocated handles.

    facade.dispose(cropped)
    facade.dispose(cropped)
    facade.dispose(foreign)
    assert backend.disposals == [cropped]
    assert facade.checkpoint() == owned - {cropped}
    keep = facade.checkpoint()
    extra = facade.create(1, 1)
    facade.release_except(keep)
    assert backend.disposals == [cropped, extra]
    assert facade.checkpoint() == keep
    assert set(backend._images) == keep | {foreign}
    facade.release_except(frozenset())
    assert facade.checkpoint() == frozenset()
    assert set(backend.disposals) == owned | {extra}
    assert len(backend.disposals) == len(owned) + 1
    assert live_pixels(backend) == {foreign: foreign_pixels}


def test_clear_drops_explicit_scratch_and_double_close_guards_all_reads():
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    foreign = raster.decode_png(patterned_png())
    loaded = AtlasSession.load(TWO, two_pngs(), raster)
    before = live_pixels(raster)
    snapshot = loaded.snapshot()
    loaded.save()
    scratch = loaded._raster.create(7, 3)
    assert scratch in raster._images
    loaded.clear_modify_state()
    assert live_pixels(raster) == before
    assert loaded._raster.checkpoint() == loaded._roots
    assert raster.disposals.count(scratch) == 1
    assert loaded._modifications_saved is False
    assert loaded.snapshot() == snapshot
    loaded.close()
    disposals = list(raster.disposals)
    loaded.close()
    assert raster.disposals == disposals
    assert live_pixels(raster) == {foreign: PATTERN}
    for read in (
        loaded.snapshot, loaded.save, loaded.clear_modify_state,
        lambda: loaded.extract_regions([]), lambda: loaded.extract_regions(["a"]),
        loaded.get_preview_png, lambda: loaded.get_preview_png([]),
        lambda: loaded.get_preview_png(["a"]),
    ):
        with pytest.raises(RuntimeError, match="closed"):
            read()
    assert live_pixels(raster) == {foreign: PATTERN}


def test_extraction_outer_cleanup_discriminator_when_extraction_raises(monkeypatch):
    from atlas_toolkit.engine._images import _SessionRaster
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    loaded = AtlasSession.load(TWO, two_pngs(), raster)
    before, snapshot = live_pixels(raster), loaded.snapshot()
    extract_region = loaded._processor.extract_region
    release_except = _SessionRaster.release_except
    calls = []

    def no_release(self, keep):
        calls.append((self, keep))

    def allocate_then_fail(name):
        extract_region(name)
        raise RuntimeError("extraction failed after allocating crop")

    try:
        monkeypatch.setattr(_SessionRaster, "release_except", no_release)
        monkeypatch.setattr(loaded._processor, "extract_region", allocate_then_fail)
        with pytest.raises(RuntimeError, match="after allocating crop"):
            loaded.extract_regions(["a"])
        assert calls == [(loaded._raster, loaded._roots)]
        extras = set(raster._images) - set(before)
        assert len(extras) == 1  # Proves the normal exact-live-set assertion discriminates.
        assert loaded._raster.checkpoint() == loaded._roots | extras
        assert {h: raster.get_pixels(h) for h in before} == before
        assert loaded.snapshot() == snapshot
    finally:
        monkeypatch.setattr(_SessionRaster, "release_except", release_except)
        loaded._raster.release_except(loaded._roots)
        assert live_pixels(raster) == before
        loaded.close()
    assert raster._images == {}


def test_repeated_page_filename_keeps_first_modifier_size_and_last_extract_size():
    """Repeated filename declarations remain serialized; modifier uses first size, extraction last."""
    from atlas_toolkit.engine.session import AtlasSession

    class CountingRaster(FaultRaster):
        def __init__(self):
            super().__init__()
            self.decode_count = 0

        def decode_png(self, data):
            self.decode_count += 1
            return super().decode_png(data)

    atlas = """p.png
size: 2,1
r
 bounds: 0,0,1,1
p.png
size: 2,1
t
 bounds: 1,0,1,1
p.png
size: 4,2
s
 bounds: 1,0,2,1"""
    source_pixels = bytes((
        10, 0, 0, 255, 0, 20, 0, 255, 0, 0, 30, 255, 40, 40, 0, 255,
        1, 2, 3, 255, 4, 5, 6, 255, 7, 8, 9, 255, 10, 11, 12, 255,
    ))
    source_stream = io.BytesIO()
    Image.frombytes("RGBA", (4, 2), source_pixels).save(source_stream, format="PNG")
    raster = CountingRaster()
    loaded = AtlasSession.load(atlas, {"p.png": source_stream.getvalue()}, raster)
    try:
        assert raster.decode_count == 1
        assert loaded.snapshot().modify_region_bounds == {
            "r": (0, 0, 2, 2, 0), "t": (2, 0, 2, 2, 0),
            "s": (2, 0, 4, 2, 0),
        }
        extracted = loaded.extract_regions(["r", "t", "s"])
        assert decoded_pixels(extracted["r"]) == ((1, 1), bytes((10, 0, 0, 255)))
        assert decoded_pixels(extracted["t"]) == ((1, 1), bytes((0, 20, 0, 255)))
        assert decoded_pixels(extracted["s"]) == (
            (2, 1), bytes((0, 20, 0, 255, 0, 0, 30, 255)),
        )
        saved_text, saved_pages = loaded.save()
        assert saved_text.count("p.png") == 3
        assert saved_text.count("size: 2,1") == 2
        assert saved_text.count("size: 4,2") == 1
        assert list(saved_pages) == ["p.png"]
        assert decoded_pixels(saved_pages["p.png"]) == ((4, 2), source_pixels)
    finally:
        loaded.close()


def test_pending_add_label_collision_rejects_before_decode_and_preserves_output():
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    foreign = raster.decode_png(patterned_png())
    foreign_pixels = raster.get_pixels(foreign)
    loaded = AtlasSession.load(SINGLE, single_pngs(), raster)
    loaded.apply_mod_image(patterned_png(), ["a"])
    loaded.add_region(patterned_png(), "Pending")
    loaded.rename_region("Pending", "VisiblePending")
    loaded.save()
    borrowed = loaded._active.image
    borrowed_pixels = raster.get_pixels(borrowed)
    before, snapshot = live_pixels(raster), loaded.snapshot()
    raster.calls.clear()
    try:
        with pytest.raises(ValueError, match="already in use"):
            loaded.add_region(b"corrupt incoming image", " VisiblePending ")
        assert raster.calls.get("decode", 0) == 0
        with pytest.raises(ValueError, match="already in use"):
            loaded.rename_region("b", " VisiblePending ")
        assert raster.calls.get("decode", 0) == 0
        assert loaded.snapshot() == snapshot
        assert loaded._active.image == borrowed
        assert raster.get_pixels(borrowed) == borrowed_pixels
        assert live_pixels(raster) == before
        assert raster.get_pixels(foreign) == foreign_pixels
        assert loaded.rename_region("b", "renamed") is None
        assert {region.key: region.label for region in loaded.snapshot().regions}["b"] == "renamed"
    finally:
        loaded.close()
    assert live_pixels(raster) == {foreign: foreign_pixels}


SINGLE = """p.png
size: 2,2
a
 bounds: 0,0,1,1
b
 bounds: 1,0,1,1"""


def single_pngs():
    return {"p.png": patterned_png()}


@pytest.mark.parametrize("failure", ["decode", "prepare", "pack", "assembly"])
@pytest.mark.parametrize("saved_before_failure", [False, True])
def test_failed_mod_candidate_keeps_published_output_and_releases_facade_allocations(
    monkeypatch, failure, saved_before_failure,
):
    import atlas_toolkit.engine.modifier as modifier_module
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    loaded = AtlasSession.load(SINGLE, single_pngs(), raster)
    old_result = loaded.apply_mod_image(patterned_png(), ["a"])
    if saved_before_failure:
        loaded.save()
    old_image = old_result.image
    old_pixels = raster.get_pixels(old_image)
    old_snapshot = loaded.snapshot()
    before = live_pixels(raster)
    assert old_snapshot.has_output is True
    assert old_snapshot.has_pending_modifications is (not saved_before_failure)
    assert loaded._modifications_saved is saved_before_failure

    root = next(iter(loaded._roots))
    if failure == "decode":
        def fail_decode(data):
            loaded._raster.clone(root)  # Real allocation, tracked by the facade.
            raise RuntimeError("candidate decode failure")

        target, name = loaded._raster, "decode_png"
        replacement = fail_decode
    elif failure == "prepare":
        def fail_prepare(self, source, selected_regions):
            self.raster.clone(source)
            raise RuntimeError("candidate prepare failure")

        target, name = modifier_module.AtlasModifier, "prepare_mod_image"
        replacement = fail_prepare
    elif failure == "pack":
        def fail_pack(
            self, modded_sprites, full_canvas_regions=None,
            mesh_lookup_fn=None, nest_options=None,
        ):
            self.raster.clone(root)
            raise RuntimeError("candidate pack failure")

        target, name = modifier_module.AtlasModifier, "repack_with_modded_sprites"
        replacement = fail_pack
    else:
        def fail_assembly(pack):
            raise RuntimeError("candidate metadata assembly failure")

        target, name = loaded, "_make_result"
        replacement = fail_assembly

    with monkeypatch.context() as patcher:
        patcher.setattr(target, name, replacement)
        with pytest.raises(RuntimeError, match="candidate .* failure"):
            loaded.apply_mod_image(png(), ["b"])

    assert loaded.snapshot() == old_snapshot
    assert loaded._modifications_saved is saved_before_failure
    assert loaded._active.image == old_image
    assert raster.get_pixels(old_image) == old_pixels
    assert live_pixels(raster) == before

    # The same real pipeline remains usable after each injected failure.
    assert loaded.apply_mod_image(png(), ["b"]) is not None
    assert loaded.snapshot().generation == old_snapshot.generation + 1
    loaded.close()


@pytest.mark.parametrize("width,height", [(0, 3), (4, 0), (0, 0)])
@pytest.mark.parametrize("operation", ["apply", "repack"])
@pytest.mark.parametrize("has_previous_output", [False, True])
def test_nonpositive_pack_dimensions_roll_back_apply_and_repack(
    monkeypatch, width, height, operation, has_previous_output,
):
    import atlas_toolkit.engine.modifier as modifier_module
    from atlas_toolkit.engine.modifier import PackResult
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    loaded = AtlasSession.load(SINGLE, single_pngs(), raster)
    if has_previous_output:
        loaded.apply_mod_image(patterned_png(), ["a"])
    before = live_pixels(raster)
    snapshot = loaded.snapshot()

    def empty_pack(self, modded_sprites, full_canvas_regions=None,
                   mesh_lookup_fn=None, nest_options=None):
        image = self.raster.create(width, height)
        return PackResult(image, loaded._document, {})

    monkeypatch.setattr(
        modifier_module.AtlasModifier, "repack_with_modded_sprites", empty_pack,
    )
    invoke = (
        (lambda: loaded.apply_mod_image(patterned_png(), ["a"]))
        if operation == "apply" else loaded.repack
    )
    with pytest.raises(ValueError, match="^Repack output must have positive dimensions\\.$"):
        invoke()

    assert loaded.snapshot() == snapshot
    assert loaded._modifications_saved is False
    assert live_pixels(raster) == before
    if has_previous_output:
        assert loaded._active.image in before
        assert raster.get_pixels(loaded._active.image) == before[loaded._active.image]
    else:
        assert loaded.snapshot().has_output is False
    loaded.close()


def test_nonpositive_dimension_gate_discriminator_and_real_empty_atlas(monkeypatch):
    import atlas_toolkit.engine.modifier as modifier_module
    from atlas_toolkit.engine.modifier import PackResult
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    loaded = AtlasSession.load(SINGLE, single_pngs(), raster)

    def empty_pack(self, modded_sprites, full_canvas_regions=None,
                   mesh_lookup_fn=None, nest_options=None):
        image = self.raster.create(0, 3)
        return PackResult(image, loaded._document, {})

    with monkeypatch.context() as patched:
        patched.setattr(
            modifier_module.AtlasModifier, "repack_with_modded_sprites", empty_pack,
        )
        before = live_pixels(raster)
        with pytest.raises(ValueError, match="positive dimensions"):
            loaded.repack()
        assert live_pixels(raster) == before

        # In-memory bypass probe: without the size gate, this same injected
        # zero-width PackResult is published; the assertion above distinguishes
        # the required check from a passing fake result.
        patched.setattr(loaded._raster, "size", lambda handle: (1, 3))
        result = loaded.repack()
        assert raster.size(result.image) == (0, 3)
        loaded.clear_modify_state()
    loaded.close()

    empty_atlas = AtlasSession.load(
        "empty.png\nsize: 1,1\n", {"empty.png": png()}, FaultRaster(),
    )
    empty_raster = empty_atlas._raster
    with pytest.raises(ValueError, match="positive dimensions"):
        empty_atlas.repack()
    assert empty_atlas.snapshot().has_output is False
    assert empty_raster.checkpoint() == empty_atlas._roots
    empty_atlas.close()


@pytest.mark.parametrize("saved_before_failure", [False, True])
def test_active_output_encode_failure_preserves_output_pixels_and_state(saved_before_failure):
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    loaded = AtlasSession.load(SINGLE, single_pngs(), raster)
    result = loaded.apply_mod_image(patterned_png(), ["a"])
    if saved_before_failure:
        assert loaded.save()
    old_snapshot = loaded.snapshot()
    old_pixels = raster.get_pixels(result.image)
    before = live_pixels(raster)
    assert loaded._modifications_saved is saved_before_failure
    raster.calls.clear()
    raster.fail_method, raster.fail_at = "encode", 1
    try:
        with pytest.raises(RuntimeError, match="encode failure"):
            loaded.save()
        assert loaded.snapshot() == old_snapshot
        assert loaded._active.image == result.image
        assert raster.get_pixels(result.image) == old_pixels
        assert live_pixels(raster) == before
        assert loaded._modifications_saved is saved_before_failure
    finally:
        raster.fail_method = None
    assert loaded.save()
    assert loaded._active.image == result.image
    assert loaded.snapshot().generation == old_snapshot.generation
    loaded.close()


def test_repeated_repack_reuses_durable_roots_and_isolates_shared_backend(monkeypatch):
    import atlas_toolkit.engine.modifier as modifier_module
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    foreign = raster.decode_png(png())
    left = AtlasSession.load(SINGLE, single_pngs(), raster)
    right = AtlasSession.load(
        SINGLE, single_pngs(), raster,
    )
    prepared_pixels = []
    prepare = modifier_module.AtlasModifier.prepare_mod_image
    prepare_calls = 0

    def count_prepare(self, source, selected_regions):
        nonlocal prepare_calls
        prepare_calls += 1
        result = prepare(self, source, selected_regions)
        prepared_pixels.append(self.raster.get_pixels(result.image))
        return result

    monkeypatch.setattr(modifier_module.AtlasModifier, "prepare_mod_image", count_prepare)
    left.apply_mod_image(patterned_png(), ["a"])
    assert prepare_calls == 1
    batch = left._mod_batches[0]
    durable_batch_pixels = (
        raster.get_pixels(batch["source"]),
        raster.get_pixels(batch["prepared"].image),
    )
    foreign_and_right = {
        handle: raster.get_pixels(handle)
        for handle in raster._images if handle == foreign or handle in right._raster.checkpoint()
    }

    previous_output = left._active.image
    for _ in range(5):
        left.repack()
        assert left._active.image != previous_output
        previous_output = left._active.image
        batch = left._mod_batches[0]
        expected_live = (
            set(left._roots)
            | {batch["source"], batch["prepared"].image}
            | set(left._modded_sprites.values())
            | {left._active.image}
        )
        assert left._raster.checkpoint() == expected_live
        assert len(left._raster.checkpoint()) == len(left._roots) + 3
        assert prepare_calls == 1
        assert (
            raster.get_pixels(batch["source"]),
            raster.get_pixels(batch["prepared"].image),
        ) == durable_batch_pixels
        assert prepared_pixels == [durable_batch_pixels[1]]
        assert {
            handle: raster.get_pixels(handle) for handle in foreign_and_right
        } == foreign_and_right

    left.clear_modify_state()
    assert left._raster.checkpoint() == left._roots
    assert set(foreign_and_right) <= set(raster._images)
    assert {
        handle: raster.get_pixels(handle) for handle in foreign_and_right
    } == foreign_and_right
    left.close()
    assert set(foreign_and_right) <= set(raster._images)
    right.close()
    assert set(raster._images) == {foreign}


def test_pack_failure_before_first_output_restores_pristine_roots(monkeypatch):
    import atlas_toolkit.engine.modifier as modifier_module
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    loaded = AtlasSession.load(SINGLE, single_pngs(), raster)
    before = live_pixels(raster)
    snapshot = loaded.snapshot()
    root = next(iter(loaded._roots))

    def fail_pack(
        self, modded_sprites, full_canvas_regions=None,
        mesh_lookup_fn=None, nest_options=None,
    ):
        self.raster.clone(root)
        raise RuntimeError("initial pack failure")

    with monkeypatch.context() as patcher:
        patcher.setattr(
            modifier_module.AtlasModifier, "repack_with_modded_sprites", fail_pack,
        )
        with pytest.raises(RuntimeError, match="initial pack failure"):
            loaded.apply_mod_image(patterned_png(), ["a"])

    assert loaded.snapshot() == snapshot
    assert loaded.snapshot().has_output is False
    assert loaded.snapshot().has_pending_modifications is False
    assert live_pixels(raster) == before
    assert loaded.apply_mod_image(patterned_png(), ["a"]) is not None
    loaded.close()


def test_active_base_with_pristine_document_changes_untouched_sprite_pixels(
    monkeypatch,
):
    import atlas_toolkit.engine.modifier as modifier_module
    from atlas_toolkit.engine.session import AtlasSession

    def sized_png(width, height, color):
        output = io.BytesIO()
        Image.new("RGBA", (width, height), color).save(output, format="PNG")
        return output.getvalue()

    raster = FaultRaster()
    source_pixels = bytes(
        channel
        for index in range(12 * 4)
        for channel in (
            (index * 31) % 256,
            (index * 17) % 256,
            (index * 13) % 256,
            255,
        )
    )
    source_stream = io.BytesIO()
    Image.frombytes("RGBA", (12, 4), source_pixels).save(source_stream, format="PNG")
    atlas = """p.png
size: 12,4
a
 bounds: 0,0,4,3
b
 bounds: 4,0,4,3
c
 bounds: 8,1,3,2
 offsets: 1,1,5,4"""
    loaded = AtlasSession.load(atlas, {"p.png": source_stream.getvalue()}, raster)

    loaded.apply_mod_image(sized_png(3, 2, (220, 20, 30, 255)), ["a", "b"])
    good_result = loaded.apply_mod_image(
        sized_png(2, 3, (15, 40, 230, 255)), ["b"],
    )

    def saved_sprite(result, name):
        x, y, width, height, rotation = result.regions[name]
        image = loaded._raster.crop_rotate(
            result.image, x, y, width, height, rotation,
        )
        try:
            return raster.size(image), raster.get_pixels(image)
        finally:
            loaded._raster.dispose(image)

    good_c = saved_sprite(good_result, "c")
    active_pack = loaded._active

    def wrong_fresh_modifier():
        page = loaded._document.pages[0]
        return modifier_module.AtlasModifier(
            loaded._document, active_pack.image, loaded._raster, page.filename,
        )

    with monkeypatch.context() as patched:
        patched.setattr(loaded, "_fresh_modifier", wrong_fresh_modifier)
        wrong_result = loaded.repack()

    wrong_c = saved_sprite(wrong_result, "c")
    assert wrong_c[1] != good_c[1]
    loaded.close()


@pytest.mark.parametrize("nest_enabled", [False, True])
@pytest.mark.parametrize("previous_output", [False, True])
def test_real_packers_remove_all_roll_back_exact_checkpoint(nest_enabled, previous_output):
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    foreign = raster.decode_png(patterned_png())
    foreign_pixels = raster.get_pixels(foreign)
    loaded = AtlasSession.load(SINGLE, single_pngs(), raster)
    loaded._processor.set_nest_options(nest_enabled, 4)
    if previous_output:
        loaded.apply_mod_image(patterned_png(), ["a"])
        loaded.save()
    exported = loaded.save() if previous_output else None
    snapshot = loaded.snapshot()
    checkpoint = live_pixels(raster)
    saved_flag = loaded._modifications_saved
    old_image = loaded._active.image if loaded._active is not None else None
    old_pixels = raster.get_pixels(old_image) if old_image is not None else None

    with pytest.raises(ValueError, match=r"^Repack output must have positive dimensions\.$"):
        loaded.remove_regions(["a", "b"])
    assert loaded.snapshot() == snapshot
    assert loaded._modifications_saved is saved_flag
    assert live_pixels(raster) == checkpoint
    if old_image is not None:
        assert raster.get_pixels(old_image) == old_pixels
        assert loaded.save() == exported
    assert raster.get_pixels(foreign) == foreign_pixels

    loaded.rename_region("a", "aa")
    assert loaded.snapshot().generation == snapshot.generation + 1
    loaded.close()


def test_session_operations_do_not_reparse_atlas_after_load(monkeypatch):
    from atlas_toolkit.document import AtlasDocument
    from atlas_toolkit.engine.session import AtlasSession

    loaded = AtlasSession.load(SINGLE, single_pngs(), PillowRasterBackend())

    def unexpected_parse(*args, **kwargs):
        raise AssertionError("session operation reparsed atlas text")

    monkeypatch.setattr(AtlasDocument, "parse", staticmethod(unexpected_parse))
    loaded.add_region(patterned_png(), "new")
    loaded.rename_region("a", "renamed")
    loaded.remove_regions(["b"])
    loaded.repack()
    loaded.snapshot()
    text, pages = loaded.save()
    assert text and pages["p.png"]
    loaded.close()


def test_structural_repack_releases_no_foreign_or_shared_session_handles():
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    foreign = raster.decode_png(patterned_png())
    foreign_pixels = raster.get_pixels(foreign)
    left = AtlasSession.load(SINGLE, single_pngs(), raster)
    left_root_pixels = {
        handle: raster.get_pixels(handle)
        for handle in left._roots
    }
    right_atlas = "q.png\nsize: 2,2\nx\n bounds: 0,0,1,1"
    right = AtlasSession.load(right_atlas, {"q.png": png()}, raster)
    right_pixels = {
        handle: raster.get_pixels(handle)
        for handle in right._roots
    }
    right_preview = right.get_preview_png()

    left.add_region(patterned_png(), "new")
    live_count = len(raster._images)
    for _ in range(3):
        left.repack()
        assert len(raster._images) == live_count
    right_before_clear = {
        handle: raster.get_pixels(handle)
        for handle in right._roots
    }
    assert right_before_clear == right_pixels

    left.clear_modify_state()
    assert live_pixels(raster) == {
        foreign: foreign_pixels,
        **left_root_pixels,
        **right_pixels,
    }
    left.close()
    assert live_pixels(raster) == {
        foreign: foreign_pixels,
        **right_pixels,
    }
    assert decoded_pixels(right.get_preview_png()) == decoded_pixels(right_preview)
    right.close()
    assert live_pixels(raster) == {foreign: foreign_pixels}


def test_empty_remove_multipage_guards_and_closed_guards():
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    loaded = AtlasSession.load(TWO, two_pngs(), raster)
    snapshot = loaded.snapshot()
    calls = dict(raster.calls)
    pixels = live_pixels(raster)
    assert loaded.remove_regions([]) is None
    assert loaded.snapshot() == snapshot
    with pytest.raises(NotImplementedError):
        loaded.add_region(b"corrupt", "new")
    with pytest.raises(NotImplementedError):
        loaded.rename_region("a", "new")
    with pytest.raises(NotImplementedError):
        loaded.remove_regions(["a"])
    assert raster.calls == calls
    assert live_pixels(raster) == pixels
    loaded.close()
    for operation in (
        lambda: loaded.remove_regions([]),
        lambda: loaded.add_region(b"", "x"),
        lambda: loaded.rename_region("a", "x"),
    ):
        with pytest.raises(RuntimeError, match="closed"):
            operation()


def test_corrupt_add_rollback_and_historical_source_ownership():
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    foreign = raster.decode_png(patterned_png())
    loaded = AtlasSession.load(SINGLE, single_pngs(), raster)
    baseline = live_pixels(raster)
    snapshot = loaded.snapshot()
    with pytest.raises(UnidentifiedImageError):
        loaded.add_region(b"corrupt", "new")
    assert loaded.snapshot() == snapshot
    assert live_pixels(raster) == baseline

    raster.calls.clear()
    loaded.add_region(patterned_png(), "new")
    assert raster.calls["decode"] == 1
    source = loaded._mod_batches[-1]["source"]
    source_pixels = raster.get_pixels(source)
    loaded.remove_regions(["new"])
    raster.calls.clear()
    loaded.repack()
    loaded.repack()
    assert raster.calls.get("decode", 0) == 0
    assert raster.get_pixels(source) == source_pixels
    loaded.clear_modify_state()
    assert live_pixels(raster) == baseline
    assert foreign in baseline
    loaded.close()


@pytest.mark.parametrize("operation", ["add", "rename", "remove"])
@pytest.mark.parametrize("failure", ["pack", "assembly"])
def test_failed_structural_operation_preserves_saved_output_and_recovers(
    monkeypatch, operation, failure,
):
    import atlas_toolkit.engine.modifier as modifier_module
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    loaded = AtlasSession.load(SINGLE, single_pngs(), raster)
    loaded.apply_mod_image(patterned_png(), ["a"])
    loaded.save()
    snapshot = loaded.snapshot()
    pixels = live_pixels(raster)
    old_image = loaded._active.image
    old_pixels = raster.get_pixels(old_image)

    if failure == "pack":
        target, method = modifier_module.AtlasModifier, "repack_with_effective_model"

        def fail_pack(self, *args, **kwargs):
            self.raster.clone(old_image)
            raise RuntimeError("structural pack failure")

        replacement = fail_pack
    else:
        target, method = loaded, "_make_result"

        def fail_assembly(pack):
            raise RuntimeError("structural assembly failure")

        replacement = fail_assembly

    if operation == "add":
        invoke = lambda: loaded.add_region(patterned_png(), "new")
    elif operation == "rename":
        invoke = lambda: loaded.rename_region("b", "bee")
    else:
        invoke = lambda: loaded.remove_regions(["b"])

    with monkeypatch.context() as patcher:
        patcher.setattr(target, method, replacement)
        with pytest.raises(RuntimeError, match="structural .* failure"):
            invoke()
    assert loaded.snapshot() == snapshot
    assert loaded._modifications_saved is True
    assert loaded._active.image == old_image
    assert raster.get_pixels(old_image) == old_pixels
    assert live_pixels(raster) == pixels

    assert invoke() is None
    assert loaded.snapshot().generation == snapshot.generation + 1
    loaded.close()


# Stage 7 Task 5: session mesh/nest rollback tests (append to test_session_lifecycle.py).
_TASK5_LIFECYCLE_GEOMETRY = {
    "uvs": [0, 0, 0.5, 0, 0, 1, 0.5, 1],
    "triangles": [0, 1, 2, 1, 3, 2],
}


@pytest.mark.parametrize("setter", [
    "set_mesh_aware_repack_enabled",
    "set_nest_regions_enabled",
    "set_nest_gap_distance",
])
@pytest.mark.parametrize("failure_point", ["pack", "assembly"])
@pytest.mark.parametrize("route", ["pixel", "rename"])
def test_task5_setting_rerun_failure_restores_saved_output_and_all_live_pixels(
    monkeypatch, setter, failure_point, route,
):
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    loaded = AtlasSession.load(SINGLE, single_pngs(), raster)
    if route == "pixel":
        loaded.apply_mod_image(patterned_png(), ["a"])
    else:
        loaded.rename_region("a", "renamed")
    loaded.save()
    before_snapshot = loaded.snapshot()
    before_active = loaded._active.image
    before_encoded = loaded.save()
    before_live = live_pixels(raster)
    assert loaded._modifications_saved is True
    assert before_snapshot.has_pending_modifications is False

    arguments = {
        "set_mesh_aware_repack_enabled": True,
        "set_nest_regions_enabled": True,
        "set_nest_gap_distance": 7,
    }
    root = next(iter(loaded._roots))

    with monkeypatch.context() as patched:
        if failure_point == "pack":
            def fail_after_allocation(batches, sprites):
                loaded._raster.clone(root)
                raise RuntimeError("task5 injected pack failure")

            patched.setattr(loaded, "_build_repack", fail_after_allocation)
        else:
            def fail_after_allocation(pack):
                loaded._raster.clone(root)
                raise RuntimeError("task5 injected assembly failure")

            patched.setattr(loaded, "_make_result", fail_after_allocation)

        with pytest.raises(RuntimeError, match=f"task5 injected {failure_point} failure"):
            getattr(loaded, setter)(arguments[setter])

    assert loaded.snapshot() == before_snapshot
    assert loaded._active.image == before_active
    assert raster.get_pixels(before_active) == before_live[before_active]
    assert live_pixels(raster) == before_live
    assert loaded.save() == before_encoded
    assert loaded._modifications_saved is True
    assert loaded.snapshot().has_pending_modifications is False

    # The same real repack pipeline remains usable after the injected failure.
    result = getattr(loaded, setter)(arguments[setter])
    assert result is not None
    assert loaded.snapshot().generation == before_snapshot.generation + 1
    assert loaded._modifications_saved is True
    loaded.close()
    assert raster._images == {}


def test_task5_mesh_copy_failure_preserves_attached_data_flags_and_session_state():
    from atlas_toolkit.engine.session import AtlasSession

    class BrokenGeometry(dict):
        def __deepcopy__(self, memo):
            raise RuntimeError("mesh geometry deepcopy failure")

    raster = FaultRaster()
    loaded = AtlasSession.load(SINGLE, single_pngs(), raster)
    loaded.set_mesh_data({"a": _TASK5_LIFECYCLE_GEOMETRY})
    loaded.set_mesh_mask_enabled(True)
    before_snapshot = loaded.snapshot()
    before_live = live_pixels(raster)
    before_active = loaded._active

    with pytest.raises(RuntimeError, match="mesh geometry deepcopy failure"):
        loaded.set_mesh_data({"a": BrokenGeometry(_TASK5_LIFECYCLE_GEOMETRY)})

    assert loaded.snapshot() == before_snapshot
    assert loaded._active is before_active
    assert live_pixels(raster) == before_live
    assert loaded._processor.get_mesh_geometry("a") == _TASK5_LIFECYCLE_GEOMETRY
    loaded.close()


def test_task5_toggle_success_failure_and_clear_preserve_shared_backend_owners(monkeypatch):
    from atlas_toolkit.engine.session import AtlasSession

    raster = FaultRaster()
    foreign = raster.decode_png(png())
    left = AtlasSession.load(SINGLE, single_pngs(), raster)
    right = AtlasSession.load(SINGLE, single_pngs(), raster)
    left.apply_mod_image(patterned_png(), ["a"])
    left.save()
    right_preview = right.get_preview_png()
    foreign_pixels = raster.get_pixels(foreign)
    right_live = {handle: raster.get_pixels(handle) for handle in right._raster.checkpoint()}

    left.set_mesh_data({"a": _TASK5_LIFECYCLE_GEOMETRY})
    result = left.set_mesh_aware_repack_enabled(True)
    assert result is not None
    assert raster.get_pixels(foreign) == foreign_pixels
    assert {handle: raster.get_pixels(handle) for handle in right._raster.checkpoint()} == right_live
    assert right.get_preview_png() == right_preview

    before_snapshot = left.snapshot()
    before_live = live_pixels(raster)
    root = next(iter(left._roots))

    def fail_after_allocation(batches, sprites):
        left._raster.clone(root)
        raise RuntimeError("shared-backend candidate failure")

    with monkeypatch.context() as patched:
        patched.setattr(left, "_build_repack", fail_after_allocation)
        with pytest.raises(RuntimeError, match="shared-backend candidate failure"):
            left.set_nest_regions_enabled(True)

    assert left.snapshot() == before_snapshot
    assert live_pixels(raster) == before_live
    assert raster.get_pixels(foreign) == foreign_pixels
    assert {handle: raster.get_pixels(handle) for handle in right._raster.checkpoint()} == right_live
    assert right.get_preview_png() == right_preview

    left.clear_modify_state()
    assert left._raster.checkpoint() == left._roots
    assert raster.get_pixels(foreign) == foreign_pixels
    assert {handle: raster.get_pixels(handle) for handle in right._raster.checkpoint()} == right_live
    left.close()
    right.close()
    assert set(raster._images) == {foreign}



def test_task5_toggle_rerun_keeps_positive_dimension_gate_on_real_packer(monkeypatch):
    import atlas_toolkit.engine.modifier as modifier_module
    from atlas_toolkit.engine.session import AtlasSession
    from atlas_toolkit.engine.modifier import PackResult

    raster = FaultRaster()
    loaded = AtlasSession.load(SINGLE, single_pngs(), raster)
    loaded.apply_mod_image(patterned_png(), ["a"])
    loaded.save()
    before_snapshot = loaded.snapshot()
    before_live = live_pixels(raster)
    before_active = loaded._active.image
    before_encoded = loaded.save()
    real_pack = modifier_module.AtlasModifier.repack_with_modded_sprites

    def nonpositive_after_real_pack(
        self, modded_sprites, full_canvas_regions=None,
        mesh_lookup_fn=None, nest_options=None,
    ):
        candidate = real_pack(
            self, modded_sprites, full_canvas_regions,
            mesh_lookup_fn, nest_options,
        )
        self.raster.dispose(candidate.image)
        image = self.raster.create(0, 2)
        return PackResult(image, candidate.document, candidate.region_bounds)

    with monkeypatch.context() as patched:
        patched.setattr(
            modifier_module.AtlasModifier,
            "repack_with_modded_sprites",
            nonpositive_after_real_pack,
        )
        with pytest.raises(ValueError, match="positive dimensions"):
            loaded.set_mesh_aware_repack_enabled(True)

    assert loaded.snapshot() == before_snapshot
    assert loaded._active.image == before_active
    assert raster.get_pixels(before_active) == before_live[before_active]
    assert live_pixels(raster) == before_live
    assert loaded.save() == before_encoded
    assert loaded._modifications_saved is True
    # A valid later call exercises the unchanged real packer and gate again.
    result = loaded.set_mesh_aware_repack_enabled(True)
    assert result is not None
    assert loaded.snapshot().generation == before_snapshot.generation + 1
    loaded.close()
    assert raster._images == {}
