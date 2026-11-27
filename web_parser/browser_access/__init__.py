"""Playwright access to marketplace pages in the selected browser."""

from web_parser.browser_access.models import (
    BrowserAccessCancelled, BrowserAccessDenied, BrowserAccessError, BrowserCaptchaDenied,
    BrowserFetchFailed, BrowserLoginRequired, BrowserProxyAuthenticationRequired,
    BrowserProxyConnectionFailed, BrowserSessionClosed, PageSnapshot, YandexCaptchaDenied,
)
from web_parser.browser_access.page import BrowserPageAccess
from web_parser.browser_access.profiles import browser_profile_dir

__all__ = [
    "BrowserAccessCancelled",
    "BrowserAccessDenied",
    "BrowserAccessError",
    "BrowserCaptchaDenied",
    "BrowserFetchFailed",
    "BrowserLoginRequired",
    "BrowserPageAccess",
    "BrowserProxyAuthenticationRequired",
    "BrowserProxyConnectionFailed",
    "BrowserSessionClosed",
    "PageSnapshot",
    "YandexCaptchaDenied",
    "browser_profile_dir",
]
