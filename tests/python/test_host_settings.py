"""Requested preferences, effective engine options, and atomic rebuilds."""
import pytest

from atlas_toolkit import api
from atlas_toolkit.api import SpineAtlas
from atlas_toolkit.host import AppHost
from .host_fixtures import all_pma, mixed_pma, one_page, page_bytes
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


@pytest.mark.parametrize('packer', ['polygons', 'polygons_in_place'])
def test_polygon_packers_force_mesh_aware_and_pass_none(packer, mesh, calls):
    host = loaded()
    host.set_skeleton('sample.skel', b'mesh')
    state = host.set_settings({'packer': packer, 'meshAware': False})
    assert state['settings']['meshAware'] is True
    assert state['settings']['meshAwareLocked'] is True
    host.enter_edit()
    assert calls[-1]['packer_options'].mesh_aware is None
    assert calls[-1]['packer'] == packer


@pytest.mark.parametrize('packer', ['rectangles_in_place', 'polygons_in_place'])
def test_in_place_packers_lock_split_and_keep_layout(packer, mesh, calls):
    host = loaded()
    host.set_skeleton('sample.skel', b'mesh')
    settings = host.set_settings({'packer': packer, 'splitComposite': False, 'keepLayout': False})['settings']
    for key in ['splitComposite', 'keepLayout']:
        assert settings[key] is True and settings[key + 'Locked'] is True
    host.enter_edit()
    assert calls[-1]['split_composite'] is True
    assert calls[-1]['packer_options'].repack is None
    settings = host.set_settings({'packer': 'shelf'})['state']['settings']
    assert settings['splitComposite'] is False and settings['keepLayout'] is False
    assert calls[-1]['split_composite'] is False
    assert calls[-1]['packer_options'].repack == 'always'


def test_mesh_aware_is_off_and_locked_without_mesh(calls):
    host = loaded()
    settings = host._state()['settings']
    assert settings['meshAware'] is False and settings['meshAwareLocked'] is True
    host.enter_edit()
    assert calls[-1]['packer_options'].mesh_aware is False


@pytest.mark.parametrize('packer', ['polygons', 'polygons_in_place'])
def test_fallback_is_undone_by_late_skeleton(packer, mesh, calls):
    host = loaded()
    state = host.set_settings({'packer': packer})
    assert state['settings']['packer'] == packer.replace('polygons', 'rectangles')
    assert state['settings']['fallbackFrom'] == packer
    host.enter_edit()
    assert calls[-1]['packer'] == packer.replace('polygons', 'rectangles')
    assert calls[-1]['packer_options'].mesh_aware is False
    result = host.set_skeleton('sample.skel', b'mesh')
    assert result['state']['settings']['packer'] == packer
    assert result['state']['settings']['fallbackFrom'] is None
    assert calls[-1]['packer'] == packer


def test_keep_layout_off_and_padding_reach_engine(calls):
    host = loaded()
    host.set_settings({'keepLayout': False, 'padding': 19, 'splitComposite': True})
    host.enter_edit()
    assert calls[-1]['packer_options'].repack == 'always'
    assert calls[-1]['packer_options'].padding == 19
    assert calls[-1]['split_composite'] is True


@pytest.mark.parametrize('settings', [
    {'padding': v} for v in [0, 257, True, 2.5, '2', None]
] + [{'packer': v} for v in ['grid', 'bad', [], None]] + [
    {'meshAware': 1}, {'splitComposite': None}, {'keepLayout': 'yes'},
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
                 'splitComposite': True, 'keepLayout': False, 'alpha': 'premultiplied'}
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
    assert host._saved is True
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
    assert host.page_preview(0) == preview and host._saved is True


def test_late_skeleton_edit_rebuilds_and_failure_restores(mesh, monkeypatch, calls):
    host = loaded()
    host.enter_edit()
    before = host.modify(['a'], page_bytes(12, 8), 'straight')
    after = host.set_skeleton('first.skel', b'first')
    assert after['image'] != before['image']
    assert calls[-1]['packer_options'].mesh_aware is True
    host.mark_saved()
    state, packed, preview = host._state(), host._packed, host.page_preview(0)
    def fail(self, **kwargs):
        raise MemoryError('synthetic rebuild failure')
    monkeypatch.setattr(SpineAtlas, 'pack', fail)
    with pytest.raises(MemoryError):
        host.set_skeleton('bad.skel', b'bad')
    assert host._state() == state and host._packed is packed
    assert host._skeleton == b'first' and host._atlas._skeleton == b'first'
    assert host.page_preview(0) == preview and host._saved is True


def test_requested_mesh_flag_is_used_when_mesh_is_usable(mesh, calls):
    host = loaded()
    host.set_skeleton('sample.skel', b'mesh')
    state = host.set_settings({'meshAware': False})
    assert state['settings']['meshAwareLocked'] is False
    host.enter_edit()
    assert calls[-1]['packer_options'].mesh_aware is False
    result = host.set_settings({'meshAware': True})
    assert result['state']['settings']['meshAware'] is True
    assert calls[-1]['packer_options'].mesh_aware is True


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
    assert host._saved is True


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
    assert host._saved is False


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
    assert result['image'] == before['image']
    assert host.page_preview(0)['image'] == result['image']
    assert result['state']['hasPending'] is False
    assert result['state']['hasChanges'] is True
    assert host._saved is True


def test_preferences_can_be_restored_before_load():
    host = AppHost()
    requested = {'packer': 'rectangles', 'padding': 9, 'meshAware': False,
                 'splitComposite': True, 'keepLayout': False, 'alpha': 'premultiplied'}
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
    host.enter_edit()
    host.modify(['r0'], page_bytes(1, 1), 'straight')
    host.save_output()
    host.mark_saved()
    before, packed, preview = host._state(), host._packed, host.page_preview(0)
    cache = host._atlas._build_cache
    with pytest.raises(api.CanvasTooLarge):
        host.set_settings({'padding': 256})
    assert host._state() == before and host._packed is packed
    assert host.page_preview(0) == preview and host._saved is True
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
