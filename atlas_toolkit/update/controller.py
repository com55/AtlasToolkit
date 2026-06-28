"""Self-update download and silent install orchestration."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
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

# Runner invokes Inno after this process exits (AppMutex blocks while we are alive).
# Relaunch with preserved argv is done by the runner — not Inno Restart Manager.
_INNO_SILENT_INSTALL_FLAGS = (
    "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART "
    "/CLOSEAPPLICATIONS /NORESTARTAPPLICATIONS"
)
_PID_WAIT_MAX_ITERATIONS = 90


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


def _batch_quote(value: str) -> str:
    """Quote and escape a value for a cmd.exe double-quoted string."""
    escaped = value.replace("^", "^^").replace('"', '^"')
    return f'"{escaped}"'


def build_update_runner_script(
    installer_path: Path,
    target_exe: Path,
    pid: int,
    relaunch_args: list[str],
    inno_log_path: Path,
) -> str:
    """Wait for app exit, run Inno, relaunch with saved command-line args."""
    inst = _batch_quote(str(installer_path))
    exe = _batch_quote(str(target_exe))
    log_path = _batch_quote(str(inno_log_path))
    relaunch_suffix = " ".join(_batch_quote(a) for a in relaunch_args)
    success_launch = f'start "" {exe} {relaunch_suffix}'.rstrip()

    lines = [
        "@echo off",
        "setlocal",
        "set /a wait_count=0",
        ":waitloop",
        f'tasklist /FI "PID eq {pid}" 2>nul | find /I " {pid} " >nul',
        "if not errorlevel 1 (",
        "    set /a wait_count+=1",
        f'    if %wait_count% geq {_PID_WAIT_MAX_ITERATIONS} goto runinstaller',
        "    ping -n 2 127.0.0.1 >nul",
        "    goto waitloop",
        ")",
        ":runinstaller",
        f"{inst} {_INNO_SILENT_INSTALL_FLAGS} /LOG={log_path}",
        "if errorlevel 1 goto cleanup",
        success_launch,
        ":cleanup",
        'start "" /min cmd /c del /f /q "%~f0"',
        "endlocal",
        "exit /b 0",
    ]
    return "\r\n".join(lines) + "\r\n"


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

            relaunch_args = list(sys.argv[1:])
            target_exe = get_relaunch_executable_path()
            metadata = {
                "installer_path": str(target_installer_path),
                "target_exe_path": str(target_exe),
                "relaunch_args": relaunch_args,
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

    def restart_and_install(self) -> dict[str, Any]:
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

        update_dir = get_update_dir()
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        inno_log_path = update_dir / f"inno_install_{timestamp}.log"
        script_text = build_update_runner_script(
            installer_path=installer_path,
            target_exe=target_exe,
            pid=os.getpid(),
            relaunch_args=self._relaunch_args,
            inno_log_path=inno_log_path,
        )
        script_path = update_dir / f"install_update_{timestamp}_{os.getpid()}.cmd"

        try:
            script_path.write_text(script_text, encoding="utf-8")
            cmd_exe = os.environ.get("COMSPEC") or "cmd"
            popen_kwargs: dict[str, Any] = {
                "cwd": str(update_dir),
                "close_fds": True,
            }
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = (
                    subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NO_WINDOW
                )
            else:
                popen_kwargs["start_new_session"] = True

            # Runner waits for this PID; exit immediately so Inno AppMutex is released.
            subprocess.Popen([cmd_exe, "/d", "/c", str(script_path)], **popen_kwargs)
            threading.Timer(0.25, lambda: os._exit(0)).start()
            log.info("Launched update runner (pid=%s): %s", os.getpid(), script_path.name)
            return {"ok": True}
        except Exception as e:
            return {
                "ok": False,
                "error": f"Failed to launch update installer: {e}",
            }
