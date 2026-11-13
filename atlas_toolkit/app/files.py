"""Registered desktop inputs and dialog-scoped output grants."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import secrets
import threading


def _atlas_page_filenames(atlas_text: str) -> list[str]:
    """Page PNG lines: no colon, ends with `.png` (case-sensitive)."""
    names: list[str] = []
    for raw in atlas_text.splitlines():
        line = raw.strip()
        if ":" in line or not line.endswith(".png"):
            continue
        names.append(line)
    return names


def _page_candidate(atlas_dir: Path, name: str) -> Path | None:
    """Reject unsafe page names before any filesystem lookup."""
    if not name or "\0" in name or ":" in name or name.startswith(("/", "\\")):
        return None
    for pure_path in (PureWindowsPath(name), PurePosixPath(name)):
        if pure_path.is_absolute() or pure_path.drive or pure_path.anchor:
            return None
    if ".." in re.split(r"[/\\]", name):
        return None
    candidate = atlas_dir / name
    if not candidate.resolve().is_relative_to(atlas_dir.resolve()):
        return None
    return candidate


class DesktopFiles:
    """Keep filesystem paths behind opaque asset and grant identifiers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._assets: dict[str, Path] = {}
        self._grants: dict[str, tuple[str, Path]] = {}
        self._atlas_dir: Path | None = None

    @property
    def atlas_dir(self) -> Path | None:
        with self._lock:
            return self._atlas_dir

    def _register(self, path: Path) -> dict[str, str]:
        """Register while the caller holds the lock."""
        identifier = secrets.token_urlsafe(16)
        self._assets[identifier] = path.resolve()
        return {"name": path.name, "url": "asset/" + identifier}

    def register(self, path: Path) -> dict[str, str]:
        with self._lock:
            return self._register(path)

    def lookup(self, id: str) -> Path | None:
        with self._lock:
            return self._assets.get(id)

    def atlas_bundle(self, path: Path) -> dict:
        with self._lock:
            text = path.read_text(encoding="utf-8")
            self._assets.clear()
            self._atlas_dir = path.resolve().parent
            atlas = self._register(path)
            pages = {}
            for name in _atlas_page_filenames(text):
                candidate = _page_candidate(path.parent, name)
                if candidate is not None and candidate.is_file():
                    pages[name] = self._register(candidate)
            skel = path.with_suffix(".skel")
            return {
                "atlas": atlas,
                "pages": pages,
                "skel": self._register(skel) if skel.is_file() else None,
                "dir": str(self._atlas_dir),
            }

    def _grant(self, kind: str, path: Path) -> dict[str, str]:
        with self._lock:
            resolved = path.resolve()
            identifier = secrets.token_urlsafe(16)
            self._grants.clear()
            self._grants[identifier] = (kind, resolved)
            return {"grant": identifier, "path": str(resolved)}

    def grant_folder(self, path: Path) -> dict[str, str]:
        return self._grant("folder", path)

    def grant_file(self, path: Path) -> dict[str, str]:
        return self._grant("file", path)

    def _get_grant(self, grant: str) -> tuple[str, Path]:
        """Look up a grant while the caller holds the lock."""
        if grant not in self._grants:
            raise PermissionError("Unknown write grant")
        return self._grants[grant]

    @staticmethod
    def _folder_target(folder: Path, name: str) -> Path:
        if not isinstance(name, str) or not name:
            raise ValueError("Output name must be a nonempty string")
        if name in (".", ".."):
            raise ValueError("Output name cannot be a dot segment")
        if any(character in name for character in ("/", "\\", "\0")):
            raise ValueError("Output name must be one path segment")
        if ":" in name:
            raise ValueError("Output name cannot contain a colon")
        if name != name.rstrip(". "):
            raise ValueError("Output name cannot end with a dot or space")
        if re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?", name):
            raise ValueError("Output name cannot be a Windows reserved device name")
        candidate = folder / name
        if candidate.resolve().parent != folder.resolve():
            raise ValueError("Output must stay within the granted folder")
        return candidate

    def existing_output_names(self, grant: str, names: list[str]) -> list[str]:
        with self._lock:
            kind, path = self._get_grant(grant)
            if kind == "file":
                return [path.name] if path.exists() and path.name in names else []
            found = []
            for name in names:
                try:
                    candidate = self._folder_target(path, name)
                    if candidate.is_file() or candidate.is_dir():
                        found.append(name)
                except (ValueError, OSError):
                    continue
            return found

    def write_files(self, grant: str, files: list[dict]) -> None:
        with self._lock:
            kind, path = self._get_grant(grant)
            if kind == "file":
                if len(files) != 1:
                    raise ValueError("A file grant requires exactly one file")
                targets = [path]
            else:
                targets = [self._folder_target(path, item["name"]) for item in files]
        # Large writes must not block asset lookups or drop registration on other threads.
        for target, item in zip(targets, files):
            target.write_bytes(bytes(item["data"]))
        with self._lock:
            self._grants.pop(grant, None)
