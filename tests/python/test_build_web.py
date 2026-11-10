"""Offline checks for the web build's file selection and import traversal."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from build_web import excluded, import_offenders


def test_excluded_engine_files():
    names = [
        "atlas-document", "atlas-session", "atlas-modifier", "atlas-extracter",
        "atlas-converter", "atlas-api-legacy", "core-region-ops",
        "effective-region-model", "lanczos-resize", "region-mesh-lookup",
        "region-mesh-mask", "repack-nest",
        "job-runner", "preview-jobs", "canvas-surface",
    ]
    for name in names:
        assert excluded(f"js/{name}.js"), name
    for path in ("js/vendor/x.js", "js/vendor/nested/x.js",
                 "workers/engine-job-worker.js", "workers/preview-job-worker.js"):
        assert excluded(path), path


def test_retained_ui_files():
    for path in ("js/atlas-api.js", "js/preview.js", "workers/engine-host-worker.js",
                 "script.js", "style.css", "js/vendor-other.js"):
        assert not excluded(path), path


def test_import_offenders_follows_transitive_imports(tmp_path):
    (tmp_path / "script.js").write_text("import { a } from './a.js';")
    (tmp_path / "a.js").write_text("import('./b.js');")
    assert import_offenders(tmp_path) == ["b.js"]


def test_import_offenders_resolves_both_entries_and_deduplicates(tmp_path):
    (tmp_path / "workers").mkdir()
    (tmp_path / "js").mkdir()
    (tmp_path / "script.js").write_text(
        "import './js/a.js'; import './z.js'; import 'bare'; import('vendor');"
    )
    (tmp_path / "js/a.js").write_text(
        "import '../script.js'; import { x } from '../missing.js';"
    )
    (tmp_path / "workers/engine-host-worker.js").write_text(
        "import('../missing.js'); import './worker-missing.js';"
    )
    assert import_offenders(tmp_path) == [
        "missing.js", "workers/worker-missing.js", "z.js",
    ]


def test_import_offenders_skips_missing_entries_and_unreachable_files(tmp_path):
    (tmp_path / "unused.js").write_text("import './missing.js';")
    assert import_offenders(tmp_path) == []
    (tmp_path / "workers").mkdir()
    (tmp_path / "workers/engine-host-worker.js").write_text("import '../missing.js';")
    assert import_offenders(tmp_path) == ["missing.js"]


# The download tests replace urlretrieve; no request reaches the network.
import hashlib
import json

import pytest
import build_web


def fake_runtime(cache, payload=b"correct wheel"):
    cache.mkdir(parents=True, exist_ok=True)
    for name in ("pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip"):
        (cache / name).write_bytes(b"runtime")
    packages = {
        name: {"file_name": f"{name}.whl", "sha256": hashlib.sha256(payload).hexdigest()}
        for name in ("numpy", "pillow")
    }
    (cache / "pyodide-lock.json").write_text(json.dumps({"packages": packages}))
    return packages


def test_failed_download_leaves_no_final_file(tmp_path, monkeypatch):
    final = tmp_path / "runtime.bin"

    def interrupted(url, target):
        assert Path(target) == final.with_name(final.name + ".part")
        Path(target).write_bytes(b"partial")
        raise OSError("interrupted")

    monkeypatch.setattr(build_web, "urlretrieve", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        build_web.download("unused", final)
    assert not final.exists()

    def succeeds(url, target):
        Path(target).write_bytes(b"complete")

    monkeypatch.setattr(build_web, "urlretrieve", succeeds)
    build_web.download("unused", final)
    assert final.read_bytes() == b"complete"
    assert not final.with_name(final.name + ".part").exists()


def test_bad_cached_wheel_is_fetched_again(tmp_path, monkeypatch):
    packages = fake_runtime(tmp_path)
    for name in packages:
        (tmp_path / f"{name}.whl").write_bytes(b"wrong")
    fetched = []

    def succeeds(url, target):
        fetched.append(url)
        Path(target).write_bytes(b"correct wheel")

    monkeypatch.setattr(build_web, "urlretrieve", succeeds)
    build_web.download_runtime(tmp_path)
    assert fetched == [build_web.PYODIDE_URL + f"{name}.whl" for name in packages]
    for name, entry in packages.items():
        assert hashlib.sha256((tmp_path / f"{name}.whl").read_bytes()).hexdigest() == entry["sha256"]


def test_wheel_that_stays_bad_raises_and_is_removed(tmp_path, monkeypatch):
    fake_runtime(tmp_path)
    wheel = tmp_path / "numpy.whl"
    wheel.write_bytes(b"wrong cached bytes")
    fetched = []

    def stays_bad(url, target):
        fetched.append(url)
        Path(target).write_bytes(b"wrong downloaded bytes")

    monkeypatch.setattr(build_web, "urlretrieve", stays_bad)
    with pytest.raises(ValueError, match="SHA256 mismatch: numpy.whl"):
        build_web.download_runtime(tmp_path)
    assert fetched == [build_web.PYODIDE_URL + "numpy.whl"]
    assert not wheel.exists()


def test_prepare_output_refuses_a_directory_with_other_content(tmp_path):
    valuable = tmp_path / "keep.txt"
    valuable.write_text("keep")
    with pytest.raises(ValueError, match="does not look like a build output") as error:
        build_web.prepare_output(tmp_path)
    assert str(tmp_path) in str(error.value)
    assert valuable.read_text() == "keep"


def test_prepare_output_replaces_an_earlier_build(tmp_path):
    (tmp_path / "wheels").mkdir()
    (tmp_path / "wheels/manifest.json").write_text("{}")
    (tmp_path / "old.js").write_text("old")
    build_web.prepare_output(tmp_path)
    assert not tmp_path.exists()


def test_prepare_output_accepts_missing_and_empty(tmp_path):
    build_web.prepare_output(tmp_path / "missing")
    assert not (tmp_path / "missing").exists()
    build_web.prepare_output(tmp_path)
    assert tmp_path.is_dir()
    assert list(tmp_path.iterdir()) == []


def test_main_prepares_output_before_downloads_and_versions_runtime(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(build_web, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["build_web.py"])
    monkeypatch.setattr(build_web, "prepare_output", lambda path: calls.append(("prepare", path)))
    monkeypatch.setattr(build_web, "download_runtime", lambda path: calls.append(("download", path)) or [])
    monkeypatch.setattr(build_web, "build_project_wheels", lambda path: {})
    monkeypatch.setattr(build_web, "assemble_site", lambda *args: None)
    assert build_web.main() == 0
    assert build_web.PYODIDE_VERSION == "314.0.7"
    assert build_web.PYODIDE_URL == f"https://cdn.jsdelivr.net/pyodide/v{build_web.PYODIDE_VERSION}/full/"
    assert calls == [
        ("prepare", tmp_path / "dist/web"),
        ("download", tmp_path / "dist/.cache/pyodide" / build_web.PYODIDE_VERSION),
    ]


def test_main_rejects_unsafe_output_before_downloading(tmp_path, monkeypatch):
    (tmp_path / "keep.txt").write_text("keep")
    monkeypatch.setattr(sys, "argv", ["build_web.py", "--out", str(tmp_path)])

    def unexpected_download(*args):
        pytest.fail("Download started before output validation")

    monkeypatch.setattr(build_web, "download_runtime", unexpected_download)
    with pytest.raises(SystemExit) as error:
        build_web.main()
    assert error.value.code == 2
    assert (tmp_path / "keep.txt").read_text() == "keep"


def test_parser_cache_is_keyed_by_tag(tmp_path, monkeypatch):
    source = tmp_path / f"skel-mesh-parser-{build_web.PARSER_TAG}"
    source.mkdir()
    built = []

    def fake_build(project, output):
        built.append(project)
        return output / "test.whl"

    monkeypatch.setattr(build_web, "build_wheel", fake_build)
    monkeypatch.setattr(build_web.subprocess, "run", lambda *args, **kwargs: None)
    build_web.build_project_wheels(tmp_path)
    assert built == [build_web.ROOT, source]
