"""Registered desktop inputs and dialog-scoped write grants."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from atlas_toolkit.app.files import DesktopFiles, _atlas_page_filenames


def asset_id(item):
    assert set(item) == {"name", "url"}
    assert item["url"].startswith("asset/")
    return item["url"].removeprefix("asset/")


def test_page_parser():
    assert _atlas_page_filenames(" a.png\nmissing.png\nA.PNG\nkey: x.png\nregion\n") == [
        "a.png", "missing.png"
    ]


def test_bundle_and_registration_lifetime(tmp_path):
    registry = DesktopFiles()
    assert registry.atlas_dir is None
    atlas = tmp_path / "sample.atlas"
    atlas.write_text("a.png\nmissing.png\nfolder.png\nA.PNG\n", encoding="utf-8")
    page = tmp_path / "a.png"
    page.write_bytes(b"png")
    (tmp_path / "folder.png").mkdir()
    skel = atlas.with_suffix(".skel")
    skel.write_bytes(b"skel")
    grant = registry.grant_folder(tmp_path)
    bundle = registry.atlas_bundle(atlas)
    assert set(bundle) == {"atlas", "pages", "skel", "dir"}
    assert bundle["dir"] == str(tmp_path.resolve())
    assert registry.atlas_dir == tmp_path.resolve()
    assert set(bundle["pages"]) == {"a.png"}
    old_ids = [asset_id(bundle["atlas"]), asset_id(bundle["pages"]["a.png"]), asset_id(bundle["skel"])]
    assert [registry.lookup(key) for key in old_ids] == [atlas.resolve(), page.resolve(), skel.resolve()]
    second = tmp_path / "other.atlas"
    second.write_text("", encoding="utf-8")
    assert registry.atlas_bundle(second)["skel"] is None
    assert all(registry.lookup(key) is None for key in old_ids)
    added = registry.register(page)
    assert added["name"] == page.name
    key = asset_id(added)
    page.unlink()
    assert registry.lookup(key) == page.resolve()
    assert registry.lookup("unknown") is None
    registry.write_files(grant["grant"], [{"name": "out.bin", "data": b"out"}])
    assert (tmp_path / "out.bin").read_bytes() == b"out"


def test_failed_bundle_preserves_state(tmp_path):
    registry = DesktopFiles()
    atlas = tmp_path / "sample.atlas"
    atlas.write_text("", encoding="utf-8")
    key = asset_id(registry.atlas_bundle(atlas)["atlas"])
    for unreadable in [tmp_path / "missing.atlas", tmp_path]:
        with pytest.raises(OSError):
            registry.atlas_bundle(unreadable)
        assert registry.lookup(key) == atlas.resolve()
        assert registry.atlas_dir == tmp_path.resolve()


def test_token_and_resolved_paths(tmp_path, monkeypatch):
    calls = []
    def token(size):
        calls.append(size)
        return str(len(calls))
    monkeypatch.setattr("atlas_toolkit.app.files.secrets.token_urlsafe", token)
    registry = DesktopFiles()
    path = tmp_path / "nested" / ".." / "file.bin"
    assert registry.register(path) == {"name": "file.bin", "url": "asset/1"}
    assert registry.lookup("1") == path.resolve()
    assert registry.grant_folder(tmp_path) == {"grant": "2", "path": str(tmp_path.resolve())}
    assert registry.grant_file(path) == {"grant": "3", "path": str(path.resolve())}
    assert calls == [16, 16, 16]


def test_folder_writes_and_existing_names(tmp_path):
    registry = DesktopFiles()
    grant = registry.grant_folder(tmp_path)["grant"]
    registry.write_files(grant, [{"name": "a.bin", "data": bytearray(b"a")}, {"name": "b.bin", "data": [98]}])
    assert (tmp_path / "a.bin").read_bytes() == b"a"
    assert (tmp_path / "b.bin").read_bytes() == b"b"
    (tmp_path / "directory").mkdir()
    assert registry.existing_output_names(grant, ["a.bin", "directory", "missing", None, "", ".", "..", "../escape", "C:x", "x\\y", "x\0"]) == ["a.bin", "directory"]


@pytest.mark.parametrize("name", [
    None, 12, "", ".", "..", "a/b", "a\\b", "a\0b", "C:out.png", "z:out", "/out",
    "NUL", "nul.png", "COM1", "con.atlas", "LPT9.png", "out:stream", ":stream",
    "out.png.", "out.png ",
])
def test_invalid_names_prevent_all_writes(tmp_path, name):
    registry = DesktopFiles()
    grant = registry.grant_folder(tmp_path)["grant"]
    with pytest.raises(ValueError):
        registry.write_files(grant, [{"name": "valid.bin", "data": b"valid"}, {"name": name, "data": b"bad"}])
    assert list(tmp_path.iterdir()) == []
    assert registry.existing_output_names(grant, [name]) == []


@pytest.mark.parametrize("name", ["CONSOLE.png", "nullable.png", "com10.png", "aux_region.png"])
def test_valid_windows_name_lookalikes(tmp_path, name):
    registry = DesktopFiles()
    grant = registry.grant_folder(tmp_path)["grant"]
    registry.write_files(grant, [{"name": name, "data": b"png"}])
    assert (tmp_path / name).read_bytes() == b"png"
    assert registry.existing_output_names(grant, [name]) == [name]


def test_writes_release_registry_lock(tmp_path, monkeypatch):
    registry = DesktopFiles()
    grant = registry.grant_folder(tmp_path)["grant"]
    path_type = type(tmp_path)
    write_bytes = path_type.write_bytes

    def write_unlocked(path, data):
        assert registry._lock.acquire(blocking=False)
        registry._lock.release()
        return write_bytes(path, data)

    monkeypatch.setattr(path_type, "write_bytes", write_unlocked)
    registry.write_files(grant, [{"name": "out.png", "data": b"png"}])
    assert (tmp_path / "out.png").read_bytes() == b"png"


def test_symlink_escape(tmp_path):
    folder = tmp_path / "output"
    folder.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"original")
    (folder / "link.bin").symlink_to(outside)
    registry = DesktopFiles()
    grant = registry.grant_folder(folder)["grant"]
    with pytest.raises(ValueError):
        registry.write_files(grant, [{"name": "valid.bin", "data": b"ok"}, {"name": "link.bin", "data": b"bad"}])
    assert not (folder / "valid.bin").exists()
    assert outside.read_bytes() == b"original"
    assert registry.existing_output_names(grant, ["link.bin"]) == []


def test_file_grant(tmp_path):
    registry = DesktopFiles()
    path = tmp_path / "chosen.bin"
    grant = registry.grant_file(path)["grant"]
    assert registry.existing_output_names(grant, [path.name]) == []
    for files in [[], [{"data": b"a"}, {"data": b"b"}]]:
        with pytest.raises(ValueError):
            registry.write_files(grant, files)
    assert not path.exists()
    registry.write_files(grant, [{"name": "../ignored", "data": b"result"}])
    assert path.read_bytes() == b"result"
    assert registry.existing_output_names(grant, [path.name, path.name]) == [path.name]
    assert registry.existing_output_names(grant, ["other.bin"]) == []


def test_unknown_grants_and_missing_parent(tmp_path):
    registry = DesktopFiles()
    with pytest.raises(PermissionError):
        registry.write_files("unknown", [])
    with pytest.raises(PermissionError):
        registry.existing_output_names("unknown", [])
    for grant in [registry.grant_folder(tmp_path / "missing"), registry.grant_file(tmp_path / "missing" / "file.bin")]:
        with pytest.raises(OSError):
            registry.write_files(grant["grant"], [{"name": "out.bin", "data": b"out"}])
    assert not (tmp_path / "missing").exists()


def test_concurrent_registration(tmp_path):
    registry = DesktopFiles()
    def register(index):
        path = tmp_path / f"{index}.png"
        key = asset_id(registry.register(path))
        assert registry.lookup(key) == path.resolve()
        return key
    with ThreadPoolExecutor(max_workers=8) as pool:
        keys = list(pool.map(register, range(100)))
    assert len(set(keys)) == 100
