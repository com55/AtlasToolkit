# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Pending additions accept replacements without becoming mod batches."""
import pytest

import atlas_toolkit.api as api

from atlas_toolkit.api import AtlasError, MixedAlphaGroup, SpineAtlas
from .host_fixtures import decode, one_page, two_pages, page_bytes


def atlas_with_adds(*names, pma=False):
    text, pages = one_page(pma=pma)
    atlas = SpineAtlas.load(text, {name: decode(data) for name, data in pages.items()})
    for name in names:
        atlas.add_region(name, decode(page_bytes(2, 3)))
    return atlas


def test_replace_on_a_pending_add_keeps_its_order_and_key():
    atlas = atlas_with_adds('n1', 'n2')
    keys = [v.key for v in atlas.region_views()]
    atlas.replace({'n1': decode(page_bytes(4, 5))})
    assert [v.key for v in atlas.region_views()] == keys
    assert atlas.build().extract('n1').tobytes() == decode(page_bytes(4, 5)).tobytes()


def test_replace_follows_a_rename_of_the_add():
    atlas = atlas_with_adds('n1')
    atlas.rename_region('n1', 'renamed')
    atlas.replace({'renamed': decode(page_bytes(4, 5))})
    assert atlas.build().extract('renamed').size == (4, 5)


@pytest.mark.parametrize('packer', ['shelf', 'rectangles', 'polygons', 'rectangles_in_place', 'polygons_in_place'])
def test_composite_over_a_pristine_region_and_a_pending_add(packer, monkeypatch):
    atlas = atlas_with_adds('n1')
    mesh = {"type": "Mesh", "uvs": [0, 0, 1, 0, 1, 1, 0, 1],
            "triangles": [0, 1, 2, 0, 2, 3], "hull_length": 4}
    monkeypatch.setattr(api, '_parse_skeleton', lambda data: {"attachments": {"b": mesh}})
    atlas.set_skeleton(b'mesh')
    atlas.replace_composite(['a', 'n1'], decode(page_bytes(3, 2)))
    built = atlas.build(packer=packer)
    assert built.extract('n1').tobytes() == decode(page_bytes(3, 2)).tobytes()
    assert built.extract('a').size == (3, 2)


def test_composite_of_only_pending_adds():
    atlas = atlas_with_adds('n1', 'n2')
    atlas.replace_composite(['n1', 'n2'], decode(page_bytes(4, 5)))
    built = atlas.build()
    for name in ['n1', 'n2']:
        assert built.extract(name).tobytes() == decode(page_bytes(4, 5)).tobytes()


def test_alpha_converts_to_the_adds_page():
    atlas = atlas_with_adds('n1', pma=True)
    atlas.replace({'n1': decode(page_bytes(1, 1))}, alpha_mode='straight')
    assert atlas.build().extract('n1').getpixel((0, 0)) == (5, 10, 15, 128)


def test_extract_still_refuses_a_pending_add():
    atlas = atlas_with_adds('n1')
    for call in [lambda: atlas.extract('n1'), lambda: atlas.extract_composite(['n1']), lambda: atlas.mesh_hull_layout(['n1'])]:
        with pytest.raises(AtlasError, match='added since'):
            call()


def test_removing_a_replaced_add_undoes_it():
    atlas = atlas_with_adds('n1')
    atlas.replace({'n1': decode(page_bytes(4, 5))})
    atlas.remove_region(['n1'])
    assert [v.label for v in atlas.build().region_views()] == ['a', 'b']


def test_validation_is_atomic():
    atlas = atlas_with_adds('n1')
    before = list(atlas._session._mod_batches)
    with pytest.raises(TypeError):
        atlas.replace({'n1': decode(page_bytes(4, 5)), 'a': b'invalid'})
    assert atlas._session._mod_batches == before


def test_mixed_alpha_group_includes_pending_page():
    text, pages = two_pages(True, False)
    atlas = SpineAtlas.load(text, {name: decode(data) for name, data in pages.items()})
    atlas.add_region('n1', decode(page_bytes()), page='q.png')
    before = list(atlas._session._mod_batches)
    with pytest.raises(MixedAlphaGroup):
        atlas.replace_composite(['a', 'n1'], decode(page_bytes()), alpha_mode='straight')
    assert atlas._session._mod_batches == before


