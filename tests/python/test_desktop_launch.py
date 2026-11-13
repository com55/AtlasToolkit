"""Desktop assembly contracts, independent of the optional GUI package."""
import json
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

import pytest

from atlas_toolkit.app import desktop
from atlas_toolkit.app.rpc_frame import decode_frame, encode_frame
from .host_fixtures import one_page, page_bytes


@pytest.fixture
def site(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Desktop test</h1>", encoding="utf-8")
    return tmp_path


@pytest.fixture
def assembled(site, tmp_path, monkeypatch):
    monkeypatch.setattr("atlas_toolkit.app.config.CONFIG_PATH", tmp_path / "config.json")
    urls = []
    window = Mock()
    window.create_confirmation_dialog.return_value = False
    file_dialog = SimpleNamespace(OPEN="open", SAVE="save", FOLDER="folder")

    def factory(url):
        urls.append(url)
        return window

    server, commands, returned_window = desktop.build_desktop(
        site, ["app"], None, factory, file_dialog,
    )
    try:
        yield server, commands, returned_window, window, urls
    finally:
        if hasattr(commands, "_close_thread"):
            commands._close_thread.join(timeout=2)
        commands.set_window(None)
        server.stop()


def rpc(url, target, method, args, buffers=()):
    parts = urlsplit(url)
    body = encode_frame(
        {"target": target, "method": method, "argsJson": json.dumps(args)},
        list(buffers),
    )
    request = Request(urljoin(url, "api/rpc"), data=body, headers={
        "Origin": f"{parts.scheme}://{parts.netloc}",
        "Content-Type": "application/octet-stream",
    })
    with urlopen(request, timeout=30) as response:
        header, _ = decode_frame(response.read())
    return json.loads(header["json"])


def test_build_serves_site_and_binds_close_state(assembled):
    server, commands, returned_window, window, urls = assembled
    assert returned_window is window
    assert len(urls) == 1
    assert urls[0].startswith("http://127.0.0.1:")
    with urlopen(urls[0], timeout=30) as response:
        assert response.read() == b"<h1>Desktop test</h1>"
    assert commands.on_closing() is True
    text, pages = one_page()
    server.host.load("test.atlas", text, pages, None, None)
    assert rpc(urls[0], "engine", "enter_edit", [])["ok"]
    assert rpc(urls[0], "engine", "modify", [["a"], {"$buf": 0}, "straight"],
               [page_bytes(4, 3)])["ok"]
    assert server.unsaved is True
    assert commands.on_closing() is False


def test_close_handler_is_not_rpc_callable(assembled, monkeypatch):
    _, commands, _, _, urls = assembled
    on_closing = Mock(return_value=True)
    monkeypatch.setattr(commands, "on_closing", on_closing)
    result = rpc(urls[0], "desktop", "on_closing", [])
    assert result["ok"] is False
    assert result["error"]["type"] == "ValueError"
    on_closing.assert_not_called()


@pytest.mark.parametrize("raises", [False, True])
def test_failed_window_creation_stops_server(site, raises):
    urls = []

    def factory(url):
        urls.append(url)
        if raises:
            raise ValueError("factory failed")
        return None

    error = ValueError if raises else RuntimeError
    message = "factory failed" if raises else "window creation failed"
    with pytest.raises(error, match=message):
        desktop.build_desktop(site, ["app"], None, factory, object())
    assert len(urls) == 1
    with pytest.raises(URLError):
        urlopen(urls[0], timeout=30)


def test_source_site(monkeypatch):
    monkeypatch.setattr(desktop, "is_source_run", lambda: True)
    assert desktop.desktop_site() == desktop.app_root() / "dist" / "desktop"


def test_packaged_site(monkeypatch):
    monkeypatch.setattr(desktop, "is_source_run", lambda: False)
    assert desktop.desktop_site() == desktop.resource_path("web")
