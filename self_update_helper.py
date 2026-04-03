from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Sequence

log = logging.getLogger("atlas_update_helper")


def _get_update_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "AtlasToolkit" / "update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_logging() -> Path:
    update_dir = _get_update_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = update_dir / f"self_update_{timestamp}.log"

    formatter = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    root.addHandler(stdout_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    return log_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AtlasToolkit self-update helper")
    parser.add_argument("--zip", dest="zip_path", required=True)
    parser.add_argument("--target-exe", dest="target_exe", required=True)
    parser.add_argument("--work-dir", dest="work_dir", required=True)
    parser.add_argument("--relaunch-args-b64", dest="relaunch_args_b64", default="")
    parser.add_argument("--pid", dest="pid", type=int, default=0)
    parser.add_argument("--release-url", dest="release_url", default="")
    return parser.parse_args()


def decode_relaunch_args(encoded: str) -> list[str]:
    if not encoded:
        return []
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
        parsed = json.loads(raw.decode("utf-8"))
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except Exception as e:
        log.warning("Failed to decode relaunch args: %s", e)
    return []


def wait_for_process_exit(pid: int, timeout_seconds: int = 45) -> bool:
    if pid <= 0:
        return True

    deadline = time.time() + timeout_seconds

    if os.name == "nt":
        try:
            import ctypes

            SYNCHRONIZE = 0x00100000
            WAIT_OBJECT_0 = 0x00000000
            WAIT_TIMEOUT = 0x00000102

            kernel32 = ctypes.windll.kernel32
            proc_handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if not proc_handle:
                return True
            try:
                remaining_ms = max(0, int((deadline - time.time()) * 1000))
                result = kernel32.WaitForSingleObject(proc_handle, remaining_ms)
                return result in (WAIT_OBJECT_0,)
            finally:
                kernel32.CloseHandle(proc_handle)
        except Exception as e:
            log.warning("Windows process wait failed; using polling fallback: %s", e)

    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return True
        time.sleep(0.2)

    return False


def extract_update_zip(zip_path: Path) -> Path:
    extract_dir = Path(tempfile.mkdtemp(prefix="atlas_update_extract_"))
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
    except zipfile.BadZipFile:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise
    return extract_dir


def find_extracted_exe(extract_dir: Path, expected_name: str = "AtlasToolkit.exe") -> Path:
    direct = extract_dir / expected_name
    if direct.exists():
        return direct

    matches = list(extract_dir.rglob(expected_name))
    if not matches:
        raise FileNotFoundError(f"Could not find {expected_name} in extracted update")
    return matches[0]


def replace_exe(new_exe: Path, target_exe: Path) -> Path | None:
    backup_path = target_exe.with_name(target_exe.name + ".old")
    backup_created = False

    if backup_path.exists():
        try:
            backup_path.unlink()
        except Exception:
            pass

    if target_exe.exists():
        os.replace(target_exe, backup_path)
        backup_created = True

    try:
        try:
            os.replace(new_exe, target_exe)
        except Exception:
            shutil.copy2(new_exe, target_exe)
    except Exception:
        if backup_created and backup_path.exists() and not target_exe.exists():
            try:
                os.replace(backup_path, target_exe)
            except Exception:
                pass
        raise

    return backup_path if backup_created else None


def _build_relaunch_env() -> dict[str, str]:
    env = os.environ.copy()
    removed = [k for k in list(env.keys()) if k.startswith("NUITKA_ONEFILE")]
    for key in removed:
        env.pop(key, None)
    if removed:
        log.info("Relaunch env stripped keys: %s", removed)
    return env


def relaunch(exe_path: Path, work_dir: Path, relaunch_args: Sequence[str]) -> None:
    full_cmd = [str(exe_path), *[str(a) for a in relaunch_args]]
    bare_cmd = [str(exe_path)]
    cwd = str(work_dir)
    relaunch_env = _build_relaunch_env()

    if os.name == "nt":
        cmd_exe = relaunch_env.get("COMSPEC") or "cmd"
        primary_cmd = [
            cmd_exe,
            "/c",
            "start",
            "",
            str(exe_path),
            *[str(a) for a in relaunch_args],
        ]

        try:
            proc = subprocess.Popen(
                primary_cmd,
                cwd=cwd,
                close_fds=True,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                env=relaunch_env,
            )
            log.info("Relaunch primary via cmd/start started pid=%s cmd=%s", proc.pid, primary_cmd)
            return
        except Exception as e:
            log.warning("Relaunch primary cmd/start failed: %s", e)

        # Fallback to direct executable launch strategies.
        attempts = [
            (
                "with-args detached",
                full_cmd,
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            ),
            (
                "with-args new-process-group",
                full_cmd,
                subprocess.CREATE_NEW_PROCESS_GROUP,
            ),
            (
                "no-args detached",
                bare_cmd,
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            ),
            (
                "no-args new-process-group",
                bare_cmd,
                subprocess.CREATE_NEW_PROCESS_GROUP,
            ),
        ]

        for idx, (label, cmd, creationflags) in enumerate(attempts, start=1):
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                close_fds=True,
                creationflags=creationflags,
                env=relaunch_env,
            )
            log.info(
                "Relaunch attempt %s (%s) started pid=%s flags=0x%X cmd=%s",
                idx,
                label,
                proc.pid,
                creationflags,
                cmd,
            )

            # If process exits immediately, try a different launch strategy.
            time.sleep(1.0)
            rc = proc.poll()
            if rc is None:
                return

            log.warning(
                "Relaunch attempt %s (%s) exited immediately with code %s",
                idx,
                label,
                rc,
            )

        raise RuntimeError("All Windows relaunch attempts failed")
    else:
        proc = subprocess.Popen(
            full_cmd,
            cwd=cwd,
            close_fds=True,
            start_new_session=True,
            env=relaunch_env,
        )
        log.info("Relaunch started pid=%s", proc.pid)


