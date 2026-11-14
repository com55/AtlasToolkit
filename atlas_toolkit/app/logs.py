# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Bounded per-launch desktop logs, independent of webview."""
from datetime import datetime, timedelta
import logging
import os
from pathlib import Path
import platform
import sys
import threading

from atlas_toolkit.app.config import get_config_dir
from atlas_toolkit.app import build_info
from atlas_toolkit.update.updater import get_current_version


def log_dir() -> Path:
    directory = get_config_dir() / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


class _CappedFileHandler(logging.FileHandler):
    def __init__(self, path, max_bytes):
        super().__init__(path, mode="x", encoding="utf-8")
        self.max_bytes = max_bytes
        self.stopped = False

    def emit(self, record):
        if self.stopped:
            return
        size = len((self.format(record) + self.terminator).encode("utf-8"))
        if self.stream.tell() + size >= self.max_bytes:
            self.stopped = True
            record = logging.LogRecord(__name__, logging.WARNING, __file__, 0,
                                       "log size limit reached; further messages dropped", (), None)
        super().emit(record)


def start_session_log(debug: bool, *, directory: Path | None = None,
                      keep: int = 10, max_bytes: int = 20 * 1024 * 1024) -> Path:
    directory = log_dir() if directory is None else directory
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now()
    while True:
        path = directory / f"atlastoolkit-{stamp:%Y%m%d-%H%M%S}-{os.getpid()}.log"
        try:
            handler = _CappedFileHandler(path, max_bytes)
            break
        except FileExistsError:
            stamp += timedelta(seconds=1)
    old = sorted(p for p in directory.glob("atlastoolkit-*.log") if p != path)
    for stale in old[:max(0, len(old) + 1 - max(1, keep))]:
        try:
            stale.unlink()
        except OSError:
            pass
    root = logging.getLogger()
    for previous in root.handlers[:]:
        if getattr(previous, "_atlas_session", False):
            root.removeHandler(previous)
            previous.close()
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s")
    for target in (handler, logging.StreamHandler(sys.stdout)):
        target.setFormatter(formatter)
        target._atlas_session = True
        root.addHandler(target)
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.info("AtlasToolkit %s DEV_BUILD=%s platform=%s Python=%s", get_current_version(),
              build_info.DEV_BUILD, platform.platform(), platform.python_version())
    return path


def install_excepthooks():
    previous_sys = sys.excepthook
    previous_thread = threading.excepthook

    def uncaught(kind, value, traceback):
        logging.getLogger(__name__).error("Uncaught exception", exc_info=(kind, value, traceback))
        previous_sys(kind, value, traceback)

    def uncaught_thread(args):
        logging.getLogger(__name__).error("Uncaught thread exception", exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        previous_thread(args)

    sys.excepthook = uncaught
    threading.excepthook = uncaught_thread
