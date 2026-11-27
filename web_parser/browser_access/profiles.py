"""Persistent browser profiles and recovery from startup failures."""

import os
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError

from web_parser.browser_access.models import BrowserAccessError
from web_parser.config import BROWSERS, SITES


def browser_profile_dir(site: str, browser: str) -> Path:
    """Keep each site/browser session outside the repository and other profiles."""
    if site not in SITES or browser not in BROWSERS:
        raise ValueError("Неизвестный сайт или браузер для профиля Playwright.")
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    browser_slug = ("yandex", "chrome", "edge", "firefox")[BROWSERS.index(browser)]
    site_slug = site.replace(".", "_").replace("-", "_")
    return base / "WebParser" / "BrowserProfiles" / site_slug / browser_slug


def archive_yandex_saved_tabs(profile_dir: Path) -> None:
    """Keep old tabs recoverable while preventing Yandex's stalled restore."""
    sessions = profile_dir / "Default" / "Sessions"
    if sessions.is_symlink():
        raise BrowserAccessError(f"Недопустимый каталог сохранённых вкладок: {sessions}")
    if not sessions.exists():
        return
    if not sessions.is_dir() or not sessions.resolve().is_relative_to(profile_dir.resolve()):
        raise BrowserAccessError(f"Недопустимый каталог сохранённых вкладок: {sessions}")
    archive_dir = profile_dir / "WebParserSavedSessions"
    try:
        archive_dir.mkdir(exist_ok=True)
        if not archive_dir.resolve().is_relative_to(profile_dir.resolve()):
            raise BrowserAccessError(f"Недопустимый каталог архива вкладок: {archive_dir}")
        sessions.rename(archive_dir / uuid4().hex)
    except OSError as error:
        raise BrowserAccessError(
            f"Не удалось сохранить прежние вкладки Яндекс.Браузера: {error}"
        ) from error


def _profile_locked(error: PlaywrightError) -> bool:
    """Recognize a browser process that cannot use an occupied profile."""
    message = str(error).casefold()
    if "profile in use" in message or "profile is in use" in message:
        return True
    if "singletonlock" in message:
        return True
    return "gpupersistentcache" in message and any(
        clue in message for clue in (
            "занят другим процессом", "используется другим процессом",
            "being used by another process", "0x20",
        )
    )


def retry_yandex_profile(error: PlaywrightError) -> bool:
    """Yandex can exit on a locked profile without a readable lock message."""
    message = str(error).casefold()
    return _profile_locked(error) or (
        "launch_persistent_context" in message
        and "target page, context or browser has been closed" in message
    )
