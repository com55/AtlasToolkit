from atlas_toolkit.app.desktop_commands import DesktopCommands, RPC_METHODS


def test_rpc_boundary():
    assert RPC_METHODS == frozenset('startup get_pref set_pref set_window_title pick_atlas pick_mod_image pick_skel pick_page_image pick_save_folder pick_save_file existing_output_names write_files open_url open_update_log get_update_download_progress download_update restart_and_install_update install_update_from_file close_answer'.split())


def test_rpc_allowlist_on_class_and_instance(commands):
    assert DesktopCommands.RPC_METHODS is RPC_METHODS
    assert commands.RPC_METHODS is RPC_METHODS

import json
import threading
from types import SimpleNamespace

import pytest

from atlas_toolkit.app import build_info, config
from atlas_toolkit.app.files import DesktopFiles
from atlas_toolkit.app.events import EventBroker


class FakeWindow:
    def __init__(self):
        self.results = []
        self.calls = []
        self.titles = []
        self.destroyed = 0
        self._on_top = False
        self.confirm = False

    @property
    def on_top(self):
        return self._on_top

    @on_top.setter
    def on_top(self, value):
        self.calls.append(("on_top", value))
        self._on_top = value

    def create_file_dialog(self, kind, **kwargs):
        self.calls.append((kind, kwargs))
        return self.results.pop(0)

    def set_title(self, title):
        self.titles.append(title)

    def destroy(self):
        self.calls.append(("destroy", self.on_top))
        self.destroyed += 1

    def create_confirmation_dialog(self, title, message):
        assert self.on_top is True
        self.calls.append((title, message))
        return self.confirm


