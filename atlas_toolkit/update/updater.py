"""Update and download helpers for AtlasToolkit releases on GitHub."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional

import requests

logger = logging.getLogger(__name__)

GITHUB_REPO = "com55/AtlasToolkit"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"
# The self-update flow downloads and silently runs the Inno Setup installer.
WINDOWS_INSTALLER_ASSET_NAME = "AtlasToolkit-Setup-x64.exe"


class ReleaseAsset(NamedTuple):
    name: str
    browser_download_url: str
    size: int


class ReleaseInfo(NamedTuple):
    latest_version: str
    release_name: str
    release_url: str
    tag_name: str
    source_tree_url: str
    assets: list[ReleaseAsset]


class UpdateInfo(NamedTuple):
    current_version: str
    latest_version: str
    release_name: str
    release_url: str
    tag_name: str
    source_tree_url: str
    assets: list[ReleaseAsset]


def is_running_as_exe() -> bool:
    """Check if running as Nuitka-compiled executable."""
    import sys

    if getattr(sys, "frozen", False):
        return True
    main = sys.modules.get("__main__")
    return main is not None and getattr(main, "__compiled__", None) is not None


def get_current_version() -> str:
    """Read version from VERSION file when running as exe, pyproject.toml when in dev."""
    from atlas_toolkit.paths import app_root

    root = app_root()
    if is_running_as_exe():
        version_file = root / "VERSION"
        try:
            if version_file.exists():
                return version_file.read_text(encoding="utf-8-sig").strip()
        except Exception:
            pass
        return "0.0.0"

    try:
        toml_path = root / "pyproject.toml"
        if toml_path.exists():
            for line in toml_path.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line.startswith("version"):
                    return line.split("=", 1)[1].strip().strip("\"'")
    except Exception:
        pass
    return "0.0.0"


def _version_tuple(v: str) -> tuple[int, ...]:
    """Extract numeric version tuple, e.g. 'v1.2.3-beta' -> (1, 2, 3)."""
    match = re.search(r"(\d+(?:\.\d+)*)", v.lstrip("v"))
    if match:
        return tuple(map(int, match.group(0).split(".")))
    return (0,)


def get_latest_release_info() -> ReleaseInfo:
    """Fetch latest release metadata from GitHub API."""
    response = requests.get(
        GITHUB_API_LATEST,
        headers={"Accept": "application/vnd.github.v3+json"},
        timeout=12,
    )
    response.raise_for_status()
    data: dict[str, Any] = response.json()

    tag_name = str(data.get("tag_name") or "").strip()
    latest_version = tag_name.lstrip("v") or str(data.get("name") or "").strip()
    release_name = str(data.get("name") or tag_name or latest_version or "Latest Release")
    release_url = str(data.get("html_url") or GITHUB_RELEASES_PAGE)
    source_tree_url = (
        f"https://github.com/{GITHUB_REPO}/tree/{tag_name}"
        if tag_name
        else release_url
    )

    assets_data = data.get("assets") or []
    assets: list[ReleaseAsset] = []
    for raw in assets_data:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        browser_download_url = str(raw.get("browser_download_url") or "").strip()
        try:
            size = int(raw.get("size") or 0)
        except Exception:
            size = 0
        if name and browser_download_url:
            assets.append(
                ReleaseAsset(
                    name=name,
                    browser_download_url=browser_download_url,
                    size=size,
                )
            )

    return ReleaseInfo(
        latest_version=latest_version,
        release_name=release_name,
        release_url=release_url,
        tag_name=tag_name,
        source_tree_url=source_tree_url,
        assets=assets,
    )


def find_windows_installer_asset(assets: list[ReleaseAsset]) -> ReleaseAsset:
    """Find the Windows installer (Setup.exe) asset produced by CI."""
    for asset in assets:
        if asset.name == WINDOWS_INSTALLER_ASSET_NAME:
            return asset
    raise FileNotFoundError(
        f"Release asset '{WINDOWS_INSTALLER_ASSET_NAME}' was not found in latest release"
    )


def download_update_asset(
    download_url: str,
    target_path: Path,
    progress_cb: Optional[Callable[[int, Optional[int]], None]] = None,
) -> Path:
    """Download an update asset (the installer exe) to a target path via streaming I/O."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_suffix(target_path.suffix + ".part")

    downloaded = 0
    total: Optional[int] = None

    try:
        with requests.get(download_url, stream=True, timeout=60) as response:
            response.raise_for_status()
            header_total = response.headers.get("Content-Length")
            if header_total:
                try:
                    total = int(header_total)
                except Exception:
                    total = None

            with temp_path.open("wb") as fh:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        progress_cb(downloaded, total)
    except Exception:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except Exception:
            pass
        raise

    temp_path.replace(target_path)

    if not target_path.exists() or target_path.stat().st_size <= 0:
        raise IOError("Downloaded update asset is missing or empty")

    if progress_cb:
        progress_cb(target_path.stat().st_size, total)

    return target_path


def check_for_updates() -> UpdateInfo | None:
    """
    Check GitHub latest release.
    Returns UpdateInfo if a newer version exists, else None.
    Never raises — all errors are logged and swallowed.
    """
    try:
        latest = get_latest_release_info()
        latest_version = latest.latest_version

        current = get_current_version().lstrip("v")
        logger.debug("Version check: current=%s latest=%s", current, latest_version)

        if _version_tuple(latest_version) > _version_tuple(current):
            logger.info("Update available: %s -> %s", current, latest_version)
            return UpdateInfo(
                current_version=current,
                latest_version=latest_version,
                release_name=latest.release_name,
                release_url=latest.release_url,
                tag_name=latest.tag_name,
                source_tree_url=latest.source_tree_url,
                assets=latest.assets,
            )

    except requests.RequestException as e:
        logger.warning("Network error during update check: %s", e)
    except Exception as e:
        logger.error("Unexpected error during update check: %s", e, exc_info=True)

    return None