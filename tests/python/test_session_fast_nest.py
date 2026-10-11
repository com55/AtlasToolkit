"""Which packer a repack uses: the frontier packer only for nest +
mesh-aware with the fast switch on; everything else stays on the JS port."""
import pytest
from PIL import Image

import atlas_toolkit.engine.modifier as modifier
from atlas_toolkit.engine.session import AtlasSession
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

ATLAS = (
    "p.png\nsize: 16,8\n"
    "Head\n bounds: 0,0,6,5\n"
    "Body\n bounds: 6,0,8,6\n"
    "Arm\n bounds: 0,5,5,3\n"
)
MESH = {"Head": {"uvs": [0, 0, 0.5, 0, 0.5, 1, 0, 1], "triangles": [0, 1, 2, 0, 2, 3], "hull_length": 4}}


def page():
    return Image.frombytes(
        "RGBA", (16, 8), bytes(v for y in range(8) for x in range(16) for v in (x * 15, y * 30, 100, 255)),
    )


@pytest.fixture
def packers(monkeypatch):
    calls = []
    for name in ("nest_pack", "nest_pack_frontier", "shelf_pack"):
        real = getattr(modifier, name)
        monkeypatch.setattr(
            modifier, name,
            lambda *args, _name=name, _real=real, **kwargs: calls.append(_name) or _real(*args, **kwargs),
        )
    return calls


def pack(nest, mesh_aware, fast=None):
    session = AtlasSession.load_images(ATLAS, {"p.png": page()}, PillowRasterBackend(), defer_repack=True)
    session.set_mesh_data(MESH)
    session.apply_mod_image(Image.new("RGBA", (6, 5), (9, 8, 7, 255)), ["Head"])
    if fast is not None:
        session.set_fast_mesh_nest_enabled(fast)
    session.set_nest_regions_enabled(nest)
    session.set_mesh_aware_repack_enabled(mesh_aware)
    try:
        return session.pack_pending()
    finally:
        session.close()


@pytest.mark.parametrize("nest, mesh_aware, fast, used", [
    (True, True, None, "nest_pack_frontier"),
    (True, True, True, "nest_pack_frontier"),
    (True, True, False, "nest_pack"),
    (True, False, None, "nest_pack"),
    (False, True, None, "shelf_pack"),
    (False, False, None, "shelf_pack"),
])
def test_packer_choice(packers, nest, mesh_aware, fast, used):
    pack(nest, mesh_aware, fast)
    assert packers == [used]


def test_fast_switch_does_not_repack_an_immediate_session(packers):
    session = AtlasSession.load_images(ATLAS, {"p.png": page()}, PillowRasterBackend())
    session.set_mesh_data(MESH)
    session.apply_mod_image(Image.new("RGBA", (6, 5), (9, 8, 7, 255)), ["Head"])
    packers.clear()
    assert session.set_fast_mesh_nest_enabled(False) is None
    assert packers == []
    session.close()


def test_processor_nest_options_keep_their_js_shape():
    session = AtlasSession.load_images(ATLAS, {"p.png": page()}, PillowRasterBackend(), defer_repack=True)
    session.set_mesh_data(MESH)
    session.set_nest_regions_enabled(True)
    session.set_mesh_aware_repack_enabled(True)
    assert session._processor.get_nest_options() == {"enabled": True, "gap_distance": 4}
    session.close()
