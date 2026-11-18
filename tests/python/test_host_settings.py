# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Requested preferences, effective engine options, and atomic rebuilds."""
import pytest

from atlas_toolkit import api
from atlas_toolkit.api import PackOptions, SpineAtlas
from atlas_toolkit.host import AppHost
from .host_fixtures import all_pma, decode, mixed_pma, one_page, page_bytes
from .test_api_app_read import MESH


def loaded(factory=one_page):
    host = AppHost()
    text, pages = factory()
    host.load('sample.atlas', text, pages, None, None)
    return host


@pytest.fixture
def mesh(monkeypatch):
    monkeypatch.setattr(api, '_parse_skeleton', lambda data: {'attachments': {'a': MESH, 'b': MESH}})


@pytest.fixture
def calls(monkeypatch):
    recorded = []
    original = SpineAtlas.pack
    def record(self, **kwargs):
        recorded.append(kwargs)
        return original(self, **kwargs)
    monkeypatch.setattr(SpineAtlas, 'pack', record)
    return recorded


def test_default_settings():
    state = loaded()._state()['settings']
    assert state['packer'] == 'auto'
    assert state['padding'] == 2
    assert state['shareCompositeImage'] is False
    assert state['keepLayout'] is True
    assert state['alpha'] == 'straight'
    assert state['bleed'] is True
    assert state['requested']['meshAware'] is True
    assert state['meshAware'] is False   # effective: no .skel, so not usable
    for gone in ('splitComposite', 'splitCompositeLocked', 'keepLayoutLocked'):
        assert gone not in state


@pytest.mark.parametrize('packer', ['polygons', 'auto'])
def test_polygon_packers_force_mesh_aware_and_pass_none(packer, mesh, calls):
    # auto resolves to polygons with usable mesh, so both lock Mesh-Aware.
    host = loaded()
    host.set_skeleton('sample.skel', b'mesh')
    state = host.set_settings({'packer': packer, 'meshAware': False})
    assert state['settings']['meshAware'] is True
    assert state['settings']['meshAwareLocked'] is True
    host.enter_edit()
    assert calls[-1]['options'].mesh_aware is None
    assert calls[-1]['options'].packer == packer


@pytest.mark.parametrize('packer', ['shelf', 'rectangles', 'polygons', 'auto'])
def test_no_packer_locks_composite_or_keep_layout(packer, mesh, calls):
    host = loaded()
    host.set_skeleton('sample.skel', b'mesh')
    settings = host.set_settings({'packer': packer})['settings']
    for key in ('shareCompositeImageLocked', 'keepLayoutLocked'):
        assert key not in settings
    settings = host.set_settings({'packer': 'shelf', 'shareCompositeImage': True, 'keepLayout': False})['settings']
    assert settings['shareCompositeImage'] is True and settings['keepLayout'] is False
    host.enter_edit()
    assert calls[-1]['options'].share_composite_image is True
    assert calls[-1]['options'].try_keep_layout is False


def test_mesh_aware_is_off_and_locked_without_mesh(calls):
    host = loaded()
    host.set_settings({'packer': 'shelf'})
    settings = host._state()['settings']
    assert settings['meshAware'] is False and settings['meshAwareLocked'] is True
    host.enter_edit()
    assert calls[-1]['options'].mesh_aware is False


def test_fallback_is_undone_by_late_skeleton(mesh, calls):
    host = loaded()
    state = host.set_settings({'packer': 'polygons'})
    assert state['settings']['packer'] == 'rectangles'
    assert state['settings']['fallbackFrom'] == 'polygons'
    host.enter_edit()
    assert calls[-1]['options'].packer == 'rectangles'
    assert calls[-1]['options'].mesh_aware is False
    result = host.set_skeleton('sample.skel', b'mesh')
    assert result['state']['settings']['packer'] == 'polygons'
    assert result['state']['settings']['fallbackFrom'] is None
    assert calls[-1]['options'].packer == 'polygons'


def test_auto_needs_no_mesh_and_never_falls_back(calls):
    host = loaded()   # no .skel
    state = host.set_settings({'packer': 'auto'})
    assert state['settings']['packer'] == 'auto' and state['settings']['fallbackFrom'] is None
    host.enter_edit()
    assert calls[-1]['options'].packer == 'auto' and calls[-1]['options'].mesh_aware is None


