"""GitHub release check and silent self-update."""

from atlas_toolkit.update.controller import UpdateController
from atlas_toolkit.update.updater import get_current_version

__all__ = ["UpdateController", "get_current_version"]
