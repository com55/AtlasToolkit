# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""View Mode hull overlay: geometry helpers and layout, against actual
extraction sizes."""
import io

import pytest
from PIL import Image

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.domain.region_ops import extracted_canvas_size
from atlas_toolkit.engine.extraction import (
    AtlasProcessor, hull_polyline, pick_force_resize_target,
)
from atlas_toolkit.engine.session import AtlasSession
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend



def image(raster, width, height):
    """Patterned RGBA page: each pixel encodes its own (x, y)."""
    pixels = bytes(v for y in range(height) for x in range(width) for v in (x, y, 100, 255))
    buf = io.BytesIO()
    Image.frombytes("RGBA", (width, height), pixels).save(buf, format="PNG")
    return raster.decode_png(buf.getvalue())

REGIONS = [
    {"x": 1, "y": 2, "w": 5, "h": 3, "offsets": None},
    {"x": 1, "y": 2, "w": 5, "h": 3, "offsets": [1, 2, 9, 7]},
    {"x": 3, "y": 1, "w": 7, "h": 4, "offsets": [3, 1, 13, 6]},
]


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
@pytest.mark.parametrize("actual", [(20, 16), (30, 16), (15, 12), (10, 24), (25, 10)])
@pytest.mark.parametrize("region", REGIONS)
def test_extracted_canvas_size_is_the_real_extraction_size(region, actual, rotate):
    raster = PillowRasterBackend()
    offsets = f"\n offsets: {','.join(map(str, region['offsets']))}" if region["offsets"] else ""
    body = (
        f"r\n bounds: {region['x']},{region['y']},{region['w']},{region['h']}"
        f"\n rotate: {rotate}{offsets}"
    )
    doc = AtlasDocument.parse(f"p.png\nsize: 20,16\n{body}")
    p = AtlasProcessor(doc, {"p.png": image(raster, *actual)}, raster)
    out = p.extract_region("r")
    sx, sy = actual[0] / 20, actual[1] / 16
    assert extracted_canvas_size(p.regions["r"], sx, sy) == raster.size(out)
    layout = p.preview_hull_layout(["r"], False)
    assert (layout["items"][0]["width"], layout["items"][0]["height"]) == raster.size(out)


def test_equal_area_keeps_the_earlier_entry():
    assert pick_force_resize_target([(4, 2), (2, 4)]) == (4, 2)
    assert pick_force_resize_target([(2, 4), (4, 2)]) == (2, 4)


BOX_MESH = {"uvs": [0, 0, 1, 0, 1, 1, 0, 1, 0.5, 0.5], "triangles": [0, 1, 4], "hull_length": 4}


def test_processor_hull_layout_ignores_cropping_toggle_and_skips_unloaded_page():
    raster = PillowRasterBackend()
    doc = AtlasDocument.parse(
        "p.png\nsize: 20,16\nbox\n bounds: 1,2,4,6\n offsets: 1,1,10,12\n rotate: 90\n"
        "\nq.png\nsize: 10,10\nmissing\n bounds: 0,0,3,3\n"
    )
    p = AtlasProcessor(doc, {"p.png": image(raster, 20, 16)}, raster)
    p.set_mesh_mask_data(
        {"box": BOX_MESH, "missing": {"uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2], "hull_length": 3}},
        enabled=False, repack_enabled=False,
    )
    layout = p.preview_hull_layout(["box", "missing", "nope"], True)
    assert layout["frame"] == {"width": 10, "height": 12, "scale": False}
    assert len(layout["items"]) == 1
    assert hull_polyline(layout["items"][0]) == [(0, 0), (10, 0), (10, 12), (0, 12)]


def test_session_hull_layout_force_resize_stretches_smaller_region():
    raster = PillowRasterBackend()
    text = "p.png\nsize: 20,16\nbig\n bounds: 0,0,8,4\nsmall\n bounds: 10,0,2,4\n"
    session = AtlasSession.load(text, {"p.png": raster.encode_png(image(raster, 20, 16))}, raster)
    session.set_mesh_data({"small": BOX_MESH})
    plain = session.get_mesh_hull_layout(["big", "small"])
    forced = session.get_mesh_hull_layout(["big", "small"], force_resize=True)
    assert plain["frame"] == {"width": 8, "height": 4, "scale": False}
    assert forced["frame"] == {"width": 8, "height": 4, "scale": True}
    assert hull_polyline(plain["items"][1]) == [(0, 0), (2, 0), (2, 4), (0, 4)]
    assert hull_polyline(forced["items"][1]) == [(0, 0), (8, 0), (8, 4), (0, 4)]
    assert hull_polyline(forced["items"][0]) is None
    session.close()


def test_returned_uvs_are_a_copy_of_the_session_mesh_data():
    raster = PillowRasterBackend()
    text = "p.png\nsize: 4,4\nr\n bounds: 0,0,4,4\n"
    session = AtlasSession.load(text, {"p.png": raster.encode_png(image(raster, 4, 4))}, raster)
    session.set_mesh_data({"r": dict(BOX_MESH, triangles=[0, 1, 2, 0, 2, 3])})
    session.set_mesh_mask_enabled(True)
    before = session.extract_regions(["r"])["r"]
    layout = session.get_mesh_hull_layout(["r"])
    layout["items"][0]["uvs"][:] = [0.0] * len(layout["items"][0]["uvs"])
    assert session.extract_regions(["r"])["r"] == before
    again = session.get_mesh_hull_layout(["r"])
    assert hull_polyline(again["items"][0]) == [(0, 0), (4, 0), (4, 4), (0, 4)]
    session.close()