def test_auto_ignores_stored_mesh_aware_false(mesh, calls):
    host = loaded()
    host.set_skeleton('sample.skel', b'mesh')
    host.set_settings({'packer': 'auto', 'meshAware': False})
    host.enter_edit()
    assert calls[-1]['options'].packer == 'auto'
    assert calls[-1]['options'].mesh_aware is None


def test_keep_layout_and_share_image_reach_engine(calls):
    host = loaded()
    host.set_settings({'keepLayout': False, 'padding': 19, 'shareCompositeImage': True})
    host.enter_edit()
    assert calls[-1]['options'].try_keep_layout is False
    assert calls[-1]['options'].padding == 19
    assert calls[-1]['options'].share_composite_image is True


@pytest.mark.parametrize('settings', [
    {'padding': v} for v in [0, 257, True, 2.5, '2', None]
] + [{'packer': v} for v in ['grid', 'bad', [], None]] + [
    {'meshAware': 1}, {'shareCompositeImage': None}, {'keepLayout': 'yes'},
    {'packer': 'rectangles_in_place'}, {'packer': 'polygons_in_place'}, {'splitComposite': False},
    {'alpha': 'bad'}, {'unknown': True}, {'padding': 3, 'alpha': False},
])
@pytest.mark.parametrize('edit', [False, True])
def test_invalid_settings_change_nothing(settings, edit, calls):
    host = loaded()
    if edit:
        host.enter_edit()
        host.mark_saved()
    before, packed, count = host._state(), host._packed, len(calls)
    with pytest.raises(ValueError):
        host.set_settings(settings)
    assert host._state() == before and host._packed is packed
    assert len(calls) == count


@pytest.mark.parametrize('padding', [1, 256])
def test_padding_boundaries(padding):
    assert loaded().set_settings({'padding': padding})['settings']['padding'] == padding


def test_preferences_survive_load():
    host = loaded()
    requested = {'packer': 'polygons', 'padding': 7, 'meshAware': False,
                 'shareCompositeImage': True, 'keepLayout': False, 'alpha': 'premultiplied',
                 'bleed': False}
    host.set_settings(requested)
    text, pages = one_page()
    state = host.load('second.atlas', text, pages, None, None)
    assert state['settings']['fallbackFrom'] == 'polygons'
    assert host._pack_settings == requested


@pytest.mark.parametrize('factory,visible', [(one_page, False), (all_pma, True), (mixed_pma, False)])
def test_alpha_visibility(factory, visible):
    assert loaded(factory)._state()['settings']['alphaVisible'] is visible


def test_alpha_applies_to_later_edits_only():
    host = loaded(all_pma)
    host.enter_edit()
    host.modify(['a'], page_bytes(3, 2), 'straight')
    before = host.page_preview(0)
    host.mark_saved()
    result = host.set_settings({'alpha': 'premultiplied'})
    assert host.page_preview(0) == before
    assert host.has_pending() is False
    assert result['state']['settings']['alpha'] == 'premultiplied'
    host.modify(['a'], page_bytes(3, 2), result['state']['settings']['alpha'])
    assert host.page_preview(0) != before


def test_failed_setting_restores_everything(monkeypatch):
    host = loaded()
    host.enter_edit()
    host.rename('a', 'renamed')
    host.mark_saved()
    before, packed, preview = host._state(), host._packed, host.page_preview(0)
    def fail(self, **kwargs):
        self.rename_region('renamed', 'lost')
        raise MemoryError('synthetic overflow')
    monkeypatch.setattr(SpineAtlas, 'pack', fail)
    with pytest.raises(MemoryError):
        host.set_settings({'padding': 256})
    assert host._state() == before and host._packed is packed
    assert host.page_preview(0) == preview and host.has_pending() is False


