"""Deferred repack: same output as immediate mode for the same calls; pack_pending never publishes."""
import io

import pytest
from PIL import Image

from atlas_toolkit.engine.session import AtlasSession, RegionNameError, RemoveAllRegionsError
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

ATLAS = (
    "p.png\nsize: 16,8\n"
    "Head\n bounds: 0,0,6,5\n"
    "Body\n bounds: 6,0,8,6\n"
    "Arm\n bounds: 0,5,5,3\n offsets: 1,0,7,4\n"
)
MESH = {"Head": {"uvs": [0, 0, 0.5, 0, 0.5, 1, 0, 1], "triangles": [0, 1, 2, 0, 2, 3], "hull_length": 4}}


def page():
    return Image.frombytes(
        "RGBA", (16, 8),
        bytes(v for y in range(8) for x in range(16) for v in (x * 15, y * 30, 100, 255 if x % 5 else 128)),
    )


def sprite(width, height, seed):
    return Image.frombytes(
        "RGBA", (width, height),
        bytes(v for y in range(height) for x in range(width) for v in ((x + seed) * 20 % 256, y * 40, seed, 255)),
    )


def pixels(img):
    img = img if isinstance(img, Image.Image) else Image.open(io.BytesIO(img))
    return img.size, img.convert("RGBA").tobytes()


SEQUENCES = {
    "mod": [lambda s: s.apply_mod_image(sprite(6, 5, 1), ["Head"])],
    "composite": [lambda s: s.apply_mod_image(sprite(14, 6, 2), ["Head", "Body"])],
    "structural": [
        lambda s: s.rename_region("Head", "H2"),
        lambda s: s.add_region(sprite(3, 3, 3), "Head"),
        lambda s: s.add_region(sprite(2, 4, 4), "Head_2"),
        lambda s: s.remove_regions(["Head_2"]),
        lambda s: s.rename_region("Head_2_2", "Z"),
    ],
    "mixed": [
        lambda s: s.apply_mod_image(sprite(6, 5, 5), ["Head"]),
        lambda s: s.rename_region("Body", "X"),
        lambda s: s.rename_region("Head", "Body"),
        lambda s: s.add_region(sprite(4, 2, 6), "New"),
        lambda s: s.remove_regions(["Arm"]),
        lambda s: s.apply_mod_image(sprite(8, 6, 7), ["Body"]),
    ],
    "unknown_key": [lambda s: s.apply_mod_image(sprite(6, 5, 8), ["Nope"])],
}


def run(defer, steps, nest, gap, mesh_aware):
    session = AtlasSession.load_images(ATLAS, {"p.png": page()}, PillowRasterBackend(), defer_repack=defer)
    session.set_mesh_data(MESH)
    if not defer:  # immediate: options first, every edit repacks with them
        session.set_nest_regions_enabled(nest)
        session.set_nest_gap_distance(gap)
        session.set_mesh_aware_repack_enabled(mesh_aware)
    for step in steps:
        step(session)
    if defer:  # deferred: options after the edits, read at pack time
        session.set_nest_regions_enabled(nest)
        session.set_nest_gap_distance(gap)
        session.set_mesh_aware_repack_enabled(mesh_aware)
        text, pages = session.pack_pending()
    else:
        text, pages = session.save()
    return text, {name: pixels(img) for name, img in pages.items()}


@pytest.mark.parametrize("name", list(SEQUENCES))
@pytest.mark.parametrize("nest,gap,mesh_aware", [(False, 4, False), (True, 2, False), (True, 6, True), (False, 4, True)])
def test_deferred_matches_immediate(name, nest, gap, mesh_aware):
    assert run(True, SEQUENCES[name], nest, gap, mesh_aware) == run(False, SEQUENCES[name], nest, gap, mesh_aware)


def test_structural_sequence_keeps_the_rename_of_the_second_add():
    text, _ = run(True, SEQUENCES["structural"], False, 4, False)
    names = [line for line in text.splitlines() if line and not line.startswith((" ", "p.png", "size"))]
    # The first add ("Head", key Head_2) was removed; the second (key Head_2_2) was renamed to Z.
    assert sorted(names) == ["Arm", "Body", "H2", "Z"]


