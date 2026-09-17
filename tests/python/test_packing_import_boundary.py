"""The domain/ -> engine/ import ban (architecture spec §1, promoted to a
package-level rule in rev 6) enforced mechanically rather than by reviewer
attention. domain/packing.py is the module the rule exists for -- it needs
engine/modifier.py's identity/offset helpers and gets them by injection --
but the rule covers every module in the package."""
import ast
import pathlib

DOMAIN_DIR = pathlib.Path(__file__).resolve().parents[2] / "atlas_toolkit" / "domain"


def _imported_modules(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.append(node.module)
    return names


def test_domain_modules_never_import_engine():
    offenders = {}
    for path in sorted(DOMAIN_DIR.glob("*.py")):
        bad = [name for name in _imported_modules(path) if name.startswith("atlas_toolkit.engine")]
        if bad:
            offenders[path.name] = bad
    assert offenders == {}


def test_the_guard_actually_inspects_packing():
    # Without this, a renamed/moved packing.py would leave the test above
    # passing vacuously.
    assert (DOMAIN_DIR / "packing.py").exists()
    assert "atlas_toolkit.domain.region_ops" in _imported_modules(DOMAIN_DIR / "packing.py")
