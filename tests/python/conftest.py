# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for the app tests (tests/python)."""

import pytest


@pytest.fixture(autouse=True)
def isolate_config_path(tmp_path, monkeypatch):
    """Keep every test away from the developer's real preferences."""
    try:
        from atlas_toolkit_app.app import config
    except ImportError:
        return
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
