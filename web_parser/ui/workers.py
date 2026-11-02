"""Background workers for browser walks and source JSON downloads."""

from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from urllib.parse import quote, quote_plus

from PySide6.QtCore import QThread, Signal

from web_parser.browser_access import (
    BrowserAccessCancelled, BrowserAccessDenied, BrowserAccessError,
    BrowserPageAccess, browser_profile_dir,
)
from web_parser.browser_discovery import BrowserInstallation
from web_parser.images import ImageDownloadCancelled, ImageStore, SavedImage
from web_parser.ozon import OzonError, OzonProduct, parse_ozon_search
from web_parser.product_links import ProductLinks, ProductLinksError, fetch_product_links
from web_parser.wildberries import (
    WildberriesClient, WildberriesError, WildberriesProduct,
)
from web_parser.yandex_market import YandexMarketError, YandexMarketProduct, parse_yandex_search


class BrowserWalkThread(QThread):
    """Visit search pages and collect the first six marketplace products."""

    page_started = Signal(str, str, str, int, int)
    page_loaded = Signal(str, str, int, int, int)
    page_failed = Signal(str, str, str, int, int, str)
    ozon_parsed = Signal(str, object)
    wildberries_parsed = Signal(str, object)
    yandex_parsed = Signal(str, object)
    image_failed = Signal(str, str, str, str)
    ozon_recovery_started = Signal(str)

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
        self.ozon_results: dict[str, tuple[OzonProduct, ...]] = {}
        self.wildberries_results: dict[str, tuple[WildberriesProduct, ...]] = {}
        self.yandex_results: dict[str, tuple[YandexMarketProduct, ...]] = {}
        self.saved_images: dict[tuple[str, str], tuple[SavedImage, ...]] = {}
        self.image_failures = 0
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
            ) as wildberries, ExitStack() as recovery:
                images = ImageStore(
                    lambda: access.page.request,
                    cancel_requested=self._stop_event.is_set,
                    sleep=self._pause,
                )
                ozon_access: BrowserPageAccess | None = None
                ozon_images: ImageStore | None = None
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
                                try:
                                    document = access.fetch_wildberries_json(url)
                                except BrowserAccessCancelled:
                                    raise
                                except BrowserAccessDenied:
                                    raise
                                except BrowserAccessError as browser_error:
                                    try:
                                        parsed = wildberries.fetch(url)
                                    except WildberriesError:
                                        raise browser_error from None
                                else:
                                    parsed = wildberries.parse_document(document)
                                self.wildberries_results[product.product_id] = parsed
                                self.wildberries_parsed.emit(product.product_id, parsed)
                                parsed_products = parsed
                                status = 200
                            else:
                                marketplace_access = (ozon_access or access) if marketplace == "Ozon" else access
                                try:
                                    snapshot = marketplace_access.fetch(url)
                                except BrowserAccessDenied as error:
                                    if marketplace != "Ozon" or error.status != 403 or ozon_access is not None:
                                        raise
                                    if self.stop_requested:
                                        raise BrowserAccessCancelled(
                                            "Обход страниц остановлен пользователем."
                                        ) from error
                                    self.ozon_recovery_started.emit(product.product_id)
                                    try:
                                        fresh_profile = recovery.enter_context(
                                            TemporaryDirectory(prefix="web-parser-ozon-", ignore_cleanup_errors=True)
                                        )
                                    except OSError as profile_error:
                                        raise BrowserAccessError(
                                            "Не удалось создать временный профиль Ozon."
                                        ) from profile_error
                                    fresh_access = recovery.enter_context(BrowserPageAccess(
                                        self.installation, self.pause_seconds, headless=False,
                                        sleep=self._pause, cancel_requested=self._stop_event.is_set,
                                        profile_dir=Path(fresh_profile), proxy=self.proxy,
                                        playwright=getattr(access, "playwright", None),
                                    ))
                                    ozon_access = fresh_access
                                    ozon_images = ImageStore(
                                        lambda: fresh_access.page.request,
                                        cancel_requested=self._stop_event.is_set,
                                        sleep=self._pause,
                                    )
                                    marketplace_access = fresh_access
                                    snapshot = marketplace_access.fetch(url)
                                status = snapshot.status
                                if marketplace == "Ozon":
                                    parsed_ozon = parse_ozon_search(marketplace_access)
                                    self.ozon_results[product.product_id] = parsed_ozon
                                    self.ozon_parsed.emit(product.product_id, parsed_ozon)
                                    parsed_products = parsed_ozon
                                else:
                                    parsed_yandex = parse_yandex_search(access)
                                    self.yandex_results[product.product_id] = parsed_yandex
                                    self.yandex_parsed.emit(product.product_id, parsed_yandex)
                                    parsed_products = parsed_yandex
                            image_store = ozon_images if marketplace == "Ozon" and ozon_images is not None else images
                            batch = image_store.save_products(
                                self.site, product.product_id, marketplace, parsed_products, url,
                            )
                            self.saved_images[(product.product_id, marketplace)] = batch.saved
                            for failure in batch.failed:
                                self.image_failures += 1
                                self.image_failed.emit(
                                    product.product_id, marketplace, failure.source_url,
                                    f"Фото №{failure.position}: {failure.reason}",
                                )
                        except (BrowserAccessCancelled, ImageDownloadCancelled):
                            self.cancelled = True
                            return
                        except (
                            BrowserAccessError, OzonError, WildberriesError, YandexMarketError,
                        ) as error:
                            self.failed += 1
                            self.page_failed.emit(
                                product.product_id, marketplace, url, index, total, str(error),
                            )
                        else:
                            self.loaded += 1
                            self.page_loaded.emit(
                                product.product_id, marketplace, index, total, status,
                            )
        except (BrowserAccessError, OzonError, WildberriesError, YandexMarketError) as error:
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
