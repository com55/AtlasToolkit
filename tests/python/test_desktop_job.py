# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas_toolkit_app.app import config
from atlas_toolkit_app.app.desktop_commands import DesktopCommands
from atlas_toolkit_app.app.events import EventBroker
from atlas_toolkit_app.app.files import DesktopFiles
from atlas_toolkit_app.app.job import ReturnJob, parse_job_args

from .test_desktop_commands import FakeWindow

NO_ATLAS = "No atlas among the files given to --return."


def test_parse_job_args():
    rest, job = parse_job_args(["--return", "out", "--host-name", "  Example Host  ", "a.atlas", "a.png"])
    assert rest == ["a.atlas", "a.png"]
    assert job.dir == Path("out")
    assert job.host_name == "Example Host"
    assert job.files == [Path("a.atlas"), Path("a.png")]
    assert parse_job_args(["a.atlas"]) == (["a.atlas"], None)
    _, job = parse_job_args(["--host-name", "x" * 50, "--return", "out"])
    assert job.host_name == "x" * 40
    assert job.files == []


def _result(directory):
    return json.loads((directory / "result.json").read_text(encoding="utf-8"))


def test_result_json_saved(tmp_path):
    job = ReturnJob(tmp_path, "", [])
    job.record_saved(["p.png", "a.atlas"])
    assert job.write_result() == 0
    assert _result(tmp_path) == {"protocol": 1, "status": "saved", "files": ["p.png", "a.atlas"], "message": None}
    assert [p.name for p in tmp_path.iterdir()] == ["result.json"]
    (tmp_path / "result.json").unlink()
    assert job.write_result() == 0
    assert not (tmp_path / "result.json").exists()


def test_result_json_cancelled(tmp_path):
    job = ReturnJob(tmp_path / "new", "", [])
    assert job.write_result() == 1
    assert _result(tmp_path / "new") == {"protocol": 1, "status": "cancelled", "files": [], "message": None}


def test_result_json_error(tmp_path):
    job = ReturnJob(tmp_path, "", [])
    job.record_error("x")
    assert job.write_result() == 1
    assert _result(tmp_path) == {"protocol": 1, "status": "error", "files": [], "message": "x"}


def _commands(tmp_path, monkeypatch, job):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    calls = []
    updates = SimpleNamespace(pending_failure=None, check_for_notification=lambda: calls.append(1))
    commands = DesktopCommands(FakeWindow(), DesktopFiles(), EventBroker(), config.AppConfig(),
                               updates, ["app"], lambda: True, SimpleNamespace(OPEN=1, FOLDER=2, SAVE=3), job=job)
    return commands, calls


def test_startup_with_job_opens_given_files(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "hero.atlas").write_text("hero.png\nsize: 4,4\na\n bounds: 0,0,1,1\n", encoding="utf-8")
    (src / "hero.png").write_bytes(b"png")
    (src / "hero-pro.skel").write_bytes(b"skel")
    files = [src / "hero.atlas", src / "hero.png", src / "hero-pro.skel"]
    commands, calls = _commands(tmp_path, monkeypatch, ReturnJob(tmp_path / "ret", "Example Host", files))
    result = commands.startup()
    assert result["job"] == {"hostName": "Example Host"}
    assert result["atlas"]["atlas"]["name"] == "hero.atlas"
    assert result["atlas"]["skel"]["name"] == "hero-pro.skel"
    assert getattr(commands, "_update_thread", None) is None
    assert calls == []


def test_startup_with_job_and_no_atlas(tmp_path, monkeypatch):
    (tmp_path / "x.png").write_bytes(b"png")
    job = ReturnJob(tmp_path / "ret", "", [tmp_path / "x.png"])
    commands, _ = _commands(tmp_path, monkeypatch, job)
    result = commands.startup()
    assert result["atlas"] is None
    assert result["atlasError"] == NO_ATLAS
    assert commands.on_closed() == 1
    assert _result(tmp_path / "ret") == {"protocol": 1, "status": "error", "files": [], "message": NO_ATLAS}


def test_startup_without_job_reports_none(tmp_path, monkeypatch):
    commands, _ = _commands(tmp_path, monkeypatch, None)
    assert commands.startup()["job"] is None
    commands._update_thread.join(2)


def test_return_folder_and_finish_return(tmp_path, monkeypatch):
    job = ReturnJob(tmp_path / "ret" / "deep", "", [])
    commands, _ = _commands(tmp_path, monkeypatch, job)
    grant = commands.return_folder()
    assert (tmp_path / "ret" / "deep").is_dir()
    commands.write_files(grant["grant"], [{"name": "a.atlas", "data": b"text"}])
    assert (tmp_path / "ret" / "deep" / "a.atlas").read_bytes() == b"text"
    commands.finish_return(["a.atlas"])
    assert commands._window.destroyed == 1
    assert commands.on_closing() is True
    assert commands.on_closed() == 0
    assert _result(tmp_path / "ret" / "deep")["status"] == "saved"
    assert _result(tmp_path / "ret" / "deep")["files"] == ["a.atlas"]


