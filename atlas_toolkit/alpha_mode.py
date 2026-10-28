"""Alpha space of a caller's image for SpineAtlas edits and extracts
(spec 2026-10-28-alpha-mode). None means the page's own space."""

from __future__ import annotations

from enum import StrEnum

from atlas_toolkit.packer import _type_name


class AlphaMode(StrEnum):
    STRAIGHT = "straight"
    PREMULTIPLIED = "premultiplied"


def parse_alpha_mode(value: object) -> AlphaMode | None:
    if value is None or isinstance(value, AlphaMode):
        return value
    if not isinstance(value, str):
        raise TypeError(f"alpha_mode must be an AlphaMode or str, not {_type_name(value)}")
    try:
        return AlphaMode(value)
    except ValueError:
        names = ", ".join(mode.value for mode in AlphaMode)
        raise ValueError(f"unknown alpha_mode {value!r}; expected one of: {names}") from None
