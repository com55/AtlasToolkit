"""Pure region-name validation rules -- port of
www/js/region-name-validation.js's validateRegionName. See
docs/superpowers/specs/2026-09-01-advanced-region-editing-design.md §2.4
for the design rationale.

`effective_display_names` is a REQUIRED argument, not optional context --
the collision check is dead code without it. Callers (engine/session.py,
a later stage) must build this set themselves before calling here, e.g.
via domain/effective_model.py's derive_effective_model -- this was a real
gap the architecture spec's rev 7 fixed (an earlier draft of that spec
dropped this argument from the AtlasSession.add_region/rename_region
call sites)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    value: str = ""
    reason: str = ""


def validate_region_name(
    candidate: str, effective_display_names: Iterable[str]
) -> ValidationResult:
    """Canonicalizes (trims) once up front so the same string is checked,
    compared, and eventually written -- this is what closes the
    whitespace-evasion class (e.g. " arm " passing validation against
    "arm" then colliding with it after a trim on reload).

    The '#' ban is a TOOL POLICY, not a parser fact: the parser accepts a
    literal '#' in a name and only ever generates one itself during
    parse-time dedup of duplicate literal names (see document.py's
    unique_region_key). Banning it from every name this tool writes means
    the tool can never produce a string that collides with a
    parser-synthesized key on reparse.

    `effective_display_names`: every OTHER region's current effective
    display name (pristine union pending Add/Rename results), never
    including the candidate's own prior name."""
    value = str(candidate if candidate is not None else "").strip()

    if value == "":
        return ValidationResult(ok=False, reason="Name cannot be blank.")
    if "\n" in value or "\r" in value:
        return ValidationResult(ok=False, reason="Name cannot contain a line break.")
    if ":" in value:
        return ValidationResult(ok=False, reason="Name cannot contain ':'.")
    if value.lower().endswith(".png"):
        return ValidationResult(ok=False, reason="Name cannot end in '.png'.")
    if "#" in value:
        return ValidationResult(ok=False, reason="Name cannot contain '#'.")
    for other in effective_display_names:
        if str(other).strip() == value:
            return ValidationResult(ok=False, reason=f"'{value}' is already in use.")
    return ValidationResult(ok=True, value=value)
