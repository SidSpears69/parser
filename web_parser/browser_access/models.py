"""Page snapshots and errors returned by browser access."""

from dataclasses import dataclass


class BrowserAccessError(RuntimeError):
    """The selected browser could not return a usable page."""


class BrowserFetchFailed(BrowserAccessError):
    """Navigation failed without a usable page."""


class BrowserProxyConnectionFailed(BrowserFetchFailed):
    """The browser could not connect to the configured proxy server."""


class BrowserProxyAuthenticationRequired(BrowserFetchFailed):
    """The configured proxy returned HTTP 407."""

    def __init__(self) -> None:
        super().__init__("Прокси запросил авторизацию (HTTP 407). Проверьте его настройки и реквизиты.")


class BrowserSessionClosed(BrowserAccessError):
    """The current browser session cannot handle any more requests."""


class BrowserAccessCancelled(BrowserAccessError):
    """The user stopped the page sequence."""


class BrowserAccessDenied(BrowserAccessError):
    """The site did not provide a usable page after an access check."""

    def __init__(self, status: int | None, reason: str | None = None) -> None:
        self.status = status
        detail = reason or "страница не прошла проверку доступа."
        super().__init__(f"HTTP {status}: {detail}" if status is not None else detail)


class BrowserCaptchaDenied(BrowserAccessDenied):
    """The page requires a CAPTCHA before granting access."""


class BrowserLoginRequired(BrowserAccessDenied):
    """The requested page redirected to a sign-in page."""


class YandexCaptchaDenied(BrowserCaptchaDenied):
    """Yandex Market requires a captcha check before granting access."""

    def __init__(
        self, status: int | None, reason: str | None = None, *,
        attempts: int = 0, solved: bool = False,
    ) -> None:
        super().__init__(status, reason)
        self.attempts = attempts
        self.solved = solved


@dataclass(frozen=True, slots=True)
class PageSnapshot:
    requested_url: str
    final_url: str
    status: int
    html: str
    attempts: int
