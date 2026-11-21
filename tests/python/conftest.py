# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for tests/python."""

import pytest


@pytest.fixture(autouse=True)
def isolate_config_path(tmp_path, monkeypatch):
    """Keep every test away from the developer's real preferences."""
    try:
        from atlas_toolkit_app.app import config
    except ImportError:
        return
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")




@pytest.fixture
def untrimmed_nest(monkeypatch):
    """Nest pages keep their grown canvas, as before the final trim
    (packing.trim_canvas). For fixtures pinned to older commits; they still
    pin every placement."""
    import spine_atlas_toolkit.engine.modifier as modifier

    monkeypatch.setattr(modifier, "trim_canvas", lambda packed: packed)


@pytest.fixture
def no_auto_bleed(monkeypatch):
    """PackOptions(bleed=None) stays off with a split pack, as before bleed
    existed. For fixtures pinned to older commits."""
    import spine_atlas_toolkit.packer as packer_module

    monkeypatch.setattr(packer_module, "_auto_bleed", lambda split: False)
