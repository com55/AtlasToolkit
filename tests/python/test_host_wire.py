# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
import json

import pytest

from atlas_toolkit.api import CanvasTooLarge
from atlas_toolkit.host import AppHost, wire
from .host_fixtures import one_page


def test_bytes_round_trip_through_the_wire():
    class H:
        def echo(self, data, n):
            return {"data": data, "n": n}
    text, out = wire.call(H(), "echo", json.dumps([{"$buf": 0}, 3]), [b"xy"])
    assert json.loads(text) == {"ok": True, "value": {"data": {"$buf": 0}, "n": 3}} and out == [b"xy"]


def test_an_error_returns_type_and_message():
    error = CanvasTooLarge(101, 202, 100)
    class H:
        def fail(self):
            raise error
    text, buffers = wire.call(H(), "fail", "[]", [])
    assert json.loads(text) == {"ok": False, "error": {"type": "CanvasTooLarge", "message": str(error)}}
    assert buffers == []


@pytest.mark.parametrize("method", ["_private", "missing", "value"])
def test_private_methods_are_refused(method):
    class H:
        value = 4
        def _private(self):
            pytest.fail("private method called")
    text, buffers = wire.call(H(), method, "[]", [])
    result = json.loads(text)
    assert result["ok"] is False
    assert result["error"]["type"] == "ValueError"
    assert result["error"]["message"]
    assert buffers == []


def test_recursive_buffers_tuples_and_exact_marker():
    class H:
        def echo(self, data):
            assert data == [{"nested": b"in"}, {"$buf": 0, "other": True}]
            return (data, bytearray(b"two"), memoryview(b"three"))
    text, buffers = wire.call(H(), "echo", '[[{"nested":{"$buf":0}},{"$buf":0,"other":true}]]', [b"in"])
    assert text == '{"ok":true,"value":[[{"nested":{"$buf":0}},{"$buf":0,"other":true}],{"$buf":1},{"$buf":2}]}'
    assert buffers == [b"in", b"two", b"three"]
    assert all(type(item) is bytes for item in buffers)


@pytest.mark.parametrize("args", ['{}', 'null', 'bad json', '[{"$buf":4}]'])
def test_invalid_arguments_return_error(args):
    class H:
        def echo(self, *args):
            pytest.fail("invalid args reached host")
    text, buffers = wire.call(H(), "echo", args, [])
    assert json.loads(text)["ok"] is False
    assert buffers == []


def test_encoding_failure_discards_partial_buffers():
    class H:
        def value(self):
            return [b"partial", object()]
    text, buffers = wire.call(H(), "value", "[]", [])
    assert json.loads(text)["error"]["type"] == "TypeError"
    assert buffers == []


def test_base_exceptions_are_not_caught():
    class H:
        def stop(self):
            raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        wire.call(H(), "stop", "[]", [])


def test_non_ascii_page_and_region_round_trip():
    atlas_text, pages = one_page(page="頁.png", first="頭")
    host = AppHost()
    args = ["図.atlas", atlas_text, {"頁.png": {"$buf": 0}}, None, None]
    text, buffers = wire.call(host, "load", json.dumps(args), [pages["頁.png"]])
    state = json.loads(text)["value"]
    assert state["pages"] == ["頁.png"]
    assert state["regions"][0] == {"key": "頭", "label": "頭"}
    assert "頁" in text and "頭" in text and buffers == []
    text, buffers = wire.call(host, "extract", '[["頭"],false,"straight"]', [])
    assert json.loads(text)["value"] == [{"filename": "頭.png", "png": {"$buf": 0}}]
    assert len(buffers) == 1
