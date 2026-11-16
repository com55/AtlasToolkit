# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Self-update download and silent install orchestration."""

from __future__ import annotations

import json
import logging
import os
import sys
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

from atlas_toolkit.app.config import get_config_dir
from atlas_toolkit.update.updater import (
    check_for_updates,
    download_update_asset,
    find_windows_installer_asset,
    get_latest_release_info,
    is_running_as_exe,
)

log = logging.getLogger(__name__)

# Inno closes the running app via Restart Manager; relaunch argv is handled by a watcher script.
_INNO_SILENT_INSTALL_ARGS = (
    "/SILENT /SUPPRESSMSGBOXES /NORESTART "
    "/CLOSEAPPLICATIONS /FORCECLOSEAPPLICATIONS /NORESTARTAPPLICATIONS"
)
_SETUP_EXE_BASENAME = "AtlasToolkit-Setup-x64"
RESULT_FILE_NAME = "update_result.json"
# How long the app keeps the installer handle while the relaunch watcher starts.
_HANDOFF_TIMEOUT_SECONDS = 15.0

# Inno Setup exit codes (jrsoftware.org/ishelp, "Setup Exit Codes").
_INSTALLER_EXIT_MESSAGES = {
    1: "The installer could not start. The file may be damaged or incomplete.",
    2: "The update was cancelled before it started.",
    3: "The installer hit an error while preparing to install.",
    4: "The installer hit an error while installing.",
    5: "The update was cancelled during installation.",
    7: "The installer could not continue. Close other copies of AtlasToolkit and try again.",
    8: "The installer could not continue until Windows is restarted.",
}


def installer_failure_message(exit_code: int) -> str:
    base = _INSTALLER_EXIT_MESSAGES.get(exit_code, "The installer stopped before finishing.")
    return f"{base} (installer exit code {exit_code})"


def read_install_result(update_dir: Path) -> Optional[dict[str, str]]:
    """Consume the watcher's failure record; the relaunch flag only says it exists."""
    path = update_dir / RESULT_FILE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    if not isinstance(data, dict) or not isinstance(data.get("exit_code"), int):
        return None
    return {
        "message": installer_failure_message(data["exit_code"]),
        "logPath": str(data.get("log_path") or ""),
        "releaseUrl": str(data.get("release_url") or ""),
    }


def get_update_dir() -> Path:
    d = get_config_dir() / "update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_nuitka_onefile_parent_exe_path() -> Optional[Path]:
    if os.name != "nt":
        return None

    raw_pid = os.environ.get("NUITKA_ONEFILE_PARENT", "").strip()
    if not raw_pid:
        return None

    try:
        pid = int(raw_pid)
    except Exception:
        return None

    if pid <= 0:
        return None

    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32

        process_handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not process_handle:
            return None

        try:
            size = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            ok = kernel32.QueryFullProcessImageNameW(
                process_handle, 0, buf, ctypes.byref(size),
            )
            if not ok:
                return None
            p = Path(buf.value)
            if p.exists() and p.is_file():
                return p
            return None
        finally:
            kernel32.CloseHandle(process_handle)
    except Exception:
        return None


def get_running_executable_path() -> Path:
    if sys.argv and sys.argv[0]:
        try:
            argv0_path = Path(os.path.abspath(sys.argv[0]))
            if argv0_path.exists() and argv0_path.is_file():
                return argv0_path.resolve()
        except Exception:
            pass

    candidates: list[Path] = []
    onefile_parent = get_nuitka_onefile_parent_exe_path()
    if onefile_parent is not None:
        candidates.append(onefile_parent)

    for raw in ([sys.argv[0]] if sys.argv else []) + [sys.executable]:
        if not raw:
            continue
        try:
            p = Path(raw).expanduser()
        except Exception:
            continue
        if not p.is_absolute():
            p = Path.cwd() / p
        candidates.append(p)

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if resolved.exists() and resolved.is_file():
            return resolved

    if candidates:
        try:
            return candidates[0].resolve()
        except Exception:
            return candidates[0]

    return Path(sys.executable).resolve()


def install_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return (base / "AtlasToolkit").resolve()


def is_installed_build() -> bool:
    if not is_running_as_exe():
        return False
    try:
        exe = get_running_executable_path().resolve()
        target = install_dir()
        return exe.parent == target or target in exe.parents
    except Exception:
        return False


def get_relaunch_executable_path() -> Path:
    if is_installed_build():
        installed = install_dir() / "AtlasToolkit.exe"
        if installed.exists():
            return installed.resolve()
    return get_running_executable_path()


def _ps_single_quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _unblock_downloaded_file(path: Path) -> None:
    """Remove Mark-of-the-Web so Windows does not prompt when the app launches the installer."""
    zone_stream = Path(f"{path}:Zone.Identifier")
    try:
        zone_stream.unlink(missing_ok=True)
    except OSError:
        pass


