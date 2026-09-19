"""The domain/ -> engine/ import ban (architecture spec §1, promoted to a
package-level rule in rev 6) enforced mechanically rather than by reviewer
attention. domain/packing.py is the module the rule exists for -- it needs
engine/modifier.py's identity/offset helpers and gets them by injection --
but the rule covers every module in the package."""
import ast
import pathlib
import tempfile

DOMAIN_DIR = pathlib.Path(__file__).resolve().parents[2] / "atlas_toolkit" / "domain"


def _imported_modules(path: pathlib.Path) -> list[str]:
    # Every domain/*.py file is a plain submodule of atlas_toolkit.domain
    # (never a package __init__.py), so its own "package" for relative-
    # import resolution is always this one dotted name -- matches
    # importlib._bootstrap._resolve_name's algorithm. Hardcoded rather than
    # derived from `path` because the probe-file tests below feed this
    # function temp files that AREN'T really under atlas_toolkit/domain/ --
    # they're simulating what a domain file's import would resolve to,
    # same as this function has always been used.
    own_package = "atlas_toolkit.domain"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module
            else:
                # Relative import (`from .foo import x`, `from ..engine
                # import x`, `from .. import engine`) -- WITHOUT resolving
                # node.level, this whole branch silently mis-happens: a
                # relative import of the engine package bypasses the guard
                # entirely, since it never produces an "atlas_toolkit.
                # engine..." string. `from .. import engine` is the
                # sharpest case -- node.module is None there, so the old
                # `node.module is not None` guard skipped it outright.
                bits = own_package.rsplit(".", node.level - 1)
                base = bits[0] if len(bits) >= node.level else None
                if base is not None and node.module:
                    base = f"{base}.{node.module}"
            if base is None:
                continue
            names.append(base)
            # `from atlas_toolkit import engine` (or its relative
            # equivalents) reports a base of "atlas_toolkit" with name
            # "engine": the banned package is visible only by joining the
            # two. Without this the guard passes while the import really
            # does bind the engine subpackage (verified -- the module
            # object is genuinely reachable).
            names.extend(f"{base}.{alias.name}" for alias in node.names)
    return names


def _is_engine(name: str) -> bool:
    """Exact package match or a dotted descendant -- never a bare prefix, so
    a future sibling like `atlas_toolkit.engine_helpers` is not flagged."""
    return name == "atlas_toolkit.engine" or name.startswith("atlas_toolkit.engine.")


def test_domain_modules_never_import_engine():
    offenders = {}
    for path in sorted(DOMAIN_DIR.glob("*.py")):
        bad = [name for name in _imported_modules(path) if _is_engine(name)]
        if bad:
            offenders[path.name] = bad
    assert offenders == {}


def test_the_guard_actually_inspects_packing():
    # Without this, a renamed/moved packing.py would leave the test above
    # passing vacuously.
    assert (DOMAIN_DIR / "packing.py").exists()
    assert "atlas_toolkit.domain.region_ops" in _imported_modules(DOMAIN_DIR / "packing.py")


def test_guard_catches_every_import_form():
    # A guard nobody proved is worth nothing. Every statement below really
    # does import the banned subpackage from a file living in
    # atlas_toolkit/domain/ (own_package's assumed location). The last two
    # are RELATIVE forms -- found missing by a whole-branch scrutinize
    # review (2026-09-19): `from .. import engine` has node.module is None,
    # which the old guard's `node.module is not None` check skipped
    # entirely, and neither relative form ever produced an
    # "atlas_toolkit.engine..." string under the old absolute-only logic.
    forms = [
        "import atlas_toolkit.engine.modifier",
        "from atlas_toolkit.engine.modifier import canvas_size_match",
        "from atlas_toolkit import engine",
        "from ..engine import modifier",
        "from .. import engine",
    ]
    for source in forms:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
            handle.write(source + "\n")
            probe = pathlib.Path(handle.name)
        try:
            assert any(_is_engine(name) for name in _imported_modules(probe)), source
        finally:
            probe.unlink()


def test_guard_does_not_flag_a_similarly_named_sibling():
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
        handle.write("from atlas_toolkit import engine_helpers\n")
        probe = pathlib.Path(handle.name)
    try:
        assert not any(_is_engine(name) for name in _imported_modules(probe))
    finally:
        probe.unlink()


def test_guard_does_not_flag_a_legitimate_relative_sibling_import():
    # `from . import region_ops` / `from ..raster import base` are real,
    # legitimate imports every domain/*.py file already uses -- the
    # relative-import resolution added for the finding above must not
    # start false-flagging these.
    for source in ["from . import region_ops", "from ..raster import base"]:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
            handle.write(source + "\n")
            probe = pathlib.Path(handle.name)
        try:
            assert not any(_is_engine(name) for name in _imported_modules(probe)), source
        finally:
            probe.unlink()
