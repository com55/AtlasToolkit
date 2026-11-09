"""Core modules must not depend on desktop UI or network packages."""
import ast
from importlib.util import resolve_name
from pathlib import Path
import subprocess
import sys

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "atlas_toolkit"


def _imported_modules(source, own_package):
    names = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = resolve_name("." * node.level + base, own_package)
            names.append(base)
            names.extend(f"{base}.{alias.name}" for alias in node.names)
    return names


def _forbidden(name):
    return name.split(".")[0] in {"requests", "pywebview", "webview"}


def test_core_modules_do_not_import_desktop_or_network_packages():
    offenders = {}
    inspected = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE)
        if relative.parts[0] in {"app", "update"}:
            continue
        inspected.append(relative.as_posix())
        package = ".".join(("atlas_toolkit", *relative.parts[:-1]))
        bad = [name for name in _imported_modules(path.read_text(encoding="utf-8"), package) if _forbidden(name)]
        if bad:
            offenders[relative.as_posix()] = bad
    assert "api.py" in inspected and "__init__.py" in inspected
    assert "engine/session.py" in inspected
    assert offenders == {}


@pytest.mark.parametrize("source,package,expected", [
    ("import requests.sessions", "atlas_toolkit.domain", "requests.sessions"),
    ("from requests.sessions import Session", "atlas_toolkit.domain", "requests.sessions.Session"),
    ("from requests import sessions", "atlas_toolkit.domain", "requests.sessions"),
    ("from ..engine import session", "atlas_toolkit.domain", "atlas_toolkit.engine.session"),
    ("from .. import engine", "atlas_toolkit.domain", "atlas_toolkit.engine"),
    ("from . import session", "atlas_toolkit.engine", "atlas_toolkit.engine.session"),
    ("from ... import api", "atlas_toolkit.engine.nested", "atlas_toolkit.api"),
    ("from . import api", "atlas_toolkit", "atlas_toolkit.api"),
])
def test_import_guard_resolves_every_import_form(source, package, expected):
    assert expected in _imported_modules(source, package)


@pytest.mark.parametrize("name", ["requests", "requests.sessions", "pywebview", "webview.window"])
def test_import_guard_recognizes_forbidden_packages(name):
    assert _forbidden(name)
    assert not _forbidden(name + "_helpers") if "." not in name else True


def test_package_and_version_import_without_requests():
    code = "import sys; sys.modules['requests'] = None; sys.modules['webview'] = None; import atlas_toolkit; assert atlas_toolkit.__version__; atlas_toolkit.SpineAtlas"
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