@pytest.fixture
def commands(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    updates = SimpleNamespace(pending_failure=None, check_for_notification=lambda: {"version": "test"},
                              install_from_file=lambda path: {"ok": True})
    return DesktopCommands(FakeWindow(), DesktopFiles(), EventBroker(), config.AppConfig(),
                           updates, [], lambda: False, SimpleNamespace(OPEN=1, FOLDER=2, SAVE=3))


@pytest.mark.parametrize("method,args", [
    ("pick_atlas", []), ("pick_mod_image", []), ("pick_skel", []),
    ("pick_page_image", ["page.png"]), ("pick_save_folder", []), ("pick_save_file", ["out.png"]),
])
def test_cancel_and_no_window(commands, method, args):
    commands._window.results = [None]
    assert getattr(commands, method)(*args) is None
    commands.set_window(None)
    assert getattr(commands, method)(*args) is None
    commands.set_window_title("test")


@pytest.mark.parametrize("name,adjusted,expected", [
    (".hidden", True, ".hidden.png"), ("out.jpg.", True, "out.jpg.png"),
    ("out", True, "out.png"), ("out.", True, "out.png"),
    (".png", False, ".png"), ("out.jpg", False, "out.jpg"), ("out.PNG", False, "out.PNG"),
])
def test_save_extension(commands, tmp_path, name, adjusted, expected):
    commands._window.results = [[str(tmp_path / name)]]
    result = commands.pick_save_file("default.PNG")
    assert result["adjusted"] is adjusted
    assert result["path"] == str(tmp_path / expected)


def test_startup_once_and_errors(commands, tmp_path):
    path = tmp_path / "test.atlas"
    path.write_text("page.png\n")
    commands._argv = ["app", str(path)]
    first = commands.startup()
    commands._update_thread.join(2)
    assert not commands._update_thread.is_alive()
    path.unlink()
    assert commands.startup() == {**first, "atlas": None, "atlasError": None}
    assert first["atlas"]["atlas"]["name"] == "test.atlas"
    commands._startup_result = None
    assert commands.startup()["atlasError"]
    commands._update_thread.join(2)
    commands._window.results = [[str(path)]]
    with pytest.raises(OSError):
        commands.pick_atlas()


@pytest.mark.parametrize("subscriber,answer", [(True, True), (True, False), (False, True), (False, False)])
def test_closing(commands, subscriber, answer):
    assert commands.on_closing()
    commands._is_unsaved = lambda: True
    window = commands._window
    window.confirm = answer
    if subscriber:
        sub = commands._events.subscribe()
    assert commands.on_closing() is False
    if subscriber:
        assert next(sub)["kind"] == "confirm-close"
        commands.close_answer(answer)
    commands._close_thread.join(2)
    assert not commands._close_thread.is_alive()
    assert window.destroyed == int(answer)
    assert not window.on_top
    if not subscriber:
        assert ("Discard modifications?", "You have unsaved atlas modifications.\nContinue and discard them?") in window.calls
        assert window.calls[0] == ("on_top", True)
    if answer:
        assert ("destroy", False) in window.calls
    commands.close_answer(True)
    if subscriber:
        sub.close()


def test_picks_drop_and_install(commands, tmp_path, monkeypatch):
    atlas = tmp_path / "sample.atlas"
    atlas.write_text("page.png\n")
    commands._window.results = [[str(atlas)]]
    assert commands.pick_atlas()["atlas"]["name"] == atlas.name
    for method, args, name in [("pick_mod_image", [], "a.PNG"), ("pick_skel", [], "a.skel"),
                               ("pick_page_image", ["page.png"], "page.png")]:
        commands._window.results = [[str(tmp_path / name)]]
        assert getattr(commands, method)(*args)["name"] == name
        assert commands._window.calls[-1][1]["directory"] == str(tmp_path)
    sub = commands._events.subscribe()
    for path, kind in [(atlas, "atlas"), (tmp_path / "a.png", "file"), (tmp_path / "missing.atlas", "error")]:
        commands.on_drop({"dataTransfer": {"files": [{"pywebviewFullPath": str(path)}]}, "x": 4, "clientY": 7})
        event = next(sub)
        assert (event["type"], event["x"], event["y"]) == (kind, 4, 7)
    sub.close()
    assert commands.install_update_from_file() == {"ok": False, "error": "Not a development build."}
    monkeypatch.setattr(build_info, "DEV_BUILD", True)
    commands._window.results = [None, [str(tmp_path / "setup.exe")]]
    assert commands.install_update_from_file()["cancelled"]
    assert commands.install_update_from_file()["ok"]


def test_config_threads(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "prefs.json")
    prefs = config.AppConfig()
    def write(worker):
        for key in range(20):
            prefs.set(f"{worker}-{key}", key)
    threads = [threading.Thread(target=write, args=(worker,)) for worker in range(8)]
    started = []
    try:
        for thread in threads:
            thread.start()
            started.append(thread)
    finally:
        for thread in started:
            thread.join()
    assert len(json.loads(config.CONFIG_PATH.read_text())) == 160


def test_delegates_and_os_commands(commands, tmp_path, monkeypatch):
    from atlas_toolkit.app import desktop_commands as module
    commands.set_pref("setting", 3)
    assert commands.get_pref("setting", 0) == 3
    assert commands.get_pref("absent", 9) == 9
    monkeypatch.setattr(module, "get_current_version", lambda: "1.2.3")
    commands.set_window_title("test.atlas")
    commands.set_window_title("")
    assert commands._window.titles == ["Atlas Toolkit v1.2.3 - test.atlas", "Atlas Toolkit v1.2.3"]
    commands._window.results = [[str(tmp_path)]]
    grant = commands.pick_save_folder()["grant"]
    assert commands.existing_output_names(grant, ["out"]) == []
    commands.write_files(grant, [{"name": "out", "data": b"content"}])
    assert (tmp_path / "out").read_bytes() == b"content"
    for method, target in [("get_update_download_progress", "get_progress"), ("download_update", "download"),
                           ("restart_and_install_update", "restart_and_install")]:
        setattr(commands._updates, target, lambda: {"ok": True})
        assert getattr(commands, method)() == {"ok": True}
    opened = []
    monkeypatch.setattr(module.webbrowser, "open", opened.append)
    assert commands.open_url("https://example.org")["ok"]
    assert opened == ["https://example.org"]
    assert not commands.open_url("file:///invalid")["ok"]
    assert not commands.open_update_log()["ok"]
    log = tmp_path / "update.LOG"
    log.write_text("log")
    monkeypatch.setattr(module, "get_update_dir", lambda: tmp_path)
    commands._updates.pending_failure = {"logPath": str(log)}
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.subprocess, "Popen", lambda args, **kwargs: opened.append(args))
    assert commands.open_update_log()["ok"]
    assert opened[-1] == ["xdg-open", str(log)]


def test_update_failure_logged_and_startup_shape(commands, caplog):
    def fail():
        raise RuntimeError("check failed")
    commands._updates.check_for_notification = fail
    commands._updates.pending_failure = {"error": "previous"}
    result = commands.startup()
    thread = commands._update_thread
    thread.join(2)
    assert not thread.is_alive()
    assert result["atlas"] is None and result["atlasError"] is None
    assert result["updateFailure"] == {"error": "previous"}
    assert set(result) == {"version", "devBuild", "atlas", "atlasError", "updateFailure"}
    assert commands.startup() == result
    assert commands._update_thread is thread
    assert "check failed" in caplog.text


