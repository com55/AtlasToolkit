"""Processor geometry, ownership and state against literal pixel expectations."""
import copy
import io

import pytest
from PIL import Image

from atlas_toolkit.document import AtlasDocument
from atlas_toolkit.engine.extraction import AtlasProcessor
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend


def image(raster, width=8, height=6, pixels=None):
    if pixels is None:
        pixels = bytes(v for y in range(height) for x in range(width) for v in (x, y, 100, 255))
    buf = io.BytesIO()
    Image.frombytes("RGBA", (width, height), bytes(pixels)).save(buf, format="PNG")
    return raster.decode_png(buf.getvalue())


def processor(raster, body="r\n  bounds: 1,1,3,2", declared="8,6", pixels=None, actual=(8, 6)):
    doc = AtlasDocument.parse(f"p.png\nsize: {declared}\n{body}")
    source = image(raster, *actual, pixels)
    return AtlasProcessor(doc, {"p.png": source}, raster), source


def test_borrowed_pages_copied_mapping_and_raw_numeric_key_order():
    raster = PillowRasterBackend()
    source = image(raster)
    doc = AtlasDocument.parse("p.png\nsize: 8,6\n10\n bounds: 1,1,3,2\n2\n bounds: 0,0,1,1\na\n bounds: 0,0,2,2")
    mapping = {"extra.png": source, "p.png": source}
    p = AtlasProcessor(doc, mapping, raster)
    mapping.clear()
    assert p.document is doc
    assert p.pages == doc.pages
    assert list(p.regions) == ["2", "10", "a"]
    assert p.regions["10"] is doc.regions_by_key()["10"]
    assert p.get_page_image() == source
    assert p.get_page_image("p.png") == source
    assert p.get_page_image("missing") is None
    assert AtlasProcessor(doc, {}, raster).get_page_image() is None


@pytest.mark.parametrize(("rotation", "coordinates"), [
    (0, [(1, 1), (2, 1), (3, 1), (1, 2), (2, 2), (3, 2)]),
    (90, [(1, 3), (1, 2), (1, 1), (2, 3), (2, 2), (2, 1)]),
    (180, [(3, 2), (2, 2), (1, 2), (3, 1), (2, 1), (1, 1)]),
    (270, [(2, 1), (2, 2), (2, 3), (1, 1), (1, 2), (1, 3)]),
])
def test_patterned_rectangular_rotation_is_fresh(rotation, coordinates):
    raster = PillowRasterBackend()
    p, source = processor(raster, f"r\n bounds: 1,1,3,2\n rotate: {rotation}")
    before = raster.get_pixels(source)
    out = p.extract_region("r")
    assert out != source
    assert raster.size(out) == (3, 2)
    assert raster.get_pixels(out) == bytes(v for x, y in coordinates for v in (x, y, 100, 255))
    raster.dispose(out)
    assert raster.get_pixels(source) == before
    assert set(raster._images) == {source}


def test_unknown_region_or_missing_page_returns_none():
    raster = PillowRasterBackend()
    p, _ = processor(raster)
    assert p.extract_region("absent") is None
    assert AtlasProcessor(p.document, {}, raster).extract_region("r") is None


@pytest.mark.parametrize("declared", ["0,3", "4,0", "0,0"])
def test_either_zero_declared_dimension_disables_both_scales(declared):
    raster = PillowRasterBackend()
    p, _ = processor(raster, declared=declared)
    out = p.extract_region("r")
    assert raster.size(out) == (3, 2)
    assert raster.get_pixels(out)[:4] == bytes([1, 1, 100, 255])


def test_non_square_scaling_half_even_and_pristine_metadata():
    raster = PillowRasterBackend()
    p, _ = processor(raster, "r\n bounds: 1,3,3,5", declared="8,4", actual=(12, 2))
    before = copy.deepcopy(p.document.pages)
    out = p.extract_region("r")
    # sx=1.5, sy=0.5: x=2, y=2, w=4, h=2; the crop lies below this page.
    assert raster.size(out) == (4, 2)
    assert raster.get_pixels(out) == bytes(32)
    assert p.document.pages == before
    # Pin nonempty per-axis coordinates separately from the out-of-bounds crop.
    p, _ = processor(raster, "r\n bounds: 1,1,3,3", declared="8,4", actual=(12, 2))
    before = copy.deepcopy(p.document.pages)
    out = p.extract_region("r")
    assert raster.size(out) == (4, 2)
    assert raster.get_pixels(out) == bytes(v for y in (0, 1) for x in (2, 3, 4, 5) for v in (x, y, 100, 255))
    assert p.document.pages == before


