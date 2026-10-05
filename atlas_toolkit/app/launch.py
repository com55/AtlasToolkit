"""Desktop application entry — window creation and pywebview startup."""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

import webview

from atlas_toolkit.app.bridge import Api, setup_drop
from atlas_toolkit.app.paths import is_source_run, resource_path
from atlas_toolkit.update.updater import get_current_version

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


def _allow_qtwebengine_file_module_imports() -> None:
    """Let QtWebEngine load `www/index.html`'s `<script type="module">` chain
    over a literal `file://` URI (how `launch.py` always loads it -- see
    `gui_path` below).

    Chromium enforces CORS on ES-module fetches even for same-directory
    relative imports, and `file://` isn't in the CORS-safelisted schemes --
    so without this, `www/script.js` (and everything under `www/js/`) never
    loads at all: every `window.*` handler `index.html`'s inline
    `onclick="..."` attributes call (`openFile`, etc.) stays undefined, and
    every button throws `ReferenceError` on click. Confirmed via a live
    Chrome DevTools Protocol session against the real Qt-backend window
    (2026-09-14): `typeof window.openFile` was `"undefined"` before this
    fix, `"function"` after.

    pywebview's own CEF and EdgeChromium (Windows WebView2) backends already
    work around exactly this by adding `--allow-file-access-from-files` to
    their browser args (see `.venv/.../webview/platforms/cef.py` and
    `edgechromium.py`) -- only the Qt backend (`platforms/qt.py`) is missing
    it, which is why this only surfaced when pywebview picked Qt (this
    happens whenever no GTK bindings are available, e.g. `PYWEBVIEW_GUI=qt`
    or a Linux box without GTK webkit installed).

    Must run before ANY pywebview call touches the Qt backend --
    `webview.screens[0]` below already triggers backend initialization on
    non-Windows platforms, so this has to be the first thing `run()` does.
    `QTWEBENGINE_CHROMIUM_FLAGS` is specific to Qt's bundled Chromium and is
    simply ignored by every other backend (GTK, CEF, EdgeChromium), so
    setting it unconditionally here is a no-op everywhere except Qt.
    """
    existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    flag = "--allow-file-access-from-files"
    if flag not in existing.split():
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{existing} {flag}".strip()


def run() -> None:
    configure_stdio()
    configure_logging()
    _allow_qtwebengine_file_module_imports()

    clean_argv, pending_failure, debug_requested = _consume_launch_flags(sys.argv[1:])
    sys.argv = [sys.argv[0], *clean_argv]

    webview_debug = debug_requested and is_source_run()
    if debug_requested and not webview_debug:
        log.info("--debug is only available when running from source; ignored.")
    elif webview_debug:
        log.info("pywebview debug mode enabled (--debug).")

    api = Api(pending_update_failure=pending_failure)

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

    gui_path = resource_path("www/index.html")
    window = webview.create_window(
        f"Atlas Toolkit v{get_current_version()}",
        url=str(gui_path.absolute().as_uri()),
        width=window_width,
        height=window_height,
        # (1000, 650): 1000px width keeps the window above style.css's
        # `max-width: 900px` stacked-layout breakpoint. This alone doesn't
        # stop `orientation: portrait` from matching (that fires whenever
        # height > width, regardless of absolute size) — the real fix is the
        # `html.pywebview` CSS-class scoping (index.html + style.css,
        # unify-js-engine Phase 6); this is just a belt-and-suspenders floor
        # so the window can't be dragged into an unreasonably cramped size.
        min_size=(1000, 650),
        x=center_x,
        y=center_y,
        resizable=True,
        js_api=api,
        background_color="#2b2b2b",
    )

    if window:
        api.set_window(window)
        # api.on_closing (not _confirm_discard_modifications directly!) —
        # see its docstring: evaluate_js can't be called synchronously from
        # this event without deadlocking pywebview.
        window.events.closing += api.on_closing
        window.events.closed += api.on_closed
    else:
        sys.exit(1)

    webview.start(func=setup_drop, args=(window, api), debug=webview_debug)
