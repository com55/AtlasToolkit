"""Edit operations use stable keys and publish only successfully packed edits."""
import json

import pytest

from atlas_toolkit.api import AtlasError, InvalidRegionName, RegionNotFound, SpineAtlas
from atlas_toolkit.document import png_names_for_save
from atlas_toolkit.host import wire
from .host_fixtures import all_pma, decode, mixed_pma, one_page, page_bytes, two_pages
from .test_host_view import loaded


def test_enter_reset_exit():
    host, pristine = loaded(two_pages)
    result = host.enter_edit()
    assert result['pages'] == pristine['pages']
    assert result['activePage'] == 'p.png'
    assert result['image']['rgba'] == decode(two_pages()[1]['p.png']).tobytes()
    host.modify(['a'], page_bytes(4, 3), 'straight')
    host.modify(['c'], page_bytes(5, 2), 'straight')
    assert host.reset_edits() == result
    host.rename('a', 'renamed')
    assert host.exit_edit() == pristine
    assert host.preview(['a'], False, False, False, 'straight')
    assert host.enter_edit() == result


def test_rename_keys_and_saved_state():
    host, pristine = loaded()
    host.enter_edit()
    result = host.rename('a', 'renamed')
    assert 'a' in result['regions'] and 'renamed' not in result['regions']
    assert result['state']['regions'][0] == {'key': 'a', 'label': 'renamed'}
    assert result['state']['pristineRegions'] == pristine['regions']
    assert host.validate_name('renamed', 'a') == {'ok': True, 'value': 'renamed'}
    assert host.validate_name('b', 'a')['ok'] is False
    assert host.validate_name('  fresh  ', None) == {'ok': True, 'value': 'fresh'}
    result = host.modify(['a'], page_bytes(4, 3), 'straight')
    assert result['regions']['a'][2:4] == [4, 3]
    assert result['state']['modifiedKeys'] == ['a']
    assert result['state']['hasPending']
    output = host.save_output()
    assert not host._state()['hasPending']
    assert [p['name'] for p in output['pages']] == png_names_for_save(output['text'], len(output['pages']), 'sample.atlas')
    assert decode(output['pages'][0]['png']).tobytes() == host.page_preview(0)['image']['rgba']
    assert host.rename('a', 'again')['state']['hasPending']


@pytest.mark.parametrize('failure', ['modify', 'add', 'pack'])
def test_failure_keeps_state_and_preview(failure, monkeypatch):
    host, _ = loaded(two_pages)
    host.enter_edit()
    host.rename('a', 'renamed')
    host.save_output()
    before = host._state()
    previews = [host.page_preview(i) for i in range(2)]
    if failure == 'pack':
        def fail(*args, **kwargs):
            raise MemoryError('synthetic pack failure')
        monkeypatch.setattr(SpineAtlas, 'pack', fail)
        with pytest.raises(MemoryError):
            host.rename('a', 'lost')
    elif failure == 'modify':
        with pytest.raises(Exception):
            host.modify(['a'], b'invalid PNG', 'straight')
    else:
        with pytest.raises(InvalidRegionName):
            host.add('b', page_bytes(2, 3), None, 'straight')
    assert host._state() == before
    assert [host.page_preview(i) for i in range(2)] == previews


def test_multipage_rename_remove_and_save():
    host, _ = loaded(two_pages)
    original = host.enter_edit()
    other = host.page_preview(1)
    renamed = host.rename('a', 'renamed')
    assert renamed['image'] == original['image']
    assert host.page_preview(1) == other
    result = host.remove(['b'])
    assert 'b' not in result['regions']
    assert result['image'] != original['image']
    assert host.page_preview(1) == other
    output = host.save_output()
    assert [p['name'] for p in output['pages']] == ['p.png', 'q.png']
    for i, page in enumerate(output['pages']):
        assert decode(page['png']).tobytes() == host.page_preview(i)['image']['rgba']
    assert result['regionPages'] == {'a': 'p.png', 'c': 'q.png'}


