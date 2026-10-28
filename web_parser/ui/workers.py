"""Background workers for browser walks and source JSON downloads."""

from threading import Event
from urllib.parse import quote, quote_plus

from PySide6.QtCore import QThread, Signal

from web_parser.browser_access import (
    BrowserAccessCancelled, BrowserAccessError, BrowserPageAccess, browser_profile_dir,
)
from web_parser.browser_discovery import BrowserInstallation
from web_parser.product_links import ProductLinks, ProductLinksError, fetch_product_links
from web_parser.wildberries import (
    WildberriesClient, WildberriesError, WildberriesHTTPError, WildberriesProduct,
    WildberriesRequestError,
)

class BrowserWalkThread(QThread):
    """Visit browser pages and read Wildberries directly from storefront JSON."""

    page_started = Signal(str, str, str, int, int)
    page_loaded = Signal(str, str, int, int, int)
    page_failed = Signal(str, str, str, int, int, str)
    wildberries_parsed = Signal(str, object)

    def __init__(
        self, site: str, installation: BrowserInstallation, links: tuple[ProductLinks, ...],
        pause_seconds: int, parent=None, *, proxy: dict[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.site = site
        self.installation = installation
        self.links = links
        self.pause_seconds = pause_seconds
        self.proxy = proxy.copy() if proxy is not None else None
        self.loaded = 0
        self.failed = 0
        self.cancelled = False
        self.error: str | None = None
        self.wildberries_results: dict[str, tuple[WildberriesProduct, ...]] = {}
        self._stop_event = Event()

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def stop(self) -> None:
        self._stop_event.set()

    def _pause(self, seconds: float) -> None:
        self._stop_event.wait(seconds)

    def run(self) -> None:
        total = len(self.links) * 3
        index = 0
        try:
            with BrowserPageAccess(
                self.installation, self.pause_seconds, headless=False,
                sleep=self._pause, cancel_requested=self._stop_event.is_set,
                profile_dir=browser_profile_dir(self.site, self.installation.name),
                proxy=self.proxy,
            ) as access, WildberriesClient(
                proxy=getattr(access, "effective_proxy", self.proxy),
                playwright=getattr(access, "playwright", None),
            ) as wildberries:
                for product in self.links:
                    for marketplace, url in (
                        ("Ozon", product.ozon_url),
                        ("Wildberries", product.wildberries_url),
                        ("Яндекс Маркет", product.yandex_market_url),
                    ):
                        if self.stop_requested:
                            self.cancelled = True
                            return
                        index += 1
                        self.page_started.emit(product.product_id, marketplace, url, index, total)
                        try:
                            if marketplace == "Wildberries":
                                self._pause(self.pause_seconds)
                                if self.stop_requested:
                                    self.cancelled = True
                                    return
                                try:
                                    parsed = wildberries.fetch(url)
                                except (WildberriesHTTPError, WildberriesRequestError) as error:
                                    if error.label != "Поиск Wildberries" or (
                                        isinstance(error, WildberriesHTTPError)
                                        and error.status not in (403, 498)
                                    ):
                                        raise
                                    document = access.fetch_wildberries_json(url)
                                    parsed = wildberries.parse_document(document)
                                self.wildberries_results[product.product_id] = parsed
                                self.wildberries_parsed.emit(product.product_id, parsed)
                                status = 200
                            else:
                                snapshot = access.fetch(url)
                                status = snapshot.status
                        except BrowserAccessCancelled:
                            self.cancelled = True
                            return
                        except (BrowserAccessError, WildberriesError) as error:
                            self.failed += 1
                            self.page_failed.emit(
                                product.product_id, marketplace, url, index, total, str(error),
                            )
                        else:
                            self.loaded += 1
                            self.page_loaded.emit(
                                product.product_id, marketplace, index, total, status,
                            )
        except (BrowserAccessError, WildberriesError) as error:
            if self.stop_requested:
                self.cancelled = True
            else:
                self.error = str(error) or type(error).__name__


class ProductLinksFetchThread(QThread):
    """Fetch the site's source JSON without blocking the Qt event loop."""

    def __init__(self, source_url: str, token: str, parent=None):
        super().__init__(parent)
        self.source_url = source_url
        self.token = token
        self.links: list[ProductLinks] | None = None
        self.error: str | None = None

    def run(self) -> None:
        try:
            self.links = fetch_product_links(self.source_url, self.token)
        except ProductLinksError as error:
            message = str(error) or type(error).__name__
            # Avoid exposing the credential even if a lower layer includes it
            # in an error message or in an URL-encoded query parameter.
            for secret in {self.token, quote(self.token, safe=""), quote_plus(self.token)}:
                if secret:
                    message = message.replace(secret, "[токен скрыт]")
            self.error = message
