"""Update checker for Atlas Extracter - checks GitHub for new releases."""
import logging
import re
from typing import NamedTuple

import requests

logger = logging.getLogger(__name__)

GITHUB_REPO = "com55/AtlasToolkit"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"


class UpdateInfo(NamedTuple):
    current_version: str
    latest_version: str
    release_name: str
    release_url: str


def is_running_as_exe() -> bool:
    """Check if running as Nuitka-compiled executable."""
    return "__compiled__" in globals()


def get_current_version() -> str:
    """Read version from VERSION file when running as exe, pyproject.toml when in dev."""
    from pathlib import Path

    if is_running_as_exe():
        # Nuitka embeds VERSION next to the exe via include-data-files
        version_file = Path(__file__).parent / "VERSION"
        try:
            if version_file.exists():
                return version_file.read_text(encoding="utf-8-sig").strip()
        except Exception:
            pass
        return "0.0.0"
    else:
        # Dev mode — read from pyproject.toml
        try:
            toml_path = Path(__file__).parent / "pyproject.toml"
            if toml_path.exists():
                for line in toml_path.read_text(encoding="utf-8-sig").splitlines():
                    line = line.strip()
                    if line.startswith("version"):
                        return line.split("=", 1)[1].strip().strip('"\'')
        except Exception:
            pass
        return "0.0.0"


def _version_tuple(v: str) -> tuple[int, ...]:
    """Extract numeric version tuple, e.g. 'v1.2.3-beta' -> (1, 2, 3)."""
    match = re.search(r"(\d+(?:\.\d+)*)", v.lstrip("v"))
    if match:
        return tuple(map(int, match.group(0).split(".")))
    return (0,)


def check_for_updates() -> UpdateInfo | None:
    """
    Check GitHub latest release.
    Returns UpdateInfo if a newer version exists, else None.
    Never raises — all errors are logged and swallowed.
    """
    try:
        response = requests.get(
            GITHUB_API_LATEST,
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=8,
        )
        response.raise_for_status()
        from typing import Any
        data: dict[str, Any] = response.json()

        latest_version = data.get("tag_name", "").lstrip("v")
        release_name = data.get("name", latest_version)
        release_url = data.get("html_url", GITHUB_RELEASES_PAGE)

        current = get_current_version().lstrip("v")
        logger.debug("Version check: current=%s latest=%s", current, latest_version)

        if _version_tuple(latest_version) > _version_tuple(current):
            logger.info("Update available: %s -> %s", current, latest_version)
            return UpdateInfo(
                current_version=current,
                latest_version=latest_version,
                release_name=release_name,
                release_url=release_url,
            )

    except requests.RequestException as e:
        logger.warning("Network error during update check: %s", e)
    except Exception as e:
        logger.error("Unexpected error during update check: %s", e, exc_info=True)

    return None