@pytest.mark.parametrize(("offsets", "expected_size", "positions"), [
    ("1,1,6,5", (6, 5), {(1, 2): (1, 1), (2, 2): (2, 1), (3, 2): (3, 1),
                          (1, 3): (1, 2), (2, 3): (2, 2), (3, 3): (3, 2)}),
    ("-1,1,2,2", (2, 2), {(0, 0): (2, 2), (1, 0): (3, 2)}),
])
def test_offset_bottom_edge_flip_padding_and_clipping(offsets, expected_size, positions):
    raster = PillowRasterBackend()
    p, _ = processor(raster, f"r\n bounds: 1,1,3,2\n offsets: {offsets}")
    before = copy.deepcopy(p.document.pages)
    out = p.extract_region("r")
    assert raster.size(out) == expected_size
    expected = bytes(v for y in range(expected_size[1]) for x in range(expected_size[0])
                     for v in ((*positions[(x, y)], 100, 255) if (x, y) in positions else (0, 0, 0, 0)))
    assert raster.get_pixels(out) == expected
    assert p.document.pages == before


def test_scaled_offsets_round_each_axis_before_padding():
    raster = PillowRasterBackend()
    p, _ = processor(raster, "r\n bounds: 1,1,3,3\n offsets: 1,1,5,7", declared="8,4", actual=(12, 2))
    before = copy.deepcopy(p.document.pages)
    out = p.extract_region("r")
    assert raster.size(out) == (8, 4)
    expected = bytes(v for y in range(4) for x in range(8)
                     for v in ((x, y - 2, 100, 255) if 2 <= x < 6 and y >= 2 else (0, 0, 0, 0)))
    assert raster.get_pixels(out) == expected
    assert p.document.pages == before


class FaultRaster(PillowRasterBackend):
    """Real pixels, bounded allocations and failures at a chosen protocol call."""
    def __init__(self):
        super().__init__()
        self.fail = None
        self.remaining = 1
        self.calls = []

    def _check(self, op, *args):
        self.calls.append((op, args))
        assert len(self._images) < 12
        if self.fail == op:
            self.remaining -= 1
            if self.remaining == 0:
                raise RuntimeError(f"injected {op}")

    def crop_rotate(self, handle, x, y, w, h, rotate):
        self._check("crop", handle, x, y, w, h, rotate)
        return super().crop_rotate(handle, x, y, w, h, rotate)

    def create(self, width, height):
        self._check("create", width, height)
        return super().create(width, height)

    def paste(self, dest, src, x, y):
        self._check("paste", dest, src, x, y)
        return super().paste(dest, src, x, y)

    def rasterize_mesh_mask(self, uvs, triangles, width, height):
        self._check("mask", width, height)
        return super().rasterize_mesh_mask(uvs, triangles, width, height)

    def composite_destination_in(self, dest, mask, src_rect=None):
        self._check("composite", dest, mask, src_rect)
        return super().composite_destination_in(dest, mask, src_rect)

    def encode_png(self, handle):
        raise AssertionError("processor must never encode PNG")


@pytest.mark.parametrize("operation", ["crop", "create", "paste"])
def test_offset_failure_releases_owned_intermediates(operation):
    raster = FaultRaster()
    p, source = processor(raster, "r\n bounds: 1,1,3,2\n offsets: 1,1,6,5")
    before = raster.get_pixels(source)
    raster.fail = operation
    with pytest.raises(RuntimeError, match=f"injected {operation}"):
        p.extract_region("r")
    assert set(raster._images) == {source}
    assert raster.get_pixels(source) == before


LEFT_HALF = {"uvs": [0, 0, 0.5, 0, 0, 1, 0.5, 1], "triangles": [0, 1, 2, 1, 3, 2]}


@pytest.mark.parametrize("padded", [False, True])
def test_mesh_at_output_size_preserves_pattern_and_cuts_right_half(padded):
    raster = FaultRaster()
    body = "r\n bounds: 0,0,4,2" + ("\n offsets: 1,1,6,4" if padded else "")
    p, source = processor(raster, body)
    before = raster.get_pixels(source)
    p.set_mesh_mask_data({"r": LEFT_HALF}, True, False)
    out = p.extract_region("r")
    w, h = raster.size(out)
    assert (w, h) == ((6, 4) if padded else (4, 2))
    data = raster.get_pixels(out)
    retained = 0
    for y in range(h):
        for x in range(w):
            pixel = data[(y * w + x) * 4:(y * w + x + 1) * 4]
            inside = (1 <= x < 3 and 1 <= y < 3) if padded else x < 2
            assert pixel[3] == (255 if inside else 0)
            if inside:
                retained += 1
                assert pixel == bytes((x - int(padded), y - int(padded), 100, 255))
    assert retained == 4
    assert ("mask", (w, h)) in raster.calls
    assert set(raster._images) == {source, out}
    raster.dispose(out)
    assert raster.get_pixels(source) == before


