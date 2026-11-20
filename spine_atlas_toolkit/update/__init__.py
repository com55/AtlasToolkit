# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
"""GitHub release check and silent self-update."""

from spine_atlas_toolkit.update.controller import UpdateController
from spine_atlas_toolkit.update.updater import get_current_version

__all__ = ["UpdateController", "get_current_version"]
