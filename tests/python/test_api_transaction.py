"""Edit rollback, raster lifetime and pack access contracts."""
import pytest
from PIL import Image

import atlas_toolkit.api as api
from atlas_toolkit.api import CanvasTooLarge, InvalidRegionName, SpineAtlas
from atlas_toolkit.packer import Packer


def pattern(seed=1):
    return Image.frombytes('RGBA', (4, 4), bytes(
        v for y in range(4) for x in range(4)
        for v in (x * 40, y * 40, seed, 255)))


PATTERN = pattern()
TEXT = 'p1.png\nsize: 8,4\na\n bounds: 0,0,4,4\nb\n bounds: 4,0,4,4\n'


def two_region_atlas():
    return SpineAtlas.load(TEXT, Image.frombytes('RGBA', (8, 4), PATTERN.tobytes() * 2))


def packed_bytes(result):
    text, pages = result
    return text, {name: (image.size, image.tobytes()) for name, image in pages.items()}


def live(atlas):
    return len(atlas._session._raster._backend._images)


def test_a_failed_pack_rolls_back_the_edit():
    atlas = two_region_atlas()
    atlas.replace({'a': PATTERN})
    first = atlas.pack()
    with pytest.raises(CanvasTooLarge):
        with atlas.transaction():
            atlas.replace({'b': Image.new('RGBA', (11000, 4))})
            atlas.pack()
    assert packed_bytes(atlas.pack()) == packed_bytes(first)
    assert atlas.region_views(first[0])


def test_rollback_restores_a_sprite_the_edit_replaced():
    atlas = two_region_atlas()
    atlas.replace({'a': PATTERN})
    before = live(atlas)
    with pytest.raises(RuntimeError, match='rollback'):
        with atlas.transaction():
            atlas.replace({'a': pattern(2)})
            raise RuntimeError('rollback')
    assert live(atlas) == before
    assert atlas.build().extract('a').tobytes() == PATTERN.tobytes()


def test_rollback_of_a_remove_that_undid_an_add_restores_the_add():
    atlas = two_region_atlas()
    atlas.add_region('new', PATTERN)
    before = live(atlas)
    with pytest.raises(RuntimeError, match='rollback'):
        with atlas.transaction():
            atlas.remove_region('new')
            raise RuntimeError('rollback')
    assert live(atlas) == before
    assert atlas.build().extract('new').tobytes() == PATTERN.tobytes()


def test_a_failed_edit_inside_keeps_generation_and_saved_state():
    atlas = two_region_atlas()
    atlas.replace({'a': PATTERN})
    atlas.pack()
    session = atlas._session
    session._modifications_saved = True
    before = session.snapshot(), live(atlas), atlas._build_cache
    with pytest.raises(api.RegionNotFound):
        with atlas.transaction():
            atlas.replace({'missing': PATTERN})
    assert (session.snapshot(), live(atlas), atlas._build_cache) == before
    assert session._modifications_saved is True


def test_commit_releases_pinned_handles():
    atlas = two_region_atlas()
    atlas.add_region('new', PATTERN)
    before = live(atlas)
    with atlas.transaction():
        atlas.remove_region('new')
        assert live(atlas) == before
    assert live(atlas) == before - 1


def test_reset_clears_edits_and_the_next_edit_sees_no_old_sprite():
    atlas = two_region_atlas()
    pristine = packed_bytes(atlas.pack())
    roots = live(atlas)
    atlas.replace({'a': pattern(2)})
    atlas.rename_region('b', 'renamed')
    atlas.add_region('new', PATTERN)
    generation = atlas._session.snapshot().generation
    atlas.reset()
    assert not atlas.has_changes
    assert live(atlas) == roots
    assert atlas._session.snapshot().generation == generation
    assert packed_bytes(atlas.pack()) == pristine
    atlas.replace({'b': PATTERN})
    assert atlas._session.snapshot().generation > generation
    assert atlas.build().extract('a').tobytes() == two_region_atlas().extract('a').tobytes()


