# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0


def test_local_installer(tmp_path, monkeypatch):
    from atlas_toolkit.update import controller
    source = tmp_path / "input.exe"
    source.write_bytes(b"installer")
    output = tmp_path / "updates"
    output.mkdir()
    monkeypatch.setattr(controller, "get_update_dir", lambda: output)
    monkeypatch.setattr(controller, "is_running_as_exe", lambda: True)
    monkeypatch.setattr(controller.sys, "argv", ["app", "sample.atlas"])
    updates = controller.UpdateController()
    restarted = []
    monkeypatch.setattr(updates, "restart_and_install", lambda: restarted.append(True) or {"ok": True})
    assert updates.install_from_file(source) == {"ok": True}
    assert restarted == [True]
    assert updates._installer_path.name == "AtlasToolkit-Setup-x64.exe"
    assert updates._installer_path.read_bytes() == b"installer"
    assert updates._ready and updates._version == "local file"
    assert updates._relaunch_args == ["sample.atlas"]
    assert updates._release_url == ""
    params = controller._inno_install_params(tmp_path / "install.log")
    assert "/SILENT" in params and "/VERYSILENT" not in params
    monkeypatch.setattr(controller, "is_running_as_exe", lambda: False)
    assert not updates.install_from_file(source)["ok"]


def test_local_installer_same_path(tmp_path, monkeypatch):
    from atlas_toolkit.update import controller
    source = tmp_path / "AtlasToolkit-Setup-x64.exe"
    source.write_bytes(b"installer")
    monkeypatch.setattr(controller, "get_update_dir", lambda: tmp_path)
    monkeypatch.setattr(controller, "is_running_as_exe", lambda: True)
    updates = controller.UpdateController()
    called = []
    monkeypatch.setattr(updates, "restart_and_install", lambda: called.append(True) or {"ok": True})
    assert updates.install_from_file(source) == {"ok": True}
    assert called == [True]
    assert source.read_bytes() == b"installer"


def test_local_installer_copy_error(tmp_path, monkeypatch):
    from atlas_toolkit.update import controller
    monkeypatch.setattr(controller, "get_update_dir", lambda: tmp_path)
    monkeypatch.setattr(controller, "is_running_as_exe", lambda: True)
    def fail(*args):
        raise OSError("copy failed")
    monkeypatch.setattr(controller.shutil, "copyfile", fail)
    updates = controller.UpdateController()
    called = []
    monkeypatch.setattr(updates, "restart_and_install", lambda: called.append(True))
    assert updates.install_from_file(tmp_path / "source.exe") == {"ok": False, "error": "copy failed"}
    assert called == []
    assert not updates._ready


class FakeProcess:
    def __init__(self, codes):
        self.pid = 4321
        self.codes = list(codes)
        self.closed = False

    def exit_code(self):
        return self.codes.pop(0) if len(self.codes) > 1 else self.codes[0]

    def close(self):
        self.closed = True


def _ready_controller(tmp_path, monkeypatch, process, *, watcher_claims=True, watcher_error=None,
                      timeout=0.3):
    from atlas_toolkit.update import controller
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"installer")
    executable = tmp_path / "app.exe"
    executable.write_bytes(b"app")
    monkeypatch.setattr(controller, "get_update_dir", lambda: tmp_path)
    monkeypatch.setattr(controller, "get_relaunch_executable_path", lambda: executable)
    monkeypatch.setattr(controller, "is_running_as_exe", lambda: True)
    monkeypatch.setattr(controller, "_HANDOFF_TIMEOUT_SECONDS", timeout)
    calls, timers = [], []

    def launch(file, params, work_dir, show=1):
        calls.append(("installer", file, show))
        if isinstance(process, Exception):
            raise process
        return process

    def shell(file, params, work_dir, show=0):
        calls.append(("watcher", file, show))
        if watcher_error:
            raise watcher_error
        if watcher_claims:
            claim, = tmp_path.glob("relaunch_after_*.ps1")
            name = claim.name.replace("relaunch_after_", "handoff_").replace(".ps1", ".claim")
            (tmp_path / name).write_text("")

    monkeypatch.setattr(controller, "_launch_process", launch)
    monkeypatch.setattr(controller, "_shell_execute", shell)
    monkeypatch.setattr(controller.threading, "Timer",
                        lambda *a: type("Timer", (), {"start": lambda self: timers.append(a)})())
    updates = controller.UpdateController()
    updates._installer_path = installer
    updates._relaunch_args = [r"C:\My Atlases\hero.atlas"]
    updates._ready = True
    return controller, updates, calls, timers