def test_unicode_atlas_errors(commands, tmp_path):
    path = tmp_path / "bad.ATLAS"
    path.write_bytes(b"\xff")
    commands._argv = ["app", str(path)]
    assert commands.startup()["atlasError"]
    commands._update_thread.join(2)
    assert not commands._update_thread.is_alive()
    commands._window.results = [[str(path)]]
    with pytest.raises(UnicodeDecodeError):
        commands.pick_atlas()
    sub = commands._events.subscribe()
    assert next(sub)["kind"] == "update-available"
    commands.on_drop({"dataTransfer": {"files": [{"pywebviewFullPath": str(path)}]}})
    event = next(sub)
    assert event["type"] == "error" and event["message"]
    assert event["x"] is None and event["y"] is None
    sub.close()


@pytest.mark.parametrize("method,args", [("pick_mod_image", []), ("pick_skel", []), ("pick_page_image", ["page.png"])])
def test_wrong_extension(commands, tmp_path, method, args):
    commands._window.results = [[str(tmp_path / "wrong.txt")]]
    assert getattr(commands, method)(*args) is None


def test_no_drop_and_duplicate_close(commands):
    commands.on_drop({})
    commands.on_drop({"dataTransfer": {"files": [{}]}})
    assert not commands._events._backlog
    commands._is_unsaved = lambda: True
    sub = commands._events.subscribe()
    assert commands.on_closing() is False
    assert next(sub)["kind"] == "confirm-close"
    thread = commands._close_thread
    assert commands.on_closing() is False
    assert commands._close_thread is thread
    commands.close_answer(True)
    thread.join(2)
    assert not thread.is_alive()
    assert commands.on_closing() is True
    assert commands._window.destroyed == 1
    sub.close()


def test_save_without_png_filter(commands, tmp_path):
    commands._window.results = [[str(tmp_path / "output")]]
    result = commands.pick_save_file("output.atlas")
    assert not result["adjusted"]
    assert "file_types" not in commands._window.calls[-1][1]


def test_installer_dialog_and_forwarded_path(commands, tmp_path, monkeypatch):
    monkeypatch.setattr(build_info, "DEV_BUILD", True)
    path = tmp_path / "setup.exe"
    received = []
    commands._updates.install_from_file = lambda chosen: received.append(chosen) or {"ok": True}
    commands._window.results = [[str(path)]]
    assert commands.install_update_from_file() == {"ok": True}
    assert received == [path]
    assert commands._window.calls == [(commands._file_dialog.OPEN, {
        "allow_multiple": False, "file_types": ("Installer (*.exe)",),
    })]


def test_startup_notification_once(commands):
    calls = []
    commands._updates.check_for_notification = lambda: calls.append(True) or {"version": "new"}
    first = commands.startup()
    thread = commands._update_thread
    thread.join(2)
    assert not thread.is_alive()
    assert commands.startup() == first
    assert commands._update_thread is thread and calls == [True]
    sub = commands._events.subscribe()
    assert next(sub) == {"kind": "update-available", "payload": {"version": "new"}}
    sub.close()


def test_reload_after_another_atlas(commands, tmp_path):
    first = tmp_path / "first.atlas"
    second = tmp_path / "second.atlas"
    first.write_text("first.png\n")
    second.write_text("second.png\n")
    commands._argv = ["app", str(first)]
    initial = commands.startup()
    commands._update_thread.join(2)
    commands._window.results = [[str(second)]]
    commands.pick_atlas()
    assert commands.startup() == {**initial, "atlas": None, "atlasError": None}
    assert commands._window.calls[-1][1]["directory"] == str(tmp_path)


@pytest.mark.parametrize("name", [None, "outside.log", "update.txt", "missing.log", "directory.log", "escape.log"])
def test_refuse_update_log(commands, tmp_path, monkeypatch, name):
    from atlas_toolkit.app import desktop_commands as module
    update_dir = tmp_path / "updates"
    update_dir.mkdir()
    outside = tmp_path / "outside.log"
    outside.write_text("log")
    (update_dir / "update.txt").write_text("text")
    (update_dir / "directory.log").mkdir()
    (update_dir / "escape.log").symlink_to(outside)
    path = outside if name == "outside.log" else update_dir / (name or "unused")
    commands._updates.pending_failure = {"logPath": str(path)} if name else None
    monkeypatch.setattr(module, "get_update_dir", lambda: update_dir)
    opened = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: opened.append(a))
    monkeypatch.setattr(module.os, "startfile", lambda *a: opened.append(a), raising=False)
    assert commands.open_update_log() == {"ok": False, "error": "Log file not found."}
    assert opened == []


