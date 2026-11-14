# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Desktop application entry — window creation and pywebview startup."""

from __future__ import annotations

import logging
import sys
from typing import Any, Optional

import webview

from atlas_toolkit.app.desktop import build_desktop, desktop_site
from atlas_toolkit.app import build_info
from atlas_toolkit.app.desktop_commands import DesktopCommands, window_title
from atlas_toolkit.app.paths import is_source_run

log = logging.getLogger(__name__)


def _consume_launch_flags(
    argv: list[str],
) -> tuple[list[str], Optional[dict[str, str]], bool]:
    clean_args: list[str] = []
    failed = False
    failed_log = ""
    failed_release_url = ""
    failed_message = ""
    debug = False

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--debug":
            debug = True
            i += 1
            continue
        if arg == "--update-install-failed":
            failed = True
            i += 1
            continue
        if arg == "--update-failed-log" and i + 1 < len(argv):
            failed_log = argv[i + 1]
            i += 2
            continue
        if arg == "--update-release-url" and i + 1 < len(argv):
            failed_release_url = argv[i + 1]
            i += 2
            continue
        if arg == "--update-failed-message" and i + 1 < len(argv):
            failed_message = argv[i + 1]
            i += 2
            continue
        clean_args.append(arg)
        i += 1

    payload: Optional[dict[str, str]] = None
    if failed:
        payload = {
            "message": failed_message or "Update installation failed. The app was relaunched.",
            "logPath": failed_log,
            "releaseUrl": failed_release_url,
        }
    return clean_args, payload, debug


def configure_stdio() -> None:
    if sys.stdout:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    if sys.stderr:
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)s: %(message)s",
        stream=sys.stdout,
    )


def setup_drop(window: webview.Window, commands: DesktopCommands) -> None:
    try:
        from webview.dom import DOMEventHandler

        def _drag_over_no_op(_e: Any) -> None:
            pass

        log.debug("Binding drop events...")
        doc = window.dom.document
        doc.events.dragover += DOMEventHandler(
            _drag_over_no_op, True, True, debounce=500
        )  # type: ignore[operator]
        doc.events.drop += DOMEventHandler(commands.on_drop, True, True)  # type: ignore[operator]
        log.debug("Drop events bound.")
    except Exception as e:
        log.error("Failed to setup drop events: %s", e)


def run() -> None:
    configure_stdio()
    configure_logging()

    clean_argv, pending_failure, debug_requested = _consume_launch_flags(sys.argv[1:])
    sys.argv = [sys.argv[0], *clean_argv]

    webview_debug = debug_requested and (is_source_run() or build_info.DEV_BUILD)
    if debug_requested and not webview_debug:
        log.info("--debug is only available when running from source or a development build; ignored.")
    elif webview_debug:
        log.info("pywebview debug mode enabled (--debug).")

    site = desktop_site()
    if not (site / "index.html").is_file():
        if is_source_run():
            log.error("Desktop site missing; run uv run python scripts/build_web.py --target desktop")
        else:
            log.error("Desktop site missing from packaged web resources.")
        sys.exit(1)

    window_width, window_height = 1200, 800
    if sys.platform == "win32":
        import ctypes

        screen_width = ctypes.windll.user32.GetSystemMetrics(0)
        screen_height = ctypes.windll.user32.GetSystemMetrics(1)
    else:
        _scr = webview.screens[0]
        screen_width, screen_height = _scr.width, _scr.height
    center_x = (screen_width - window_width) // 2
    center_y = (screen_height - window_height) // 2

    def window_factory(url):
        return webview.create_window(
            window_title(),
            url=url,
            width=window_width,
            height=window_height,
            min_size=(1000, 650),
            x=center_x,
            y=center_y,
            resizable=True,
            background_color="#2b2b2b",
        )

    server, commands, window = build_desktop(
        site, sys.argv, pending_failure, window_factory, webview.FileDialog,
    )
    window.events.closing += commands.on_closing

    def on_closed():
        commands.set_window(None)
        server.stop()

    window.events.closed += on_closed
    window.events.loaded += lambda: setup_drop(window, commands)
    webview.start(debug=webview_debug)
