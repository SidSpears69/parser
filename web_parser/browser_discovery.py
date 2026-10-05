"""Find installed Windows browsers without starting them.

App Paths registry entries take precedence over conventional installation folders,
which in turn take precedence over executables found on PATH. A stale entry is
ignored so the next location can be checked.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
import os
from pathlib import Path
from typing import Iterator

from web_parser.config import BROWSERS


@dataclass(frozen=True)
class BrowserInstallation:
    name: str
    path: Path


@dataclass(frozen=True)
class _BrowserSpec:
    name: str
    executable: str
    local_path: str
    program_path: str


_BROWSER_SPECS = (
    _BrowserSpec(BROWSERS[0], "browser.exe", "Yandex/YandexBrowser/Application", "Yandex/YandexBrowser/Application"),
    _BrowserSpec(BROWSERS[1], "chrome.exe", "Google/Chrome/Application", "Google/Chrome/Application"),
    _BrowserSpec(BROWSERS[2], "msedge.exe", "Microsoft/Edge/Application", "Microsoft/Edge/Application"),
    _BrowserSpec(BROWSERS[3], "firefox.exe", "Mozilla Firefox", "Mozilla Firefox"),
)


def _windows_host() -> bool:
    return os.name == "nt"


def _registry_paths(executable: str) -> Iterator[Path]:
    """Read per-user and machine App Paths from both registry views."""
    try:
        import winreg
    except ImportError:
        return

    key_name = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(root, key_name, 0, winreg.KEY_READ | view) as key:
                    raw, _ = winreg.QueryValueEx(key, "")
            except OSError:
                continue
            if isinstance(raw, str):
                expanded = os.path.expandvars(raw.strip().strip('"'))
                if expanded:
                    yield Path(expanded)


def _folder_paths(spec: _BrowserSpec) -> Iterator[Path]:
    locations = (
        ("LOCALAPPDATA", spec.local_path),
        ("PROGRAMFILES", spec.program_path),
        ("PROGRAMFILES(X86)", spec.program_path),
        ("PROGRAMW6432", spec.program_path),
    )
    for variable, relative in locations:
        base = os.environ.get(variable)
        if base:
            yield Path(base) / relative / spec.executable


def _path_candidates(executable: str) -> Iterator[Path]:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if directory:
            yield Path(directory.strip('"')) / executable


def discover_browsers() -> dict[str, BrowserInstallation]:
    """Return known browsers whose executable files exist on this Windows host."""
    if not _windows_host():
        return {}

    installed: dict[str, BrowserInstallation] = {}
    for spec in _BROWSER_SPECS:
        candidates = chain(
            _registry_paths(spec.executable),
            _folder_paths(spec),
            _path_candidates(spec.executable),
        )
        for path in candidates:
            try:
                if path.is_file():
                    installed[spec.name] = BrowserInstallation(spec.name, path)
                    break
            except OSError:
                continue
    return installed