def test_on_closed_without_save_is_cancelled(tmp_path, monkeypatch):
    commands, _ = _commands(tmp_path, monkeypatch, ReturnJob(tmp_path / "ret", "", []))
    assert commands.on_closed() == 1
    assert _result(tmp_path / "ret")["status"] == "cancelled"


def test_return_rpcs_refuse_without_job(tmp_path, monkeypatch):
    commands, _ = _commands(tmp_path, monkeypatch, None)
    with pytest.raises(RuntimeError, match="Not opened with --return"):
        commands.return_folder()
    with pytest.raises(RuntimeError, match="Not opened with --return"):
        commands.finish_return([])
    assert commands.on_closed() == 0


def test_launch_passes_the_job_and_exits_with_its_code(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from atlas_toolkit_app.app import launch
    monkeypatch.setattr(launch, "configure_stdio", lambda: None)
    monkeypatch.setattr(launch, "start_session_log", lambda **kwargs: None)
    monkeypatch.setattr(launch, "install_excepthooks", lambda: None)
    monkeypatch.setattr(launch, "is_source_run", lambda: False)
    monkeypatch.setattr(launch.sys, "argv", ["app", "--return", str(tmp_path / "ret"), "--host-name", "Example Host", "a.atlas"])
    monkeypatch.setattr(launch.sys, "platform", "linux")
    (tmp_path / "index.html").write_text("test")
    monkeypatch.setattr(launch, "desktop_site", lambda: tmp_path)
    monkeypatch.setattr(launch.webview, "screens", [SimpleNamespace(width=1920, height=1080)])
    class Hooks(list):
        def __iadd__(self, handler):
            self.append(handler)
            return self

    seen = {}
    window = SimpleNamespace(events=SimpleNamespace(closing=Hooks(), closed=Hooks(), loaded=Hooks()))
    commands = MagicMock()
    commands.on_closed.return_value = 1

    def assemble(site, argv, failure, factory, dialog, job=None):
        seen.update(argv=argv, job=job)
        return MagicMock(), commands, window
    monkeypatch.setattr(launch, "build_desktop", assemble)

    def start(**kwargs):
        # The window closes: launch's closed handler runs.
        for handler in window.events.closed:
            handler()
    monkeypatch.setattr(launch.webview, "start", start)
    with pytest.raises(SystemExit) as exit_info:
        launch.run()
    assert exit_info.value.code == 1
    assert seen["argv"] == ["app", "a.atlas"]
    assert seen["job"].host_name == "Example Host"
    assert seen["job"].files == [Path("a.atlas")]


def test_a_previous_result_is_removed_when_the_job_starts(tmp_path, monkeypatch):
    (tmp_path / "ret").mkdir()
    (tmp_path / "ret" / "result.json").write_text('{"status": "saved"}', encoding="utf-8")
    commands, _ = _commands(tmp_path, monkeypatch, ReturnJob(tmp_path / "ret", "", []))
    assert not (tmp_path / "ret" / "result.json").exists()
    assert commands.on_closed() == 1


def test_a_failed_result_write_raises_and_can_be_retried(tmp_path, monkeypatch):
    job = ReturnJob(tmp_path, "", [])
    job.record_saved(["a.atlas"])
    real = os.replace
    monkeypatch.setattr("atlas_toolkit_app.app.job.os.replace", lambda *a: (_ for _ in ()).throw(PermissionError("busy")))
    with pytest.raises(PermissionError):
        job.write_result()
    monkeypatch.setattr("atlas_toolkit_app.app.job.os.replace", real)
    assert job.write_result() == 0
    assert _result(tmp_path)["status"] == "saved"


def test_launch_exits_1_and_stops_the_server_when_the_result_cannot_be_written(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from atlas_toolkit_app.app import launch
    monkeypatch.setattr(launch, "configure_stdio", lambda: None)
    monkeypatch.setattr(launch, "start_session_log", lambda **kwargs: None)
    monkeypatch.setattr(launch, "install_excepthooks", lambda: None)
    monkeypatch.setattr(launch, "is_source_run", lambda: False)
    monkeypatch.setattr(launch.sys, "argv", ["app", "--return", str(tmp_path / "ret")])
    monkeypatch.setattr(launch.sys, "platform", "linux")
    (tmp_path / "index.html").write_text("test")
    monkeypatch.setattr(launch, "desktop_site", lambda: tmp_path)
    monkeypatch.setattr(launch.webview, "screens", [SimpleNamespace(width=1920, height=1080)])

    class Hooks(list):
        def __iadd__(self, handler):
            self.append(handler)
            return self

    window = SimpleNamespace(events=SimpleNamespace(closing=Hooks(), closed=Hooks(), loaded=Hooks()))
    server, commands = MagicMock(), MagicMock()
    commands.on_closed.side_effect = PermissionError("busy")
    monkeypatch.setattr(launch, "build_desktop", lambda *a, job=None: (server, commands, window))

    def start(**kwargs):
        for handler in window.events.closed:
            with pytest.raises(PermissionError):
                handler()
    monkeypatch.setattr(launch.webview, "start", start)
    with pytest.raises(SystemExit) as exit_info:
        launch.run()
    assert exit_info.value.code == 1
    server.stop.assert_called_once()
