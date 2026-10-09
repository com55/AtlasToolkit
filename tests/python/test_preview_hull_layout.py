"""View Mode hull overlay: geometry helpers and layout, against the real
www/js exports (plain-Node oracle) and against actual extraction sizes."""
import io
import random
from types import SimpleNamespace

import pytest
from PIL import Image

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.domain.region_ops import extracted_canvas_size
from atlas_toolkit.engine.extraction import (
    AtlasProcessor, hull_polyline, layout_preview_hulls, pick_force_resize_target,
    preview_composite_frame,
)
from atlas_toolkit.engine.session import AtlasSession
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend



def image(raster, width, height):
    """Patterned RGBA page: each pixel encodes its own (x, y)."""
    pixels = bytes(v for y in range(height) for x in range(width) for v in (x, y, 100, 255))
    buf = io.BytesIO()
    Image.frombytes("RGBA", (width, height), pixels).save(buf, format="PNG")
    return raster.decode_png(buf.getvalue())

SCALES = [(1.0, 1.0), (1.5, 1.0), (0.75, 0.75), (0.5, 1.5), (1.25, 0.625)]
REGIONS = [
    {"x": 1, "y": 2, "w": 5, "h": 3, "offsets": None},
    {"x": 1, "y": 2, "w": 5, "h": 3, "offsets": [1, 2, 9, 7]},
    {"x": 3, "y": 1, "w": 7, "h": 4, "offsets": [3, 1, 13, 6]},
]


def test_extracted_canvas_size_matches_js(run_preview_oracle):
    cases = [
        (region, rotate, scale)
        for region in REGIONS for rotate in (0, 90, 180, 270) for scale in SCALES
    ]
    expected = run_preview_oracle([
        ("extractedCanvasSize", [{**region, "rotate": rotate}, {"scaleX": sx, "scaleY": sy}])
        for region, rotate, (sx, sy) in cases
    ])
    for (region, rotate, (sx, sy)), want in zip(cases, expected):
        got = extracted_canvas_size(SimpleNamespace(**region, rotate=rotate), sx, sy)
        assert got == (want["width"], want["height"]), (region, rotate, sx, sy)


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


def _random_entries(rng, count):
    entries = []
    for _ in range(count):
        points = rng.randint(1, 7)
        uvs = [round(rng.random(), 4) for _ in range(points * 2)] if rng.random() < 0.85 else None
        hull = rng.choice([None, 0, 2, 3, points, points + 2, max(points - 1, 0)])
        entries.append({
            "width": rng.randint(1, 40), "height": rng.randint(1, 40),
            "uvs": uvs, "hull_length": hull,
        })
    return entries


def test_layout_and_polylines_match_js(run_preview_oracle):
    rng = random.Random(4071)
    cases = [(_random_entries(rng, rng.randint(0, 4)), rng.random() < 0.6) for _ in range(150)]
    # Equal-area tie (earlier wins) and a target that shrinks one axis.
    cases.append(([
        {"width": 4, "height": 2, "uvs": [0, 0, 1, 0, 1, 1], "hull_length": 3},
        {"width": 2, "height": 4, "uvs": [0, 0, 1, 0, 0, 1], "hull_length": 3},
    ], True))
    cases.append(([
        {"width": 10, "height": 30, "uvs": [0, 0, 1, 0, 1, 1], "hull_length": 3},
        {"width": 20, "height": 20, "uvs": [0, 0, 1, 0, 0, 1], "hull_length": 3},
    ], True))
    expected = run_preview_oracle([
        ("layoutPreviewHulls", [[
            {"width": e["width"], "height": e["height"], "uvs": e["uvs"], "hullLength": e["hull_length"]}
            for e in entries
        ], force])
        for entries, force in cases
    ])
    for (entries, force), want in zip(cases, expected):
        got = layout_preview_hulls(entries, force)
        assert got["frame"] == want["frame"]
        assert [
            (i["width"], i["height"], i["scale_x"], i["scale_y"], i["uvs"], i["hull_length"])
            for i in got["items"]
        ] == [
            (i["width"], i["height"], i["scaleX"], i["scaleY"], i["uvs"], i["hullLength"])
            for i in want["items"]
        ]
        assert [hull_polyline(i) for i in got["items"]] == [
            None if pts is None else [(pt["x"], pt["y"]) for pt in pts]
            for pts in want["polylines"]
        ]


def test_frame_and_target_match_js(run_preview_oracle):
    rng = random.Random(77)
    size_lists = [
        [(rng.randint(1, 9), rng.randint(1, 9)) for _ in range(rng.randint(1, 5))]
        for _ in range(100)
    ] + [[(4, 2), (2, 4)], [(2, 4), (4, 2)], [(3, 3)]]
    as_js = [[{"width": w, "height": h} for w, h in sizes] for sizes in size_lists]
    expected = run_preview_oracle(
        [("pickForceResizeTarget", [sizes]) for sizes in as_js]
        + [("previewCompositeFrame", [sizes, force]) for sizes in as_js for force in (False, True)]
    )
    targets, frames = expected[:len(size_lists)], expected[len(size_lists):]
    for sizes, want in zip(size_lists, targets):
        assert pick_force_resize_target(sizes) == (want["width"], want["height"])
    frame_cases = [(sizes, force) for sizes in size_lists for force in (False, True)]
    for (sizes, force), want in zip(frame_cases, frames):
        assert preview_composite_frame(sizes, force) == want


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