def test_installer_starts_before_watcher(tmp_path, monkeypatch):
    import json
    import time
    process = FakeProcess([None])
    controller, updates, calls, timers = _ready_controller(tmp_path, monkeypatch, process, timeout=30)
    started = time.monotonic()
    assert updates.restart_and_install() == {"ok": True}
    assert time.monotonic() - started < 5  # exits as soon as the watcher claims
    assert [c[0] for c in calls] == ["installer", "watcher"]
    assert calls[0][2] == 1 and calls[1] == ("watcher", "powershell.exe", 0)
    assert process.closed and len(timers) == 1
    script, = tmp_path.glob("relaunch_after_*.ps1")
    text = script.read_text()
    assert "Get-Process -Id 4321" in text and "-Name" not in text
    assert "-ArgumentList $ArgLine" in text
    meta = json.loads((tmp_path / "pending_update.json").read_text())
    # One pre-quoted argument string survives Start-Process for paths with spaces.
    assert meta["relaunch_command_line"] == '"C:\\My Atlases\\hero.atlas"'
    assert "installer_process_name" not in meta


def test_installer_launch_failure_starts_no_watcher(tmp_path, monkeypatch):
    error = OSError(193, "%1 is not a valid Win32 application")
    controller, updates, calls, timers = _ready_controller(tmp_path, monkeypatch, error)
    result = updates.restart_and_install()
    assert not result["ok"] and result["error"].startswith("Could not start the installer:")
    assert [c[0] for c in calls] == ["installer"] and timers == []
    assert not list(tmp_path.glob("relaunch_after_*.ps1"))


def test_installer_early_exit_is_reported_in_app(tmp_path, monkeypatch):
    process = FakeProcess([1])
    controller, updates, calls, timers = _ready_controller(
        tmp_path, monkeypatch, process, watcher_claims=False)
    result = updates.restart_and_install()
    assert result == {"ok": False, "error": controller.installer_failure_message(1), "logPath": ""}
    assert "damaged" in result["error"] and "exit code 1" in result["error"]
    assert timers == [] and process.closed
    assert not list(tmp_path.glob("handoff_*.claim"))


def test_watcher_claim_wins_over_early_exit(tmp_path, monkeypatch):
    process = FakeProcess([1])
    controller, updates, calls, timers = _ready_controller(tmp_path, monkeypatch, process)
    assert updates.restart_and_install() == {"ok": True}
    assert len(timers) == 1


def test_missing_watcher_still_hands_over(tmp_path, monkeypatch):
    process = FakeProcess([None])
    controller, updates, calls, timers = _ready_controller(
        tmp_path, monkeypatch, process, watcher_error=OSError("blocked"))
    assert updates.restart_and_install() == {"ok": True}
    assert len(timers) == 1 and process.closed


def test_install_result_round_trip(tmp_path, monkeypatch):
    import json
    from atlas_toolkit.update import controller
    from atlas_toolkit.app import launch
    log = tmp_path / "inno.log"
    (tmp_path / controller.RESULT_FILE_NAME).write_text(
        "\ufeff" + json.dumps({"exit_code": 5, "log_path": str(log), "release_url": "u"}),
        encoding="utf-8")
    monkeypatch.setattr(launch, "get_update_dir", lambda: tmp_path)
    clean, payload, debug, relaunched = launch._consume_launch_flags(
        ["hero.atlas", "--update-install-failed"])
    assert clean == ["hero.atlas"] and relaunched and not debug
    assert payload == {"message": controller.installer_failure_message(5),
                       "logPath": str(log), "releaseUrl": "u"}
    assert not (tmp_path / controller.RESULT_FILE_NAME).exists()
    # Without a result file (an older watcher), the legacy flags still describe it.
    _, payload, _, _ = launch._consume_launch_flags(
        ["--update-install-failed", "--update-failed-message", "old"])
    assert payload["message"] == "old"
    assert launch._consume_launch_flags(["--update-installed"]) == ([], None, False, True)
    assert launch._consume_launch_flags(["a.atlas"]) == (["a.atlas"], None, False, False)