@pytest.mark.parametrize("client,expected", [(None, 5), (0, 0)])
def test_drop_coordinate_fallback(commands, tmp_path, client, expected):
    sub = commands._events.subscribe()
    commands.on_drop({"dataTransfer": {"files": [{"pywebviewFullPath": str(tmp_path / "a.png")}]},
                      "clientX": client, "x": 5, "clientY": client, "y": 5})
    event = sub.get(timeout=2)
    assert (event["x"], event["y"]) == (expected, expected)
    sub.close()


def test_unexpected_drop_error(commands, tmp_path, monkeypatch, caplog):
    def fail(path):
        raise RuntimeError("unexpected drop")
    monkeypatch.setattr(commands._files, "register", fail)
    commands.on_drop({"dataTransfer": {"files": [{"pywebviewFullPath": str(tmp_path / "a.png")}]}})
    commands.on_drop({"dataTransfer": {"files": [None]}})
    assert not commands._events._backlog
    assert "Drop error" in caplog.text


@pytest.mark.parametrize("answer", [True, False])
def test_close_subscriber_disconnect(commands, answer):
    commands._is_unsaved = lambda: True
    commands._window.confirm = answer
    sub = commands._events.subscribe()
    assert commands.on_closing() is False
    assert sub.get(timeout=2)["kind"] == "confirm-close"
    sub.close()
    thread = commands._close_thread
    thread.join(3)
    try:
        assert not thread.is_alive()
        assert commands._window.destroyed == int(answer)
        assert ("Discard modifications?", "You have unsaved atlas modifications.\nContinue and discard them?") in commands._window.calls
    finally:
        commands.close_answer(False)
        thread.join(2)


def test_close_republish_after_reconnect(commands):
    commands._is_unsaved = lambda: True
    sub = commands._events.subscribe()
    commands.on_closing()
    assert sub.get(timeout=2)["kind"] == "confirm-close"
    sub.close()
    replacement = commands._events.subscribe()
    try:
        assert commands.on_closing() is False
        assert replacement.get(timeout=0.5)["kind"] == "confirm-close"
    finally:
        commands.close_answer(False)
        commands._close_thread.join(2)
        replacement.close()


def test_dialog_arguments(commands, tmp_path):
    atlas = tmp_path / "a.atlas"
    atlas.write_text("a.png\n")
    commands._files.atlas_bundle(atlas)
    for method, args, filters in [
        ("pick_atlas", [], ("Atlas Files (*.atlas)", "All files (*.*)")),
        ("pick_mod_image", [], ("PNG Files (*.png)", "All files (*.*)")),
        ("pick_skel", [], ("Skeleton Files (*.skel)", "All files (*.*)")),
        ("pick_page_image", ["a.png"], ("PNG images (*.png)",)),
    ]:
        commands._window.results = [None]
        getattr(commands, method)(*args)
        assert commands._window.calls[-1] == (commands._file_dialog.OPEN, {
            "allow_multiple": False, "directory": str(tmp_path), "file_types": filters})
    commands._window.results = [None]
    commands.pick_save_file("out.png")
    assert commands._window.calls[-1] == (commands._file_dialog.SAVE, {
        "directory": str(tmp_path), "save_filename": "out.png",
        "file_types": ("PNG image (*.png)", "All files (*.*)")})


def test_failed_write_keeps_grant(commands, tmp_path):
    folder = tmp_path / "missing"
    grant = commands._files.grant_folder(folder)["grant"]
    with pytest.raises(OSError):
        commands.write_files(grant, [{"name": "out", "data": b"content"}])
    assert commands.existing_output_names(grant, ["out"]) == []
    folder.mkdir()
    commands.write_files(grant, [{"name": "out", "data": b"content"}])
    assert (folder / "out").read_bytes() == b"content"
    with pytest.raises(PermissionError):
        commands.existing_output_names(grant, ["out"])


def test_missing_fixture_has_two_missing_pages(tmp_path):
    from scripts.serve_desktop_dev import make_fixture
    from atlas_toolkit.document import AtlasDocument

    make_fixture(tmp_path)
    document = AtlasDocument.parse((tmp_path / "missing/missing.atlas").read_text())
    assert [page.filename for page in document.pages] == ["missing.png", "other.png"]
    assert not (tmp_path / "missing/missing.png").exists()
    assert not (tmp_path / "missing/other.png").exists()
    assert (tmp_path / "missing-src/missing.png").is_file()
