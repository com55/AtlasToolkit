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
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.append(node.module)
            # `from atlas_toolkit import engine` reports module
            # "atlas_toolkit" with name "engine": the banned package is
            # visible only by joining the two. Without this the guard passes
            # while the import really does bind the engine subpackage
            # (verified -- the module object is genuinely reachable).
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
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
    # A guard nobody proved is worth nothing. All three statements below
    # really do import the banned subpackage; the third is the one a
    # module-name-only check silently misses.
    forms = [
        "import atlas_toolkit.engine.modifier",
        "from atlas_toolkit.engine.modifier import canvas_size_match",
        "from atlas_toolkit import engine",
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
