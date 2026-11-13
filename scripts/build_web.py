"""Build the web site (Pyodide) or the desktop site (no bundler required)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.request import urlretrieve

from web_version import load_version, stamp_index_html

ROOT = Path(__file__).resolve().parents[1]
PYODIDE_VERSION = "314.0.7"
PYODIDE_URL = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
PARSER_URL = "https://github.com/com55/skel-mesh-parser.git"
PARSER_TAG = "py-v0.3.0"
EXCLUDED = {
    f"js/{name}.js" for name in (
        "atlas-document", "atlas-session", "atlas-modifier", "atlas-extracter",
        "atlas-converter", "atlas-api-legacy", "core-region-ops",
        "effective-region-model", "lanczos-resize", "region-mesh-lookup",
        "region-mesh-mask", "repack-nest", "region-name-validation",
        "job-runner", "preview-jobs", "canvas-surface",
    )
} | {"workers/engine-job-worker.js", "workers/preview-job-worker.js"}
IMPORT_RE = re.compile(
    r"\b(?:export\s+[^;\"\']*?\bfrom\s*|import\s*(?:\(\s*|(?:[^;'\"()]*?\bfrom\s*)?))[\"']([^\"']+)[\"']"
)


def excluded(rel_path: str) -> bool:
    """Whether a www-relative file belongs to the retired JS engine."""
    return rel_path in EXCLUDED or rel_path.startswith("js/vendor/")


def import_offenders(site: Path) -> list[str]:
    """Find missing relative imports reachable from the two site entries."""
    site = site.resolve()
    pending = [site / name for name in ("script.js", "workers/engine-host-worker.js")
               if (site / name).is_file()]
    visited = set()
    missing = set()
    while pending:
        source = pending.pop()
        if source in visited:
            continue
        visited.add(source)
        for specifier in IMPORT_RE.findall(source.read_text(encoding="utf-8")):
            if not specifier.startswith(("./", "../")):
                continue
            target = (source.parent / specifier).resolve()
            relative = Path(os.path.relpath(target, site)).as_posix()
            if not target.is_relative_to(site) or not target.is_file():
                missing.add(relative)
            else:
                pending.append(target)
    return sorted(missing)


def download(url: str, path: Path) -> None:
    """Publish a download only after the fetch completes."""
    partial = path.with_name(path.name + ".part")
    urlretrieve(url, partial)
    os.replace(partial, path)


def download_runtime(cache: Path) -> list[Path]:
    """Cache the pinned runtime and verify both package wheels on every build."""
    cache.mkdir(parents=True, exist_ok=True)
    files = []
    for name in ("pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm",
                 "python_stdlib.zip", "pyodide-lock.json"):
        path = cache / name
        if not path.is_file():
            download(PYODIDE_URL + name, path)
        files.append(path)
    lock = json.loads((cache / "pyodide-lock.json").read_text(encoding="utf-8"))
    for package in ("numpy", "pillow"):
        entry = lock["packages"][package]
        path = cache / entry["file_name"]
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            path.unlink()
        if not path.is_file():
            download(PYODIDE_URL + entry["file_name"], path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            path.unlink()
            raise ValueError(f"SHA256 mismatch: {path.name}")
        files.append(path)
    return files


def build_wheel(source: Path, output: Path) -> Path:
    """Build one project wheel into its dedicated cache directory."""
    output.mkdir(parents=True, exist_ok=True)
    for wheel in output.glob("*.whl"):
        wheel.unlink()
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(output)],
                   cwd=source, check=True)
    wheels = list(output.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("Expected exactly one built wheel")
    return wheels[0]


def build_project_wheels(cache: Path) -> dict[str, Path]:
    """Build the local engine and the shallow-cloned, pinned parser."""
    parser_source = cache / f"skel-mesh-parser-{PARSER_TAG}"
    if not parser_source.exists():
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", PARSER_TAG,
             PARSER_URL, str(parser_source)], check=True,
        )
    return {
        "atlas_toolkit": build_wheel(ROOT, cache / "atlas-wheel"),
        "skel_mesh_parser": build_wheel(parser_source, cache / "parser-wheel"),
    }


def prepare_output(out: Path) -> None:
    """Remove only a directory marked as an earlier site build."""
    if not out.exists() or (out.is_dir() and not any(out.iterdir())):
        return
    if ((out / "wheels/manifest.json").is_file()
            or (out / "desktop-build.json").is_file()):
        shutil.rmtree(out)
        return
    raise ValueError(f"{out} does not look like a build output")


def generate_sw(site: Path, template: str) -> str:
    """Fill the worker template with a deterministic inventory and content ID."""
    paths = sorted(path.relative_to(site).as_posix()
                   for path in site.rglob("*")
                   if path.is_file() and path.relative_to(site).as_posix() != "sw.js")
    pairs = [(name, hashlib.sha256((site / name).read_bytes()).hexdigest())
             for name in paths]
    digest = hashlib.sha256(json.dumps(pairs, separators=(",", ":")).encode()).hexdigest()[:12]
    return (template.replace("__CACHE_NAME__", json.dumps("atlas-toolkit-py-" + digest))
            .replace("__CORE_ASSETS__", json.dumps(sorted([".", *paths]), indent=2)))


def _copy_shell(out: Path, skip: frozenset[str]) -> None:
    """Copy shell files except retired engine files and target-specific skips."""
    for source in (ROOT / "www").rglob("*"):
        relative = source.relative_to(ROOT / "www")
        if not source.is_file() or excluded(relative.as_posix()) or relative.as_posix() in skip:
            continue
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def assemble_site(out: Path, runtime: list[Path], wheels: dict[str, Path]) -> None:
    """Copy dependencies, stamp the shell, then generate the offline worker."""
    _copy_shell(out, frozenset({"sw.js"}))
    for directory, paths in (("pyodide", runtime), ("wheels", wheels.values())):
        destination = out / directory
        destination.mkdir(parents=True, exist_ok=True)
        for path in paths:
            shutil.copy2(path, destination / path.name)
    (out / "wheels/manifest.json").write_text(
        json.dumps({key: path.name for key, path in wheels.items()}, indent=2) + "\n",
        encoding="utf-8",
    )

    index = out / "index.html"
    index.write_text(stamp_index_html(index.read_text(encoding="utf-8"), load_version(ROOT)),
                     encoding="utf-8")
    (out / "sw.js").write_text(
        generate_sw(out, (ROOT / "www/sw.js").read_text(encoding="utf-8")),
        encoding="utf-8",
    )


def assemble_desktop(out: Path) -> None:
    """Copy and stamp the desktop shell without browser engine dependencies."""
    _copy_shell(out, frozenset({"sw.js", "workers/engine-host-worker.js",
                               "workers/engine-transfer.js"}))
    index = out / "index.html"
    index.write_text(stamp_index_html(index.read_text(encoding="utf-8"), load_version(ROOT)),
                     encoding="utf-8")
    (out / "desktop-build.json").write_text(
        json.dumps({"target": "desktop"}) + "\n", encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("web", "desktop"), default="web")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--strict", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    out = (ROOT / (args.out or Path("dist") / args.target)).resolve()
    cache = ROOT / "dist/.cache"
    # Assembly replaces its output directory; never let it erase its inputs.
    if any(path.is_relative_to(out) or out.is_relative_to(path)
           for path in (ROOT / "www", cache)) or ROOT.is_relative_to(out):
        parser.error("--out must be separate from the source tree and download cache")
    try:
        prepare_output(out)
    except ValueError as error:
        parser.error(str(error))
    if args.target == "desktop":
        assemble_desktop(out)
    else:
        runtime = download_runtime(cache / "pyodide" / PYODIDE_VERSION)
        wheels = build_project_wheels(cache)
        assemble_site(out, runtime, wheels)
    offenders = import_offenders(out)
    if offenders:
        print("Missing relative imports:")
        for path in offenders:
            print(f"  {path}")
    print(f"Built site: {out}")
    return int(args.strict and bool(offenders))


if __name__ == "__main__":
    raise SystemExit(main())
