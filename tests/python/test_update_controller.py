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


def test_installer_launch_visible_watcher_hidden(tmp_path, monkeypatch):
    from atlas_toolkit.update import controller
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"installer")
    executable = tmp_path / "app.exe"
    executable.write_bytes(b"app")
    monkeypatch.setattr(controller, "get_update_dir", lambda: tmp_path)
    monkeypatch.setattr(controller, "get_relaunch_executable_path", lambda: executable)
    monkeypatch.setattr(controller, "is_running_as_exe", lambda: True)
    calls = []
    monkeypatch.setattr(controller, "_shell_execute", lambda file, params, work_dir, show=0: calls.append((file, params, work_dir, show)))
    monkeypatch.setattr(controller.threading, "Timer", lambda *a: type("Timer", (), {"start": lambda self: None})())
    updates = controller.UpdateController()
    updates._installer_path = installer
    updates._ready = True
    assert updates.restart_and_install() == {"ok": True}
    assert len(calls) == 2
    assert calls[0][0] == "powershell.exe" and calls[0][3] == 0
    assert calls[1][0] == str(installer) and calls[1][3] == 1