def test_late_skeleton_edit_rebuilds_and_failure_restores(mesh, monkeypatch, calls):
    host = loaded()
    host.set_settings({'packer': 'shelf'})
    host.enter_edit()
    before = host.modify(['a'], page_bytes(12, 8), 'straight')
    after = host.set_skeleton('first.skel', b'first')
    assert after['image'] != before['image']
    assert calls[-1]['options'].mesh_aware is True
    host.mark_saved()
    state, packed, preview = host._state(), host._packed, host.page_preview(0)
    def fail(self, **kwargs):
        raise MemoryError('synthetic rebuild failure')
    monkeypatch.setattr(SpineAtlas, 'pack', fail)
    with pytest.raises(MemoryError):
        host.set_skeleton('bad.skel', b'bad')
    assert host._state() == state and host._packed is packed
    assert host._skeleton == b'first' and host._atlas._skeleton == b'first'
    assert host.page_preview(0) == preview and host.has_pending() is False


def test_requested_mesh_flag_is_used_when_mesh_is_usable(mesh, calls):
    host = loaded()
    host.set_settings({'packer': 'shelf'})
    host.set_skeleton('sample.skel', b'mesh')
    state = host.set_settings({'meshAware': False})
    assert state['settings']['meshAwareLocked'] is False
    host.enter_edit()
    assert calls[-1]['options'].mesh_aware is False
    result = host.set_settings({'meshAware': True})
    assert result['state']['settings']['meshAware'] is True
    assert calls[-1]['options'].mesh_aware is True


def test_view_settings_do_not_pack(calls):
    host = loaded()
    state = host.set_settings({'padding': 3})
    assert state['settings']['padding'] == 3 and calls == []


@pytest.mark.parametrize('initial', [None, b'initial'])
def test_failed_skeleton_restores_loaded_skeleton(initial, mesh, monkeypatch):
    host = AppHost()
    text, pages = one_page()
    host.load('sample.atlas', text, pages, 'initial.skel' if initial else None, initial)
    host.enter_edit()
    host.mark_saved()
    before, packed = host._state(), host._packed
    def fail(self, **kwargs):
        raise MemoryError('synthetic rebuild failure')
    monkeypatch.setattr(SpineAtlas, 'pack', fail)
    with pytest.raises(MemoryError):
        host.set_skeleton('new.skel', b'new')
    assert host._state() == before and host._packed is packed
    assert host._atlas._skeleton == initial and host._skeleton == initial
    assert host.has_pending() is False


def test_settings_repack_updates_output_and_marks_unsaved():
    host = loaded()
    host.enter_edit()
    before = host.modify(['a'], page_bytes(4, 3), 'straight')
    host.save_output()
    host.mark_saved()
    assert host._state()['hasPending'] is False
    result = host.set_settings({'padding': 10})
    assert result['image'] != before['image']
    assert host.page_preview(0)['image'] == result['image']
    assert result['state']['hasPending'] is True
    assert host.has_pending() is True


def test_alpha_only_settings_cache_hit_keeps_saved_output():
    host = loaded(all_pma)
    host.enter_edit()
    before = host.modify(['a'], page_bytes(4, 3), 'straight')
    host.save_output()
    host.mark_saved()
    packed = host._packed
    result = host.set_settings({'alpha': 'premultiplied'})
    assert host._packed[0] is packed[0]
    assert host._packed[1] is packed[1]
    assert result['unchanged'] is True and 'image' not in result
    assert host.page_preview(0)['image'] == before['image']
    assert result['state']['hasPending'] is False
    assert result['state']['hasChanges'] is True
    assert host.has_pending() is False


def test_preferences_can_be_restored_before_load():
    host = AppHost()
    requested = {'packer': 'rectangles', 'padding': 9, 'meshAware': False,
                 'shareCompositeImage': True, 'keepLayout': False, 'alpha': 'premultiplied',
                 'bleed': False}
    assert host.set_settings(requested) is None
    assert host._pack_settings == requested
    for invalid in ({'padding': 0}, {'padding': 4, 'unknown': True}, {'packer': 'grid'}):
        with pytest.raises(ValueError):
            host.set_settings(invalid)
        assert host._pack_settings == requested
    text, pages = one_page()
    state = host.load('sample.atlas', text, pages, None, None)
    assert {key: state['settings'][key] for key in requested} == requested