def test_session_replacement_releases_unpinned_source_and_missing_key():
    atlas = atlas_with_adds('n1')
    session = atlas._session
    old = session._mod_batches[0]['source']
    before = list(session._mod_batches)
    with pytest.raises(KeyError):
        session.replace_added_source('missing', decode(page_bytes()))
    assert session._mod_batches == before
    session.replace_added_source('n1', decode(page_bytes(4, 5)))
    with pytest.raises(KeyError):
        session._raster.export(old)


@pytest.mark.parametrize('composite', [False, True])
def test_pending_source_pin_survives_rollback(composite):
    atlas = atlas_with_adds('n1')
    before = list(atlas._session._mod_batches)
    with pytest.raises(RuntimeError, match='rollback'):
        with atlas.transaction():
            if composite:
                atlas.replace_composite(['a', 'n1'], decode(page_bytes(4, 5)))
            else:
                atlas.replace({'n1': decode(page_bytes(4, 5))})
            raise RuntimeError('rollback')
    assert atlas._session._mod_batches == before
    assert atlas.build().extract('n1').tobytes() == decode(page_bytes(2, 3)).tobytes()


@pytest.mark.parametrize('pending_first', [False, True])
def test_replace_validates_both_kinds_before_recording(pending_first):
    atlas = atlas_with_adds('n1')
    entries = [('n1', decode(page_bytes(4, 5))), ('a', b'invalid')]
    if not pending_first:
        entries.reverse()
    before = list(atlas._session._mod_batches)
    with pytest.raises(TypeError):
        atlas.replace(dict(entries))
    assert atlas._session._mod_batches == before


def test_composite_converts_each_pending_page_and_warns_for_mixed_default():
    text, pages = two_pages(True, False)
    atlas = SpineAtlas.load(text, {name: decode(data) for name, data in pages.items()})
    atlas.add_region('pma', decode(page_bytes(1, 1)), page='p.png')
    atlas.add_region('straight', decode(page_bytes(1, 1)), page='q.png')
    atlas.replace_composite(['pma'], decode(page_bytes(1, 1)), alpha_mode='straight')
    assert atlas.build().extract('pma').getpixel((0, 0)) == (5, 10, 15, 128)
    with pytest.warns(UserWarning, match='spans pma'):
        atlas.replace_composite(['pma', 'straight'], decode(page_bytes(2, 3)))
    built = atlas.build()
    for name in ['pma', 'straight']:
        assert built.extract(name).tobytes() == decode(page_bytes(2, 3)).tobytes()


@pytest.mark.parametrize('composite', [False, True])
def test_pristine_failure_does_not_record_pending_half(composite, monkeypatch):
    atlas = atlas_with_adds('n1')
    before = list(atlas._session._mod_batches)
    def fail(*args, **kwargs):
        raise MemoryError('pristine preparation failed')
    monkeypatch.setattr(atlas._session, 'apply_mod_image', fail)
    with pytest.raises(MemoryError, match='pristine preparation'):
        if composite:
            atlas.replace_composite(['n1', 'a'], decode(page_bytes(4, 5)))
        else:
            atlas.replace({'n1': decode(page_bytes(4, 5)), 'a': decode(page_bytes(4, 5))})
    assert atlas._session._mod_batches == before
    assert atlas.build().extract('n1').tobytes() == decode(page_bytes(2, 3)).tobytes()


def test_immediate_session_replacement_repacks():
    from atlas_toolkit.engine.session import AtlasSession
    from atlas_toolkit.raster.pillow_backend import PillowRasterBackend
    text, pages = one_page()
    session = AtlasSession.load_images(text, {name: decode(data) for name, data in pages.items()}, PillowRasterBackend())
    session.add_region(decode(page_bytes(2, 3)), 'fresh')
    replacement = decode(page_bytes(4, 5))
    session.replace_added_source('fresh', replacement)
    text, pages = session.save()
    built = SpineAtlas.load(text, {name: decode(data) for name, data in pages.items()})
    assert built.extract('fresh').size == replacement.size
    assert built.extract('fresh').tobytes() == replacement.tobytes()
