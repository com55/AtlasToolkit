from atlas_toolkit.domain.converter import (
    is_old_format,
    convert_atlas_to_new_format,
    auto_convert_atlas,
)


def test_is_old_format_detects_xy_orig_offset_keys():
    assert is_old_format("page.png\nxy: 0, 0\n") is True
    assert is_old_format("page.png\norig: 10, 10\n") is True
    assert is_old_format("page.png\noffset: 1, 1\n") is True


def test_is_old_format_false_for_new_format():
    assert is_old_format("page.png\nsize: 10,10\nregion\nbounds: 0, 0, 5, 5\n") is False


def test_convert_libgdx_xy_size_to_bounds():
    text = "page.png\nsize: 10,10\nregion\nxy: 3, 4\nsize: 5, 6\n"
    out = convert_atlas_to_new_format(text)
    lines = out.split("\n")
    assert "page.png" in lines
    assert "region" in lines
    assert "  bounds: 3, 4, 5, 6" in lines


def test_convert_libgdx_orig_offset_to_offsets():
    text = "page.png\nsize: 10,10\nregion\nxy: 0, 0\nsize: 5, 5\norig: 20, 20\noffset: 2, 3\n"
    out = convert_atlas_to_new_format(text)
    assert "  offsets: 2, 3, 20, 20" in out.split("\n")


def test_convert_orig_without_offset_defaults_offset_to_0_0():
    text = "page.png\nsize: 10,10\nregion\nxy: 0, 0\nsize: 5, 5\norig: 20, 20\n"
    out = convert_atlas_to_new_format(text)
    assert "  offsets: 0, 0, 20, 20" in out.split("\n")


def test_convert_already_new_format_bounds_passes_through():
    text = "page.png\nsize: 10,10\nregion\nbounds: 1, 2, 3, 4\n"
    out = convert_atlas_to_new_format(text)
    assert "  bounds: 1, 2, 3, 4" in out.split("\n")


def test_convert_rotate_defaults_to_false_when_absent():
    text = "page.png\nsize: 10,10\nregion\nbounds: 0, 0, 5, 5\n"
    out = convert_atlas_to_new_format(text)
    assert "  rotate: false" in out.split("\n")


def test_convert_rotate_true_passes_through():
    text = "page.png\nsize: 10,10\nregion\nrotate: true\nbounds: 0, 0, 5, 5\n"
    out = convert_atlas_to_new_format(text)
    assert "  rotate: true" in out.split("\n")


def test_convert_index_split_pad_pass_through():
    text = (
        "page.png\nsize: 10,10\nregion\nindex: 2\nbounds: 0, 0, 5, 5\n"
        "split: 1, 2, 3, 4\npad: 0, 0, 0, 0\n"
    )
    out = convert_atlas_to_new_format(text)
    lines = out.split("\n")
    assert "  index: 2" in lines
    assert "  split: 1, 2, 3, 4" in lines
    assert "  pad: 0, 0, 0, 0" in lines


def test_convert_unknown_region_key_survives():
    text = "page.png\nsize: 10,10\nregion\nbounds: 0, 0, 5, 5\nmystery: 1, 2\n"
    out = convert_atlas_to_new_format(text)
    assert "  mystery: 1, 2" in out.split("\n")


def test_convert_page_level_size_key_untouched():
    text = "page.png\nsize: 10,10\nregion\nbounds: 0, 0, 5, 5\n"
    out = convert_atlas_to_new_format(text)
    assert "size: 10,10" in out.split("\n")


def test_convert_multi_region_and_blank_line_flushes_correctly():
    text = (
        "page.png\nsize: 10,10\n"
        "regionA\nxy: 0, 0\nsize: 5, 5\n"
        "\n"
        "regionB\nxy: 5, 0\nsize: 5, 5\n"
    )
    out = convert_atlas_to_new_format(text)
    lines = out.split("\n")
    assert "regionA" in lines
    assert "regionB" in lines
    assert "  bounds: 0, 0, 5, 5" in lines
    assert "  bounds: 5, 0, 5, 5" in lines


def test_auto_convert_atlas_converts_old_format():
    text = "page.png\nsize: 10,10\nregion\nxy: 0, 0\nsize: 5, 5\n"
    out = auto_convert_atlas(text)
    assert "  bounds: 0, 0, 5, 5" in out.split("\n")


def test_auto_convert_atlas_passes_through_new_format_unchanged():
    text = "page.png\nsize: 10,10\nregion\nbounds: 0, 0, 5, 5\n"
    assert auto_convert_atlas(text) == text


def test_is_page_line_is_png_only_case_insensitive_not_other_image_extensions():
    # Deliberate divergence from the older Python original's broader
    # png|jpg|jpeg|webp|bmp|gif match -- current JS (this port's oracle)
    # matches .png only, case-insensitively (_is_page_line lowercases
    # before checking .endswith(".png")).
    #
    # ".PNG" (any case) IS a page line: a region that follows attaches to
    # it as a page, so xy:/size: get converted into an indented "  bounds:"
    # line under the region name, same as a lowercase ".png" would.
    out_png = convert_atlas_to_new_format("photo.PNG\nsize: 10,10\nregion\nxy: 0, 0\nsize: 5, 5\n")
    lines_png = out_png.split("\n")
    assert "photo.PNG" in lines_png
    assert "region" in lines_png
    assert "  bounds: 0, 0, 5, 5" in lines_png

    # ".jpg" is NOT a page line -- it's a bare line with no colon, so it
    # falls through to the SAME "no colon -> region name" branch "region"
    # took above (there is no guard requiring a page to be open first);
    # "photo.jpg" itself becomes the region name and its own xy:/size:
    # convert to a bounds: line, exactly as if it were a region under an
    # (absent) page.
    out_jpg = convert_atlas_to_new_format("photo.jpg\nxy: 0, 0\nsize: 1, 1\n")
    lines_jpg = out_jpg.split("\n")
    assert "photo.jpg" in lines_jpg
    assert "  bounds: 0, 0, 1, 1" in lines_jpg