def test_real_overflow_setting_restores_everything():
    side = 42
    text = f'big.png\nsize: {side},{side}\npma: false\n' + ''.join(
        f'r{i}\n bounds: {i % side},{i // side},1,1\n' for i in range(1700)
    )
    host = AppHost()
    host.load('big.atlas', text, {'big.png': page_bytes(side, side)}, None, None)
    # Keep this overflow fixture on its original Shelf packer.
    host.set_settings({'packer': 'shelf'})
    # A pad of 256 only reaches the packers when the page is repacked, which
    # keeping the layout (the default) avoids for a replacement that fits.
    host.set_settings({'keepLayout': False})
    host.enter_edit()
    host.modify(['r0'], page_bytes(1, 1), 'straight')
    host.save_output()
    host.mark_saved()
    before, packed, preview = host._state(), host._packed, host.page_preview(0)
    cache = host._atlas._build_cache
    with pytest.raises(api.CanvasTooLarge):
        host.set_settings({'padding': 256})
    assert host._state() == before and host._packed is packed
    assert host.page_preview(0) == preview and host.has_pending() is False
    assert host._atlas._build_cache is cache
    assert host.rename('r1', 'renamed')['state']['settings']['padding'] == 2


def test_late_skeleton_after_save_marks_changed_packing_pending(mesh):
    host = loaded()
    host.enter_edit()
    host.modify(['a'], page_bytes(12, 8), 'straight')
    host.save_output()
    host.mark_saved()
    previous = host._packed[0]
    result = host.set_skeleton('late.skel', b'mesh')
    assert host._packed[0] is not previous
    assert result['state']['hasPending'] is True


# --- settings changes that do not change the output -----------------------------

def test_settings_need_pack_only_when_the_engine_must_pack(calls):
    host = loaded()
    host.set_settings({'packer': 'shelf'})
    assert host.settings_need_pack({'packer': 'rectangles'}) is False  # View: nothing packs
    host.enter_edit()
    assert host.settings_need_pack({'packer': 'rectangles'}) is False  # no edits yet
    host.modify(['a'], page_bytes(12, 8), 'straight')
    assert host.settings_need_pack({'packer': 'rectangles'}) is True
    assert host.settings_need_pack({'alpha': 'premultiplied'}) is False  # same pack: cached
    host.set_settings({'packer': 'rectangles'})
    assert host.settings_need_pack({'packer': 'shelf'}) is False
    assert host.settings_need_pack({'padding': 'x'}) is True  # invalid: set_settings reports it
    assert host._settings()['packer'] == 'rectangles'  # asking never changes the settings


def test_unchanged_output_returns_no_image(calls):
    host = loaded()
    host.enter_edit()
    result = host.set_settings({'packer': 'rectangles', 'padding': 5})  # no edits: same pages
    assert result == {'state': host._state(), 'unchanged': True}
    host.modify(['a'], page_bytes(12, 8), 'straight')
    changed = host.set_settings({'packer': 'shelf'})
    assert 'image' in changed and 'unchanged' not in changed
    assert host.set_settings({'alpha': 'premultiplied'})['unchanged'] is True


def test_kept_layout_padding_that_did_not_matter_needs_no_pack():
    host = loaded()
    host.set_settings({'packer': 'rectangles', 'keepLayout': True})
    host.enter_edit()
    host.modify(['a'], page_bytes(3, 2), 'straight')  # fits its slot
    assert host.settings_need_pack({'padding': 40}) is False
    assert host.set_settings({'padding': 40})['unchanged'] is True


# --- bleed ------------------------------------------------------------------------

def test_bleed_defaults_on_and_reaches_the_packer(calls):
    host = loaded()
    assert host._state()['settings']['bleed'] is True
    assert host._state()['settings']['bleedLocked'] is False
    host.enter_edit()
    host.modify(['a'], page_bytes(12, 8), 'straight')
    assert calls[-1]['options'].bleed is True
    host.set_settings({'bleed': False})
    assert calls[-1]['options'].bleed is False
    with pytest.raises(ValueError, match='bleed'):
        host.set_settings({'bleed': 'yes'})


