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
        "region-mesh-mask", "region-name-validation", "repack-nest",
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