def _inno_install_params(inno_log_path: Path) -> str:
    return f'{_INNO_SILENT_INSTALL_ARGS} /LOG="{inno_log_path}"'


def build_relaunch_watcher_ps1(
    pending_json_path: Path, installer_pid: int, claim_path: Path,
    result_path: Path, inno_log_path: Path,
) -> str:
    """Wait for the installer by PID, then relaunch the app and report a failure.

    The app holds the installer's process handle until a claim file exists,
    so the PID cannot be reused before this script opens its own handle.
    Whoever creates the claim file first (this script, or the app when the
    installer exits early) owns reporting the result."""
    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$Meta = Get-Content -LiteralPath {_ps_single_quoted(str(pending_json_path))} -Raw | ConvertFrom-Json",
        f"$Claim = {_ps_single_quoted(str(claim_path))}",
        f"$Result = {_ps_single_quoted(str(result_path))}",
        f"$LogPath = {_ps_single_quoted(str(inno_log_path))}",
        "try {",
        f"    $Setup = Get-Process -Id {int(installer_pid)}",
        "    $null = $Setup.Handle",
        "} catch { exit 0 }",
        "try { [System.IO.File]::Open($Claim, 'CreateNew', 'Write').Dispose() } catch { exit 0 }",
        "$Setup.WaitForExit()",
        "$Code = $Setup.ExitCode",
        "if ($Code -eq 0) {",
        "    $Flag = '--update-installed'",
        "} else {",
        "    $Info = @{ exit_code = $Code; release_url = [string]$Meta.release_url }",
        "    if (Test-Path -LiteralPath $LogPath) { $Info.log_path = $LogPath }",
        "    $Info | ConvertTo-Json | Set-Content -LiteralPath $Result -Encoding UTF8",
        "    $Flag = '--update-install-failed'",
        "}",
        # One pre-quoted string: Start-Process joins array elements without quoting them.
        "$ArgLine = ([string]$Meta.relaunch_command_line + ' ' + $Flag).Trim()",
        "Start-Process -FilePath $Meta.target_exe_path -ArgumentList $ArgLine",
        "Remove-Item -LiteralPath $Claim -Force -ErrorAction SilentlyContinue",
        "Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue",
    ]
    return "\r\n".join(lines) + "\r\n"


class _InstallerProcess:
    """A launched installer's process handle (Windows)."""

    def __init__(self, handle: int, pid: int) -> None:
        self.handle = handle
        self.pid = pid

    def exit_code(self) -> Optional[int]:
        """None while running, otherwise the exit code."""
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if kernel32.WaitForSingleObject(wintypes.HANDLE(self.handle), 0) != 0:  # WAIT_OBJECT_0
            return None
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(wintypes.HANDLE(self.handle), ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(code.value)

    def close(self) -> None:
        import ctypes
        from ctypes import wintypes

        if self.handle:
            ctypes.WinDLL("kernel32").CloseHandle(wintypes.HANDLE(self.handle))
            self.handle = 0


def _launch_process(file: str, params: str, work_dir: str, show: int = 1) -> _InstallerProcess:
    """ShellExecuteEx with a process handle, so the app can follow the installer."""
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
        ]

    SEE_MASK_NOCLOSEPROCESS, SEE_MASK_NOASYNC, SEE_MASK_FLAG_NO_UI = 0x40, 0x100, 0x400
    info = SHELLEXECUTEINFOW(
        cbSize=ctypes.sizeof(SHELLEXECUTEINFOW),
        fMask=SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC | SEE_MASK_FLAG_NO_UI,
        lpVerb="open", lpFile=file, lpParameters=params, lpDirectory=work_dir, nShow=show,
    )
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not info.hProcess:
        raise OSError("Windows did not return a process for the installer")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetProcessId.argtypes = [wintypes.HANDLE]
    kernel32.GetProcessId.restype = wintypes.DWORD
    return _InstallerProcess(int(info.hProcess), int(kernel32.GetProcessId(info.hProcess)))


def _installer_check(path: Path) -> Optional[str]:
    """A fixed message for a file that is not a Windows program at all."""
    try:
        with open(path, "rb") as handle:
            if handle.read(2) == b"MZ":
                return None
    except OSError as e:
        return f"Could not read the installer: {e.strerror or e}"
    return "This file is not a Windows program, so it cannot be an AtlasToolkit installer."


def _launch_error_message(error: OSError) -> str:
    detail = (error.strerror or str(error)).strip().replace("%1", "the file")
    code = getattr(error, "winerror", None) or error.errno
    suffix = f" (Windows: {detail}, code {code})" if code else f" ({detail})"
    return "Windows could not start this installer. It may be damaged or not an AtlasToolkit installer." + suffix


