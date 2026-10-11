"""AtlasSession with in-memory (PIL) pages: same results as the PNG paths, no PNG work."""
import io

from PIL import Image

from atlas_toolkit.engine.session import AtlasSession
from atlas_toolkit.raster.pillow_backend import PillowRasterBackend

ATLAS = "p.png\nsize: 8,4\na\n bounds: 0,0,4,4\nb\n bounds: 4,0,4,4\n"


def page():
    return Image.frombytes(
        "RGBA", (8, 4), bytes(v for y in range(4) for x in range(8) for v in (x * 30, y * 60, 90, 255)),
    )


def png(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def pixels(img_or_png):
    img = img_or_png if isinstance(img_or_png, Image.Image) else Image.open(io.BytesIO(img_or_png))
    return img.convert("RGBA").tobytes()


class CountingRaster(PillowRasterBackend):
    def __init__(self):
        super().__init__()
        self.png_calls = 0

    def decode_png(self, data):
        self.png_calls += 1
        return super().decode_png(data)

    def encode_png(self, handle):
        self.png_calls += 1
        return super().encode_png(handle)


def test_in_memory_reads_match_png_reads_and_do_no_png_work():
    counting = CountingRaster()
    mem = AtlasSession.load_images(ATLAS, {"p.png": page()}, counting)
    ref = AtlasSession.load(ATLAS, {"p.png": png(page())}, PillowRasterBackend())
    got = mem.extract_images(["b", "a"])
    want = ref.extract_regions(["b", "a"])
    assert list(got) == ["b", "a"]
    assert all(pixels(got[k]) == pixels(want[k]) for k in want)
    assert pixels(mem.get_preview_image(["a", "b"], True)) == pixels(ref.get_preview_png(["a", "b"], True))
    assert pixels(mem.export_pages()["p.png"]) == pixels(page())
    assert counting.png_calls == 0
    mem.close()
    ref.close()


def test_in_memory_edits_match_png_edits():
    sprite = page().crop((0, 0, 4, 4)).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    mem = AtlasSession.load_images(ATLAS, {"p.png": page()}, PillowRasterBackend())
    ref = AtlasSession.load(ATLAS, {"p.png": png(page())}, PillowRasterBackend())
    mem.apply_mod_image(sprite, ["a"])
    ref.apply_mod_image(png(sprite), ["a"])
    mem.add_region(sprite, "c")
    ref.add_region(png(sprite), "c")
    mem_text, mem_pages = mem.save()
    ref_text, ref_pages = ref.save()
    assert mem_text == ref_text
    assert pixels(mem_pages["p.png"]) == pixels(ref_pages["p.png"])


def test_reads_release_their_temporaries_and_close_disposes_adopted_pages():
    raster = PillowRasterBackend()
    session = AtlasSession.load_images(ATLAS, {"p.png": page()}, raster)
    roots = set(raster._images)
    session.extract_images(["a", "b"])
    session.get_preview_image(["a", "b"], True)
    assert set(raster._images) == roots
    session.close()
    assert raster._images == {}


def test_load_images_failure_releases_adopted_pages():
    raster = PillowRasterBackend()
    text = "p.png\nsize: 8,4\na\n bounds: 0,0,4,4\n\nq.png\nsize: 4,4\nb\n bounds: 0,0,4,4\n"
    try:
        AtlasSession.load_images(text, {"p.png": page()}, raster)
    except ValueError as error:
        assert "q.png" in str(error)
    assert raster._images == {}


def test_caller_image_is_never_shared():
    source = page()
    session = AtlasSession.load_images(ATLAS, {"p.png": source}, PillowRasterBackend())
    source.putpixel((0, 0), (1, 1, 1, 1))
    assert session.extract_images(["a"])["a"].getpixel((0, 0)) == (0, 0, 90, 255)
    out = session.export_pages()["p.png"]
    out.putpixel((0, 0), (2, 2, 2, 2))
    assert session.export_pages()["p.png"].getpixel((0, 0)) == (0, 0, 90, 255)
