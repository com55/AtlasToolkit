# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Assemble the HTTP desktop runtime without importing the GUI package."""
from pathlib import Path
from typing import Any

from atlas_toolkit.app.config import AppConfig
from atlas_toolkit.app.desktop_commands import DesktopCommands
from atlas_toolkit.app.events import EventBroker
from atlas_toolkit.app.files import DesktopFiles
from atlas_toolkit.app.paths import app_root, is_source_run, resource_path
from atlas_toolkit.app.server import DesktopServer
from atlas_toolkit.host import AppHost
from atlas_toolkit.update.controller import UpdateController


def desktop_site() -> Path:
    return app_root() / "dist" / "desktop" if is_source_run() else resource_path("web")


def build_desktop(
    site: Path, argv: list[str], pending_failure: dict | None,
    window_factory, file_dialog,
) -> tuple[DesktopServer, DesktopCommands, Any]:
    files = DesktopFiles()
    events = EventBroker()
    host = AppHost()
    config = AppConfig()
    updates = UpdateController(pending_failure=pending_failure)
    commands = DesktopCommands(
        None, files, events, config, updates, argv,
        is_unsaved=lambda: server.unsaved, file_dialog=file_dialog,
    )
    server = DesktopServer(site, host, commands, files, events)
    url = server.start()
    try:
        window = window_factory(url)
        if not window:
            raise RuntimeError("window creation failed")
    except BaseException:
        server.stop()
        raise
    commands.set_window(window)
    return server, commands, window