@pytest.mark.parametrize(("extract_enabled", "repack_enabled"), [(False, False), (True, False), (False, True), (True, True)])
def test_mesh_switches_are_independent_and_require_known_non_pma_region(extract_enabled, repack_enabled):
    raster = PillowRasterBackend()
    doc = AtlasDocument.parse("p.png\nsize: 8,6\nr\n bounds: 0,0,4,2\nno_mesh\n bounds: 0,0,4,2\nq.png\nsize: 8,6\npma: true\npma\n bounds: 0,0,4,2")
    source = image(raster)
    p = AtlasProcessor(doc, {"p.png": source, "q.png": source}, raster)
    lookup = {"r": LEFT_HALF, "pma": LEFT_HALF, "absent": LEFT_HALF}
    p.set_mesh_mask_data(lookup, extract_enabled, repack_enabled)
    p.set_nest_options(True, 0)
    assert p.get_mesh_geometry("r") == (LEFT_HALF if extract_enabled else None)
    assert p.get_repack_mesh_geometry("r") == (LEFT_HALF if repack_enabled else None)
    for key in ("pma", "absent", "no_mesh"):
        assert p.get_mesh_geometry(key) is None
        assert p.get_repack_mesh_geometry(key) is None
    assert raster.get_pixels(p.extract_region("pma"))[3::4] == bytes([255] * 8)
    p.set_mesh_mask_data(None, True, True)
    assert p.get_mesh_geometry("r") is None
    assert p.get_repack_mesh_geometry("r") is None
    assert p.get_nest_options() == {"enabled": True, "gap_distance": 0}


def test_mesh_lookup_geometry_lists_are_copied_and_defaults_are_off():
    raster = PillowRasterBackend()
    p, _ = processor(raster)
    assert p.get_nest_options() == {"enabled": False, "gap_distance": 4}
    assert p.get_mesh_geometry("r") is None
    assert p.get_repack_mesh_geometry("r") is None
    lookup = {"r": copy.deepcopy(LEFT_HALF)}
    p.set_mesh_mask_data(lookup, True, True)
    lookup["r"]["uvs"][0] = 0.75
    lookup["r"]["triangles"].clear()
    lookup.clear()
    assert p.get_mesh_geometry("r") == LEFT_HALF
    assert p.get_repack_mesh_geometry("r") == LEFT_HALF
    with pytest.raises(TypeError):
        p.set_mesh_mask_data(None, True)


@pytest.mark.parametrize(("value", "expected"), [(0, 0), (-2, -2), (2.5, 2.5), (True, 4), (False, 4), ("2", 4), (None, 4), (float("nan"), 4), (float("inf"), 4), (-float("inf"), 4)])
def test_nest_gap_is_stored_without_session_normalization(value, expected):
    p, _ = processor(PillowRasterBackend())
    p.set_nest_options(True, value)
    assert p.get_nest_options() == {"enabled": True, "gap_distance": expected}


@pytest.mark.parametrize("value", [10**400, -(10**400)])
def test_nest_gap_preserves_arbitrarily_large_finite_integers(value):
    p, _ = processor(PillowRasterBackend())
    p.set_nest_options(True, value)
    assert p.get_nest_options() == {"enabled": True, "gap_distance": value}


@pytest.mark.parametrize("padded", [False, True])
@pytest.mark.parametrize("operation", ["mask", "composite"])
def test_mesh_failure_cleans_output_and_mask(operation, padded):
    raster = FaultRaster()
    p, source = processor(raster, "r\n bounds: 0,0,4,2" + ("\n offsets: 1,1,6,4" if padded else ""))
    p.set_mesh_mask_data({"r": LEFT_HALF}, True, False)
    before = raster.get_pixels(source)
    raster.fail = operation
    with pytest.raises(RuntimeError, match=f"injected {operation}"):
        p.extract_region("r")
    assert set(raster._images) == {source}
    assert raster.get_pixels(source) == before


def test_preview_reverse_order_max_dimensions_and_partial_alpha():
    raster = FaultRaster()
    # r is translucent red, b is opaque blue. In [r,b], r paints LAST.
    p, source = processor(raster, "r\n bounds: 0,0,1,1\nb\n bounds: 1,0,2,1", declared="3,1", actual=(3, 1),
                          pixels=[255, 0, 0, 128, 0, 0, 255, 255, 0, 255, 0, 255])
    out = p.get_preview_image(["missing", "r", "b"])
    assert raster.size(out) == (2, 1)
    assert raster.get_pixels(out) == bytes([128, 0, 127, 255, 0, 255, 0, 255])
    assert set(raster._images) == {source, out}
    raster.dispose(out)
    out = p.get_preview_image(["b", "r"])
    assert raster.get_pixels(out) == bytes([0, 0, 255, 255, 0, 255, 0, 255])
    raster.dispose(out)
    assert p.get_preview_image([]) is None
    assert p.get_preview_image(["missing"]) is None
    assert AtlasProcessor(p.document, {}, raster).get_preview_image(["r"]) is None
    out = p.get_preview_image(["missing", "r"])
    assert out != source
    assert raster.get_pixels(out) == bytes([255, 0, 0, 128])
    assert set(raster._images) == {source, out}


@pytest.mark.parametrize(("operation", "nth"), [("crop", 2), ("create", 1), ("paste", 1), ("paste", 2)])
def test_preview_failure_releases_prior_extractions_and_composite(operation, nth):
    raster = FaultRaster()
    p, source = processor(raster, "r\n bounds: 0,0,4,2\nb\n bounds: 1,1,2,2")
    before = raster.get_pixels(source)
    raster.fail, raster.remaining = operation, nth
    with pytest.raises(RuntimeError, match=f"injected {operation}"):
        p.get_preview_image(["r", "b"])
    assert set(raster._images) == {source}
    assert raster.get_pixels(source) == before