def test_bleed_is_locked_when_every_page_is_pma():
    assert loaded(all_pma)._state()['settings']['bleedLocked'] is True
    assert loaded(mixed_pma)._state()['settings']['bleedLocked'] is False


def test_preview_settings_matches_set_settings_without_side_effects(mesh, calls):
    host = loaded()
    host.set_skeleton('sample.skel', b'mesh')
    host.enter_edit()
    calls.clear()
    before = host._state()['settings']
    assert before['requested'] == {'packer': 'auto', 'padding': 2, 'meshAware': True,
                                   'shareCompositeImage': False, 'keepLayout': True,
                                   'alpha': 'straight', 'bleed': True}
    draft = {'packer': 'polygons', 'shareCompositeImage': True, 'padding': 5}
    preview = host.preview_settings(draft)
    # Nothing stored or packed.
    assert host._state()['settings'] == before and calls == []
    # Locks follow the draft: polygons forces Mesh-Aware on.
    assert preview['meshAware'] is True and preview['meshAwareLocked'] is True
    assert preview['requested']['shareCompositeImage'] is True and preview['padding'] == 5
    applied = host.set_settings(draft)
    state = applied['state']['settings'] if 'state' in applied else applied['settings']
    assert state == preview
    with pytest.raises(ValueError):
        host.preview_settings({'padding': 0})


@pytest.mark.parametrize('preview', [False, True])
def test_auto_locks_mesh_aware_with_skel(preview, mesh):
    host = loaded()
    host.set_skeleton('sample.skel', b'mesh')
    before = host._settings()
    change = {'packer': 'auto', 'meshAware': False}
    settings = host.preview_settings(change) if preview else host.set_settings(change)['settings']
    assert settings['packer'] == 'auto'
    assert settings['meshAware'] is True and settings['meshAwareLocked'] is True
    assert settings['fallbackFrom'] is None
    if preview:
        assert host._settings() == before


@pytest.mark.parametrize('preview', [False, True])
def test_auto_without_skel_unlocks(preview):
    host = loaded()
    change = {'packer': 'auto'}
    settings = host.preview_settings(change) if preview else host.set_settings(change)['settings']
    assert settings['packer'] == 'auto' and settings['fallbackFrom'] is None
    # Without usable mesh the mesh toggle remains off and locked; layout and sharing are unlocked.
    assert settings['meshAware'] is False and settings['meshAwareLocked'] is True
    assert 'keepLayoutLocked' not in settings and 'shareCompositeImageLocked' not in settings


@pytest.mark.parametrize('change,key', [
    ({'packer': 'rectangles_in_place'}, 'packer'),
    ({'packer': 'polygons_in_place'}, 'packer'),
    ({'splitComposite': False}, 'splitComposite'),
])
def test_old_keys_rejected(change, key):
    host = loaded()
    for method in (host.set_settings, host.preview_settings):
        with pytest.raises(ValueError, match=f'^invalid setting: {key}$'):
            method(change)


@pytest.mark.parametrize('keep', [False, True])
def test_keep_layout_reaches_pack(keep, calls):
    host = loaded()
    host.set_settings({'keepLayout': keep})
    host.enter_edit()
    host.modify(['a'], page_bytes(3, 2), 'straight')
    assert calls[-1]['options'].try_keep_layout is keep


def test_share_composite_image_reaches_pack(calls):
    host = loaded()
    host.set_settings({'shareCompositeImage': True})
    host.enter_edit()
    host.modify(['a', 'b'], page_bytes(8, 6), 'straight')
    assert calls[-1]['options'].share_composite_image is True
    text, pages = one_page()
    atlas = SpineAtlas.load(text, {name: decode(data) for name, data in pages.items()})
    atlas.replace_composite(['a', 'b'], decode(page_bytes(8, 6)), alpha_mode='straight')
    expected_text, expected_pages = atlas.pack(options=PackOptions(share_composite_image=True, bleed=True))
    actual_text, actual_pages = host._packed
    assert actual_text == expected_text
    assert actual_pages.keys() == expected_pages.keys()
    for name in actual_pages:
        assert actual_pages[name].size == expected_pages[name].size
        assert actual_pages[name].tobytes() == expected_pages[name].tobytes()
