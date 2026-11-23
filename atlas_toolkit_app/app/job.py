# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Desktop job mode: another program starts the app with `--return <dir>`,
the user edits the files it gave, and Save & Return writes the outputs and a
`result.json` into that folder before the app closes."""

from __future__ import annotations

import json
import os
from pathlib import Path

PROTOCOL = 1
HOST_NAME_LIMIT = 40


class ReturnJob:
    def __init__(self, dir: Path, host_name: str, files: list[Path]) -> None:
        self.dir = Path(dir)
        self.host_name = str(host_name).strip()[:HOST_NAME_LIMIT]
        self.files = [Path(p) for p in files]
        self._status = "cancelled"
        self._names: list[str] = []
        self._message: str | None = None
        self._code: int | None = None

    def clear_previous(self) -> None:
        """Remove a result.json left by an earlier run, so a crash never reads as its result."""
        try:
            (self.dir / "result.json").unlink()
        except OSError:  # missing, or a folder we cannot write: write_result reports that
            pass

    def record_saved(self, names: list[str]) -> None:
        self._status, self._names, self._message = "saved", [str(n) for n in names], None

    def record_error(self, message: str) -> None:
        self._status, self._names, self._message = "error", [], str(message)

    def write_result(self) -> int:
        """Write result.json once (temp file + rename, so a reader never sees
        half of it) and return the exit code: 0 for saved, else 1."""
        if self._code is not None:
            return self._code
        code = 0 if self._status == "saved" else 1
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = {"protocol": PROTOCOL, "status": self._status, "files": self._names, "message": self._message}
        temporary = self.dir / "result.json.tmp"
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, self.dir / "result.json")
        self._code = code
        return code


def capabilities(version: str) -> dict:
    """What a calling program can rely on in this build (`--capabilities <file>`)."""
    return {"version": version, "host": {"protocol": PROTOCOL, "return": True}}


def write_capabilities(path: Path, version: str) -> None:
    """Write capabilities() as JSON to `path` (temp file + rename). The exe has
    no console, so a caller reads this file instead of stdout."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(capabilities(version), indent=2), encoding="utf-8")
    os.replace(temporary, path)


def parse_job_args(argv: list[str]) -> tuple[list[str], ReturnJob | None]:
    """Take `--return <dir>` and `--host-name <name>` out of `argv` (without
    the program name). The job's files are the remaining non-option args."""
    rest: list[str] = []
    return_dir: str | None = None
    host_name = ""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--return", "--host-name") and i + 1 < len(argv):
            if arg == "--return":
                return_dir = argv[i + 1]
            else:
                host_name = argv[i + 1]
            i += 2
            continue
        rest.append(arg)
        i += 1
    if return_dir is None:
        return rest, None
    return rest, ReturnJob(Path(return_dir), host_name, [Path(a) for a in rest if not a.startswith("--")])
