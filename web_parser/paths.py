"""Writable paths shared by source runs and packaged Windows builds."""

import os
from pathlib import Path
import sys


def application_root() -> Path:
    """Locate the project root or the directory containing the frozen exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ARTIFACT_ROOT = application_root() / "artifacts"


def user_settings_path() -> Path:
    """Keep settings outside the directory replaced by the installer."""
    local_data = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return local_data / "WebParser" / "settings.json"