def relaunch_with_failure_notice(
    exe_path: Path,
    work_dir: Path,
    relaunch_args: Sequence[str],
    log_path: Path,
    failure_message: str,
    release_url: str,
) -> None:
    args = [
        *[str(a) for a in relaunch_args],
        "--update-install-failed",
        "--update-failed-log",
        str(log_path),
        "--update-failed-message",
        failure_message,
    ]
    if release_url:
        args.extend(["--update-release-url", release_url])
    relaunch(exe_path, work_dir, args)


def safe_cleanup(
    zip_path: Path,
    extract_dir: Path,
    backup_path: Path | None,
    *,
    remove_backup: bool,
) -> None:
    try:
        if zip_path.exists():
            zip_path.unlink()
    except Exception:
        pass

    shutil.rmtree(extract_dir, ignore_errors=True)

    if remove_backup and backup_path and backup_path.exists():
        removed = False
        for attempt in range(1, 13):
            try:
                backup_path.unlink()
                log.info("Removed backup executable: %s", backup_path)
                removed = True
                break
            except PermissionError:
                # Antivirus or indexing can briefly lock new/old executables.
                time.sleep(0.25)
            except Exception as e:
                log.warning("Failed to remove backup executable %s: %s", backup_path, e)
                break

        if not removed and backup_path.exists():
            log.warning("Backup executable still exists after cleanup: %s", backup_path)


def _pick_relaunch_target(target_exe: Path, backup_path: Path | None) -> Path:
    if target_exe.exists():
        return target_exe
    if backup_path and backup_path.exists():
        return backup_path
    return target_exe


def main() -> int:
    args = parse_args()
    log_path = setup_logging()

    zip_path = Path(args.zip_path)
    target_exe = Path(args.target_exe)
    work_dir = Path(args.work_dir)
    relaunch_args = decode_relaunch_args(args.relaunch_args_b64)
    release_url = str(args.release_url or "")

    log.info("Starting self-update helper")
    log.info("zip=%s target_exe=%s pid=%s", zip_path, target_exe, args.pid)

    if not zip_path.exists() or zip_path.stat().st_size <= 0:
        msg = f"Update zip does not exist or is empty: {zip_path}"
        log.error(msg)
        try:
            relaunch_with_failure_notice(
                target_exe,
                work_dir,
                relaunch_args,
                log_path,
                "Update failed: downloaded package is missing or empty.",
                release_url,
            )
        except Exception as e:
            log.error("Fallback relaunch failed: %s", e)
        return 2

    if not wait_for_process_exit(args.pid):
        msg = f"Timed out waiting for old process to exit: pid={args.pid}"
        log.error(msg)
        try:
            relaunch_with_failure_notice(
                target_exe,
                work_dir,
                relaunch_args,
                log_path,
                "Update failed: timed out while waiting for app shutdown.",
                release_url,
            )
        except Exception as e:
            log.error("Fallback relaunch failed: %s", e)
        return 3

    extract_dir: Path | None = None
    backup_path: Path | None = None
    succeeded = False

    try:
        extract_dir = extract_update_zip(zip_path)
        new_exe = find_extracted_exe(extract_dir)
        backup_path = replace_exe(new_exe, target_exe)
        relaunch(target_exe, work_dir, relaunch_args)
        succeeded = True
    except zipfile.BadZipFile:
        log.error("Downloaded update zip is corrupted")
        try:
            relaunch_with_failure_notice(
                _pick_relaunch_target(target_exe, backup_path),
                work_dir,
                relaunch_args,
                log_path,
                "Update failed: update package is corrupted.",
                release_url,
            )
        except Exception as e:
            log.error("Fallback relaunch failed: %s", e)
        return 4
    except PermissionError as e:
        log.error("Permission denied while replacing executable: %s", e)
        try:
            relaunch_with_failure_notice(
                _pick_relaunch_target(target_exe, backup_path),
                work_dir,
                relaunch_args,
                log_path,
                "Update failed: no permission to replace executable.",
                release_url,
            )
        except Exception as e2:
            log.error("Fallback relaunch failed: %s", e2)
        return 5
    except Exception as e:
        log.error("Update installation failed: %s", e)
        try:
            relaunch_with_failure_notice(
                _pick_relaunch_target(target_exe, backup_path),
                work_dir,
                relaunch_args,
                log_path,
                "Update failed: unexpected installation error.",
                release_url,
            )
        except Exception as e2:
            log.error("Fallback relaunch failed: %s", e2)
        return 6
    finally:
        if extract_dir:
            safe_cleanup(
                zip_path,
                extract_dir,
                backup_path,
                remove_backup=succeeded,
            )

    log.info("Self-update completed successfully")

    return 0


if __name__ == "__main__":
    sys.exit(main())
