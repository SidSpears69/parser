"""Writable paths shared by source runs and packaged Windows builds."""

from pathlib import Path
import sys


def application_root() -> Path:
    """Locate the project root or the directory containing the frozen exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ARTIFACT_ROOT = application_root() / "artifacts"