def test_deferred_edits_never_pack_and_pack_pending_never_publishes(monkeypatch):
    session = AtlasSession.load_images(ATLAS, {"p.png": page()}, PillowRasterBackend(), defer_repack=True)
    calls = []
    original = AtlasSession._build_pages
    monkeypatch.setattr(AtlasSession, "_build_pages", lambda self, b, s: calls.append(1) or original(self, b, s))
    for step in SEQUENCES["mixed"]:
        step(session)
    session.set_nest_regions_enabled(True)
    session.set_nest_gap_distance(3)
    session.set_mesh_aware_repack_enabled(True)
    assert calls == []
    labels = session.region_labels()
    snap = session.snapshot()
    first = session.pack_pending()
    second = session.pack_pending()
    assert len(calls) == 2
    assert first[0] == second[0]
    assert session.region_labels() == labels
    assert session.snapshot() == snap and not snap.has_output
    assert pixels(session.export_pages()["p.png"]) == pixels(page())
    assert session.has_pending_edits


def test_pack_pending_releases_its_allocations_on_success_and_failure(monkeypatch):
    raster = PillowRasterBackend()
    session = AtlasSession.load_images(ATLAS, {"p.png": page()}, raster, defer_repack=True)
    session.apply_mod_image(sprite(6, 5, 1), ["Head"])
    live = set(raster._images)
    session.pack_pending()
    assert set(raster._images) == live

    def broken(self, batches, sprites):
        self._raster.create(3, 3)
        raise RuntimeError("pack failed")

    monkeypatch.setattr(AtlasSession, "_build_pages", broken)
    with pytest.raises(RuntimeError):
        session.pack_pending()
    assert set(raster._images) == live


def test_unedited_pack_pending_returns_pristine_text_and_pages():
    session = AtlasSession.load_images(ATLAS, {"p.png": page()}, PillowRasterBackend(), defer_repack=True)
    text, pages = session.pack_pending()
    assert text == session.save()[0]
    assert pixels(pages["p.png"]) == pixels(page())
    assert not session.has_pending_edits


def test_deferred_remove_all_raises_and_records_nothing():
    session = AtlasSession.load_images(ATLAS, {"p.png": page()}, PillowRasterBackend(), defer_repack=True)
    session.add_region(sprite(2, 2, 1), "New")
    before = session.region_labels()
    with pytest.raises(RemoveAllRegionsError):
        session.remove_regions(["Head", "Body", "Arm", "New"])
    assert session.region_labels() == before
    session.remove_regions(["Head", "Body", "Arm"])
    assert session.region_labels() == [("New", "New")]


def test_region_name_error_carries_reason_in_both_modes():
    for defer in (True, False):
        session = AtlasSession.load_images(ATLAS, {"p.png": page()}, PillowRasterBackend(), defer_repack=defer)
        with pytest.raises(RegionNameError) as info:
            session.rename_region("Head", "Body")
        assert info.value.reason == "'Body' is already in use."
        assert isinstance(info.value, ValueError)


def test_region_labels_matches_snapshot():
    session = AtlasSession.load_images(ATLAS, {"p.png": page()}, PillowRasterBackend(), defer_repack=True)
    for step in SEQUENCES["mixed"]:
        step(session)
    assert session.region_labels() == [(r.key, r.label) for r in session.snapshot().regions]


def test_deferred_repack_and_option_setters():
    session = AtlasSession.load_images(ATLAS, {"p.png": page()}, PillowRasterBackend(), defer_repack=True)
    session.apply_mod_image(sprite(6, 5, 1), ["Head"])
    assert session.set_nest_regions_enabled(True) is None
    with pytest.raises(RuntimeError):
        session.repack()


def test_deferred_session_close_disposes_every_recorded_image():
    raster = PillowRasterBackend()
    session = AtlasSession.load_images(ATLAS, {"p.png": page()}, raster, defer_repack=True)
    for step in SEQUENCES["mixed"]:
        step(session)
    assert len(raster._images) > 1
    session.close()
    assert raster._images == {}


def test_mod_batches_hold_no_prepared_copy_and_an_overridden_copy_is_released():
    raster = PillowRasterBackend()
    session = AtlasSession.load_images(ATLAS, {"p.png": page()}, raster, defer_repack=True)
    session.apply_mod_image(sprite(6, 5, 1), ["Head"])
    first = session._modded_sprites["Head"]
    session.apply_mod_image(sprite(6, 5, 2), ["Head"])
    assert set(session._mod_batches[0]) == {"type", "names", "source", "is_full_canvas", "composite"}
    assert first not in raster._images                      # no name points at it any more
    assert session._mod_batches[0]["source"] in raster._images
    assert session._modded_sprites["Head"] in raster._images
