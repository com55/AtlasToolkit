

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
    monkeypatch.setattr(updates, "restart_and_install", lambda: {"ok": True})
    assert updates.install_from_file(source) == {"ok": True}
    assert updates._installer_path.name == "AtlasToolkit-Setup-x64.exe"
    assert updates._installer_path.read_bytes() == b"installer"
    assert updates._ready and updates._version == "local file"
    assert updates._relaunch_args == ["sample.atlas"]
    assert updates._release_url == ""
    params = controller._inno_install_params(tmp_path / "install.log")
    assert "/SILENT" in params and "/VERYSILENT" not in params
    monkeypatch.setattr(controller, "is_running_as_exe", lambda: False)
    assert not updates.install_from_file(source)["ok"]
