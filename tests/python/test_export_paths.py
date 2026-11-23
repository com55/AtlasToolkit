# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Extracted regions get one distinct, safe relative path each."""
import pytest

from atlas_toolkit_app.host.export_paths import export_paths


def test_slashes_make_folders():
    assert export_paths(["head/face", "head/eye", "body"]) == ["head/face.png", "head/eye.png", "body.png"]


def test_names_that_used_to_collide_stay_apart():
    assert export_paths(["head/face", "head_face"]) == ["head/face.png", "head_face.png"]


def test_case_only_and_mapped_character_clashes_get_a_suffix():
    assert export_paths(["Head", "head", "HEAD"]) == ["Head.png", "head (2).png", "HEAD (3).png"]
    assert export_paths(["a:b", "a_b", "a?b"]) == ["a_b.png", "a_b (2).png", "a_b (3).png"]


def test_a_file_never_lands_on_a_folder_and_a_folder_never_on_a_file():
    assert export_paths(["a.png/b", "a"]) == ["a.png/b.png", "a (2).png"]
    assert export_paths(["a", "a.png/b"]) == ["a.png", "a.png_b.png"]


@pytest.mark.parametrize("name, path", [
    ("/lead", "lead.png"), ("trail/", "trail.png"), ("a//b", "a/b.png"),
    ("../up", "_/up.png"), ("./here", "_/here.png"), ("", "_.png"), ("/", "_.png"),
    ("dir./x", "dir/x.png"), ("dir /x", "dir/x.png"), ("con", "_con.png"),
    ("aux/x", "_aux/x.png"), ("CONSOLE", "CONSOLE.png"), ("a\\b", "a_b.png"),
    ("頭/顔", "頭/顔.png"),
])
def test_every_segment_is_safe(name, path):
    assert export_paths([name]) == [path]