@pytest.mark.parametrize('packer', [p for p in Packer if p != Packer.GRID])
def test_pack_returns_same_result_as_build(packer, monkeypatch):
    mesh = {'type': 'Mesh', 'uvs': [0, 0, 1, 0, 1, 1, 0, 1],
            'triangles': [0, 1, 2, 0, 2, 3], 'hull_length': 4}
    monkeypatch.setattr(api, '_parse_skeleton', lambda data: {'attachments': {'a': mesh, 'b': mesh}})
    atlas = SpineAtlas.load(TEXT, two_region_atlas().pages, skeleton=b'x')
    atlas.replace({'a': PATTERN})
    packed = atlas.pack(packer=packer)
    cache = atlas._build_cache
    built = atlas.build(packer=packer)
    assert atlas._build_cache is cache
    assert packed_bytes(packed) == packed_bytes((built.atlas_text, built.pages))
    atlas._build_cache = None
    built = atlas.build(packer=packer)
    cache = atlas._build_cache
    assert packed_bytes(atlas.pack(packer=packer)) == packed_bytes((built.atlas_text, built.pages))
    assert atlas._build_cache is cache


def test_pack_does_not_create_a_child_atlas(monkeypatch):
    atlas = two_region_atlas()
    def forbidden(*args, **kwargs):
        raise AssertionError('child created')
    monkeypatch.setattr(SpineAtlas, '_create', forbidden)
    assert atlas.pack()[0] == TEXT
    atlas.replace({'a': PATTERN})
    assert atlas.pack()[1]


def test_pack_shares_untouched_pages_and_never_alters_them():
    text = TEXT + '\np2.png\nsize: 4,4\nc\n bounds: 0,0,4,4\n'
    atlas = SpineAtlas.load(text, {'p1.png': two_region_atlas().pages['p1.png'], 'p2.png': pattern(3)})
    pristine = atlas.pack()[1]['p2.png']
    before = pristine.tobytes()
    atlas.replace({'a': PATTERN})
    assert atlas.pack()[1]['p2.png'] is pristine
    atlas.replace({'b': pattern(2)})
    assert atlas.pack()[1]['p2.png'] is pristine
    assert pristine.tobytes() == before
    assert atlas.build().pages['p2.png'] is not pristine
    atlas._build_cache = None
    atlas.build()
    assert atlas.pack()[1]['p2.png'] is pristine
    assert pristine.tobytes() == before


def test_page_image_copies_only_requested_page(monkeypatch):
    atlas = two_region_atlas()
    monkeypatch.setattr(atlas._session, 'export_pages', lambda: pytest.fail('all pages copied'))
    image = atlas.page_image('p1.png')
    assert image is not atlas.pack()[1]['p1.png']
    assert image.tobytes() == atlas.pack()[1]['p1.png'].tobytes()
    with pytest.raises(ValueError):
        atlas.page_image('missing')


def test_validate_region_name_and_nested_transaction():
    atlas = two_region_atlas()
    assert atlas.validate_region_name(' clean ') == 'clean'
    assert atlas.validate_region_name(' a ', exclude='a') == 'a'
    for name in ('a', '', 'bad\nname'):
        with pytest.raises(InvalidRegionName) as error:
            atlas.validate_region_name(name)
        assert error.value.reason
    with atlas.transaction():
        with pytest.raises(RuntimeError):
            with atlas.transaction():
                pass
    with atlas.transaction():
        atlas.rename_region('a', 'renamed')
    assert atlas.region('renamed')


def test_checkpoint_release_and_restore_are_idempotent():
    atlas = two_region_atlas()
    session = atlas._session
    token = session.edit_checkpoint()
    atlas.add_region('new', PATTERN)
    session.edit_restore(token)
    before = session.snapshot(), live(atlas)
    session.edit_restore(token)
    session.edit_release(token)
    assert (session.snapshot(), live(atlas)) == before
    token = session.edit_checkpoint()
    session.edit_release(token)
    session.edit_release(token)
    assert (session.snapshot(), live(atlas)) == before


def test_reset_inside_transaction_can_roll_back():
    atlas = two_region_atlas()
    atlas.add_region('new', PATTERN)
    atlas.replace({'a': pattern(2)})
    expected = packed_bytes(atlas.pack())
    count = live(atlas)
    with pytest.raises(RuntimeError, match='rollback'):
        with atlas.transaction():
            atlas.reset()
            raise RuntimeError('rollback')
    assert live(atlas) == count
    assert packed_bytes(atlas.pack()) == expected
    assert atlas.build().extract('new').tobytes() == PATTERN.tobytes()


@pytest.mark.parametrize('edited', [False, True])
def test_pack_validates_settings_like_build(edited):
    atlas = two_region_atlas()
    if edited:
        atlas.replace({'a': PATTERN})
    for packer in ('grid', 'polygons', 'unknown'):
        with pytest.raises(Exception) as expected:
            atlas.build(packer=packer)
        with pytest.raises(type(expected.value)) as actual:
            atlas.pack(packer=packer)
        assert str(actual.value) == str(expected.value)