def test_remove_all_through_wire():
    host, _ = loaded(two_pages)
    host.enter_edit()
    before = host._state()
    result, buffers = wire.call(host, 'remove', '[["a","b","c"]]', [])
    assert json.loads(result)['error']['type'] == 'AtlasError'
    assert buffers == []
    assert host._state() == before


@pytest.mark.parametrize('factory', [one_page, all_pma, mixed_pma])
def test_page_png_and_preview(factory):
    host, _ = loaded(factory)
    result = host.enter_edit()
    for i, name in enumerate(result['pages']):
        preview = host.page_preview(i)
        assert preview['activePage'] == name and preview['activeIndex'] == i
        raw = decode(host.page_png(i, 'premultiplied'))
        assert raw.tobytes() == preview['image']['rgba']
        assert list(raw.size) == [preview['image']['width'], preview['image']['height']]
        straight = decode(host.page_png(i, 'straight'))
        if factory is all_pma:
            assert straight.getpixel((1, 2)) == (28, 56, 66, 128)
        else:
            assert straight.tobytes() == raw.tobytes()
    for i in [-1, len(result['pages'])]:
        with pytest.raises(IndexError):
            host.page_preview(i)
        with pytest.raises(IndexError):
            host.page_png(i, 'straight')


def test_add_modify_composite_order_and_overlay(monkeypatch):
    host, _ = loaded(two_pages)
    host.enter_edit()
    added = host.add('fresh', page_bytes(2, 3), 'q.png', 'straight')
    key = next(r['key'] for r in added['state']['regions'] if r['label'] == 'fresh')
    assert added['regionPages'][key] == 'q.png'
    modified = host.modify(['a'], page_bytes(3, 2), 'straight')
    assert key in modified['regions']
    assert modified['regionPages'][key] == 'q.png'
    calls = []
    original = SpineAtlas.replace_composite
    def record(self, names, image, *, alpha_mode=None):
        calls.append(names)
        return original(self, names, image, alpha_mode=alpha_mode)
    monkeypatch.setattr(SpineAtlas, 'replace_composite', record)
    result = host.modify(['b', 'a'], page_bytes(4, 6), 'straight')
    assert calls == [['b', 'a']]
    assert key in result['regions']
    assert result['regionPages'][key] == 'q.png'
    assert result['regions'][key][2:4] == [2, 3]
    text, pages = host._packed
    views = host._atlas.region_views(text, {n: im.size for n, im in pages.items()})
    assert result['regions'] == {v.key: [v.x, v.y, v.w, v.h, v.rotate] for v in views}


EDIT_CALLS = [
    ('exit_edit', []), ('page_preview', [0]), ('page_png', [0, 'straight']),
    ('modify', [['a'], b'', 'straight']), ('add', ['new', b'', None, 'straight']),
    ('rename', ['a', 'new']), ('remove', [['a']]), ('reset_edits', []),
    ('validate_name', ['new', None]), ('save_output', []),
]


@pytest.mark.parametrize('method,args', EDIT_CALLS)
def test_requires_edit_mode(method, args):
    host, _ = loaded()
    with pytest.raises(AtlasError, match='^not in edit mode$'):
        getattr(host, method)(*args)
    host.enter_edit()
    host.exit_edit()
    with pytest.raises(AtlasError, match='^not in edit mode$'):
        getattr(host, method)(*args)


@pytest.mark.parametrize('method,args', [
    ('modify', [['missing'], page_bytes(), 'straight']),
    ('rename', ['missing', 'new']), ('remove', [['missing']]),
    ('validate_name', ['new', 'missing']),
])
def test_unknown_edit_key(method, args):
    host, _ = loaded()
    host.enter_edit()
    with pytest.raises(RegionNotFound):
        getattr(host, method)(*args)
