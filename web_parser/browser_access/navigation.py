"""Navigation and error handling shared by marketplace page loaders."""

from contextlib import suppress
import re
from typing import Literal
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError, Page, Response

from web_parser.browser_access.checks import AUTH_OR_RATE_LIMIT_STATUSES, is_ozon_url
from web_parser.browser_access.models import (
    BrowserAccessDenied, BrowserFetchFailed, BrowserLoginRequired, BrowserProxyAuthenticationRequired,
    BrowserProxyConnectionFailed, BrowserSessionClosed,
)
from web_parser.browser_access.session import BrowserSession
from web_parser.network_errors import browser_context_is_closed
from web_parser.socks5_relay import responds_to_socks5_greeting


_NAVIGATION_TIMEOUT_MS = 30_000
# Playwright can leave the API name blank while retaining the colon separator.
_NAVIGATION_ERROR_PREFIX = r"(?:(?:Page\.goto)?:[ \t]*)?"
_PROXY_CONNECTION_ERROR = re.compile(
    rf"^{_NAVIGATION_ERROR_PREFIX}(?:net::ERR_PROXY_CONNECTION_FAILED|NS_ERROR_PROXY_CONNECTION_REFUSED)(?:\s|$)"
)
_SOCKS_CONNECTION_ERROR = re.compile(
    rf"^{_NAVIGATION_ERROR_PREFIX}net::ERR_SOCKS_CONNECTION_FAILED(?:\s|$)"
)
_HTTP_RESPONSE_CODE_FAILURE = "net::ERR_HTTP_RESPONSE_CODE_FAILURE"
_PROXY_CONNECT_TIMEOUT_SECONDS = 2


class BrowserNavigation(BrowserSession):
    """Navigate the current tab while preserving its proxy and session state."""

    def _begin_navigation(self) -> None:
        self._check_cancelled()
        if self._navigated_once:
            self._sleep(self.pause_seconds)
            self._check_cancelled()
        self._navigated_once = True

    def _navigate(
        self, page: Page, url: str, *, wait_until: Literal["domcontentloaded", "load"],
    ) -> Response | None:
        observe_status = is_ozon_url(url)
        navigation_status: int | None = None

        def capture_status(response: Response) -> None:
            nonlocal navigation_status
            request = response.request
            with suppress(PlaywrightError):
                if request.is_navigation_request() and request.frame == page.main_frame:
                    navigation_status = response.status if is_ozon_url(response.url) else None

        if observe_status:
            page.on("response", capture_status)
        try:
            return page.goto(url, wait_until=wait_until, timeout=_NAVIGATION_TIMEOUT_MS)
        except PlaywrightError as error:
            self._check_browser_interruption(error)
            if self._proxy_connection_failed(error):
                self._check_cancelled()
                raise BrowserProxyConnectionFailed("Не удалось установить соединение с прокси.") from error
            message = str(error)
            if navigation_status == 407 and (
                _HTTP_RESPONSE_CODE_FAILURE in message or "net::ERR_INVALID_AUTH_CREDENTIALS" in message
            ):
                raise BrowserProxyAuthenticationRequired() from error
            if (
                navigation_status is not None and 400 <= navigation_status < 600
                and _HTTP_RESPONSE_CODE_FAILURE in message
            ):
                self._check_cancelled()
                message = (
                    f"Ozon вернул HTTP {navigation_status} для страницы, "
                    "и браузер не смог её открыть (ERR_HTTP_RESPONSE_CODE_FAILURE)."
                )
                if navigation_status == 408 or navigation_status >= 500:
                    raise PlaywrightError(message) from error
                raise BrowserFetchFailed(message) from error
            raise
        finally:
            if observe_status:
                with suppress(PlaywrightError):
                    page.remove_listener("response", capture_status)

    def _proxy_connection_failed(self, error: PlaywrightError) -> bool:
        if not self._strict_access or self.proxy is None:
            return False
        message = str(error)
        if _PROXY_CONNECTION_ERROR.match(message):
            return True
        if not _SOCKS_CONNECTION_ERROR.match(message):
            return False

        endpoint = urlsplit(self.proxy["server"])
        if endpoint.scheme != "socks5" or endpoint.hostname is None or endpoint.port is None:
            return False
        # A reachable TCP port is insufficient. Chromium can report this error
        # before negotiation or after the proxy rejects a destination.
        self._check_cancelled()
        return not responds_to_socks5_greeting(
            (endpoint.hostname, endpoint.port),
            authenticated=bool(self.proxy.get("username")),
            timeout=_PROXY_CONNECT_TIMEOUT_SECONDS,
        )

    def _check_access_response(self, page: Page, status: int) -> None:
        if status == 407:
            raise BrowserProxyAuthenticationRequired()
        if not self._strict_access:
            return
        if status in AUTH_OR_RATE_LIMIT_STATUSES:
            raise BrowserAccessDenied(status)
        path = urlsplit(page.url).path.lower().rstrip("/")
        if path in ("/login", "/signin", "/sign-in", "/auth/login"):
            raise BrowserLoginRequired(status, "страница перенаправила на вход в аккаунт.")

    def _check_browser_interruption(self, error: PlaywrightError) -> None:
        self._check_cancelled()
        if browser_context_is_closed(error):
            raise BrowserSessionClosed("Браузер или его контекст закрыт.") from error