def _claim(path: Path) -> bool:
    """Create the claim file; False when the watcher created it first."""
    try:
        with open(path, "x"):
            return True
    except FileExistsError:
        return False


def _await_handoff(process, claim_path: Path, timeout: float) -> tuple[str, Optional[int]]:
    """Return ("watcher", None) once the watcher owns the installer, else
    ("app", exit_code) — exit_code is None if the installer is still running."""
    deadline = time.monotonic() + timeout
    while True:
        if claim_path.exists():
            return "watcher", None
        code = process.exit_code()
        if code is not None or time.monotonic() >= deadline:
            return ("app", code) if _claim(claim_path) else ("watcher", None)
        time.sleep(0.1)


def _shell_execute(file: str, params: str, work_dir: str, show: int = 0) -> None:
    """Launch a process via ShellExecute (independent of our process tree)."""
    import ctypes

    result = ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
        None,
        "open",
        file,
        params,
        work_dir,
        show,  # SW_HIDE by default; SW_SHOWNORMAL for the installer
    )
    if result <= 32:
        raise OSError(f"ShellExecuteW failed with code {result}")


class UpdateController:
    """Manages update check, download, and install relaunch."""

    def __init__(self, pending_failure: Optional[dict[str, str]] = None) -> None:
        self.pending_failure = pending_failure
        self._installer_path: Optional[Path] = None
        self._relaunch_args: list[str] = []
        self._version: Optional[str] = None
        self._release_url: Optional[str] = None
        self._ready = False
        self._lock = threading.Lock()
        self._progress: dict[str, Any] = {
            "status": "idle",
            "downloaded_bytes": 0,
            "total_bytes": None,
            "percent": 0,
            "error": None,
        }

    def get_progress(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._progress)

    def _set_progress(
        self,
        *,
        status: str,
        downloaded_bytes: int,
        total_bytes: Optional[int],
        percent: int,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._progress = {
                "status": status,
                "downloaded_bytes": downloaded_bytes,
                "total_bytes": total_bytes,
                "percent": percent,
                "error": error,
            }

    def check_for_notification(self) -> Optional[dict[str, Any]]:
        info = check_for_updates()
        if not info:
            return None

        if is_installed_build():
            action = "download"
            source_tree_url = info.source_tree_url
        elif is_running_as_exe():
            action = "open_source_tag"
            source_tree_url = info.release_url
        else:
            action = "open_source_tag"
            source_tree_url = info.source_tree_url

        return {
            "latestVersion": info.latest_version,
            "releaseName": info.release_name,
            "releaseUrl": info.release_url,
            "tagName": info.tag_name,
            "sourceTreeUrl": source_tree_url,
            "action": action,
        }

    def download(self) -> dict[str, Any]:
        if not is_running_as_exe():
            return {
                "ok": False,
                "error": "Dev mode does not support self-update install flow.",
            }
        if not is_installed_build():
            return {
                "ok": False,
                "error": "Portable build does not support silent self-update. Use the releases page.",
            }

        self._installer_path = None
        self._relaunch_args = []
        self._version = None
        self._release_url = None
        self._ready = False
        self._set_progress(
            status="downloading",
            downloaded_bytes=0,
            total_bytes=None,
            percent=0,
        )

        try:
            latest = get_latest_release_info()
            asset = find_windows_installer_asset(latest.assets)

            update_dir = get_update_dir()
            safe_tag = "".join(
                c if c.isalnum() or c in "._-" else "_"
                for c in (latest.tag_name or latest.latest_version or "latest")
            )
            target_installer_path = update_dir / f"{safe_tag}-{asset.name}"

            def _progress(downloaded: int, total: Optional[int]) -> None:
                percent = int((downloaded * 100) / total) if total and total > 0 else 0
                self._set_progress(
                    status="downloading",
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                    percent=max(0, min(100, percent)),
                )

            download_update_asset(
                download_url=asset.browser_download_url,
                target_path=target_installer_path,
                progress_cb=_progress,
            )
            _unblock_downloaded_file(target_installer_path)

            relaunch_args = list(sys.argv[1:])
            target_exe = get_relaunch_executable_path()
            metadata = {
                "installer_path": str(target_installer_path),
                "target_exe_path": str(target_exe),
                "relaunch_command_line": subprocess.list2cmdline(relaunch_args),
                "version": latest.latest_version,
                "release_url": latest.release_url,
            }
            (update_dir / "pending_update.json").write_text(
                json.dumps(metadata, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )

            self._installer_path = target_installer_path
            self._relaunch_args = relaunch_args
            self._version = latest.latest_version
            self._release_url = latest.release_url
            self._ready = True
            size = target_installer_path.stat().st_size
            self._set_progress(
                status="ready",
                downloaded_bytes=size,
                total_bytes=size,
                percent=100,
            )

            return {
                "ok": True,
                "version": latest.latest_version,
                "downloaded_path": str(target_installer_path),
            }
        except Exception as e:
            msg = str(e) or "Unknown update download error"
            self._set_progress(
                status="error",
                downloaded_bytes=0,
                total_bytes=None,
                percent=0,
                error=msg,
            )
            return {"ok": False, "error": msg}

    def install_from_file(self, path: Path, on_handoff=None) -> dict:
        if not is_running_as_exe():
            return {"ok": False, "error": "Dev mode does not support restart-and-install self-update."}
        target = get_update_dir() / f"{_SETUP_EXE_BASENAME}.exe"
        try:
            if path.resolve() != target.resolve():
                shutil.copyfile(path, target)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        self._installer_path = target
        self._relaunch_args = list(sys.argv[1:])
        self._version = "local file"
        self._release_url = ""
        self._ready = True
        return self.restart_and_install(on_handoff=on_handoff)

    def restart_and_install(self, on_handoff=None) -> dict[str, Any]:
        """`on_handoff` runs once the installer is confirmed running, just
        before the app exits (the desktop hides its window there)."""
        if not is_running_as_exe():
            return {
                "ok": False,
                "error": "Dev mode does not support restart-and-install self-update.",
            }

        if not self._ready or not self._installer_path:
            return {
                "ok": False,
                "error": "No downloaded update found. Please download update first.",
            }

        installer_path = self._installer_path
        if not installer_path.exists() or installer_path.stat().st_size <= 0:
            return {
                "ok": False,
                "error": "Downloaded installer is missing or invalid.",
            }

        target_exe = get_relaunch_executable_path()
        if not target_exe.exists() or not target_exe.is_file():
            return {
                "ok": False,
                "error": f"Cannot locate executable for relaunch: {target_exe}",
            }

        problem = _installer_check(installer_path)
        if problem:
            return {"ok": False, "error": problem}

        update_dir = get_update_dir()
        pending_json_path = update_dir / "pending_update.json"
        pending_json_path.write_text(
            json.dumps(
                {
                    "installer_path": str(installer_path),
                    "target_exe_path": str(target_exe),
                    "relaunch_command_line": subprocess.list2cmdline(self._relaunch_args),
                    "version": self._version or "",
                    "release_url": self._release_url or "",
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        inno_log_path = update_dir / f"inno_install_{timestamp}.log"
        watcher_path = update_dir / f"relaunch_after_{timestamp}.ps1"
        claim_path = update_dir / f"handoff_{timestamp}.claim"
        result_path = update_dir / RESULT_FILE_NAME
        # A claim file stays behind as a marker when the app wins it, so a late
        # watcher still loses; clear earlier attempts' markers here instead.
        for stale in (*update_dir.glob("handoff_*.claim"), result_path):
            stale.unlink(missing_ok=True)

        _unblock_downloaded_file(installer_path)
        # Start the installer first: if Windows refuses it, nothing else has
        # started yet and the app simply stays open.
        try:
            process = _launch_process(
                str(installer_path), _inno_install_params(inno_log_path), str(update_dir), show=1,
            )
        except OSError as e:
            log.warning("Installer launch failed: %s", e)
            return {"ok": False, "error": _launch_error_message(e)}

        try:
            try:
                watcher_path.write_text(
                    build_relaunch_watcher_ps1(
                        pending_json_path, process.pid, claim_path, result_path, inno_log_path,
                    ),
                    encoding="utf-8",
                )
                _shell_execute(
                    "powershell.exe",
                    f'-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{watcher_path}"',
                    str(update_dir),
                )
                timeout = _HANDOFF_TIMEOUT_SECONDS
            except OSError as e:
                log.warning("Relaunch watcher failed to start: %s", e)
                timeout = 0.0
            owner, code = _await_handoff(process, claim_path, timeout)
        finally:
            process.close()

        if owner == "app" and code is not None and code != 0:
            # The installer quit before the watcher took over: report it here.
            log.warning("Installer exited early with code %s", code)
            return {
                "ok": False,
                "error": installer_failure_message(code),
                "logPath": str(inno_log_path) if inno_log_path.is_file() else "",
            }
        if owner == "app":
            log.warning("Relaunch watcher did not take over; the app will not reopen by itself.")
        log.info("Installer running (pid %s); exiting for the update.", process.pid)
        if on_handoff is not None:
            on_handoff()
        threading.Timer(0.25, lambda: os._exit(0)).start()
        return {"ok": True}
