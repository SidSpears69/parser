"""Background workers for browser walks and source JSON downloads."""

from collections import deque
from contextlib import ExitStack
from threading import Event
from urllib.parse import quote, quote_plus, urlsplit

from PySide6.QtCore import QThread, Signal

from web_parser.browser_access import (
    BrowserAccessCancelled, BrowserAccessDenied, BrowserAccessError, BrowserCaptchaDenied,
    BrowserLoginRequired, BrowserPageAccess, BrowserProxyAuthenticationRequired,
    BrowserProxyConnectionFailed, BrowserSessionClosed, YandexCaptchaDenied, browser_profile_dir,
)
from web_parser.browser_discovery import BrowserInstallation
from web_parser.ftp_export import FTPExportCancelled, FTPExportError, FTPExportResult, export_run
from web_parser.images import ImageDownloadCancelled, ImageRequestContextClosed, ImageStore, SavedImage
from web_parser.ozon import OzonError, OzonLayoutError, OzonProduct, parse_ozon_search
from web_parser.product_links import ProductLinks, ProductLinksError, fetch_product_links
from web_parser.proxy_pool import MARKETPLACES, ProxyPool, WalkProxyRoutes
from web_parser.wildberries import (
    WildberriesClient, WildberriesError, WildberriesLayoutError, WildberriesProduct,
)
from web_parser.yandex_market import (
    YandexMarketError, YandexMarketLayoutError, YandexMarketProduct, parse_yandex_search,
)


class BrowserWalkThread(QThread):
    """Visit search pages and collect the first six marketplace products."""

    page_started = Signal(str, str, str, int, int)
    page_loaded = Signal(str, str, int, int, int, bool)
    page_failed = Signal(str, str, str, int, int, str)
    layout_notice = Signal(str, str, str, str)
    layout_failure = Signal(str, str, str, str)
    ozon_parsed = Signal(str, object)
    wildberries_parsed = Signal(str, object)
    yandex_parsed = Signal(str, object)
    image_failed = Signal(str, str, str, str)
    route_changed = Signal(str, str, int)
    ftp_upload_started = Signal()
    browser_notice = Signal(str)

    def __init__(
        self, site: str, installation: BrowserInstallation, links: tuple[ProductLinks, ...],
        pause_seconds: int, parent=None, *, proxy: dict[str, str] | None = None,
        proxy_routes: WalkProxyRoutes | None = None,
        ftp_settings: dict[str, object] | None = None,
        headless: bool = False,
    ) -> None:
        super().__init__(parent)
        self.site = site
        self.installation = installation
        self.links = links
        self.pause_seconds = pause_seconds
        self.headless = headless
        if proxy_routes is None:
            slots = []
            if proxy is not None:
                slots.append({key: proxy.get(key, "") for key in ("server", "username", "password")})
            proxy_routes = ProxyPool().routes_for_walk(slots)
        self.proxy_routes = proxy_routes
        self.ftp_settings = ftp_settings.copy() if ftp_settings is not None else None
        self.loaded = 0
        self.failed = 0
        self.skipped = 0
        self.cancelled = False
        self.error: str | None = None
        self.fatal_layout: str | None = None
        self.ozon_results: dict[str, tuple[OzonProduct, ...]] = {}
        self.wildberries_results: dict[str, tuple[WildberriesProduct, ...]] = {}
        self.yandex_results: dict[str, tuple[YandexMarketProduct, ...]] = {}
        self.saved_images: dict[tuple[str, str], tuple[SavedImage, ...]] = {}
        self.image_failures = 0
        self.ftp_result: FTPExportResult | None = None
        self.ftp_error: str | None = None
        self.ftp_skip_reason: str | None = None
        self._stop_event = Event()

    @property
    def ftp_configured(self) -> bool:
        return self.ftp_settings is not None and bool(str(self.ftp_settings.get("host", "")).strip())

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def stop(self) -> None:
        self._stop_event.set()

    def _pause(self, seconds: float) -> None:
        self._stop_event.wait(seconds)

    def _failure_message(self, error: Exception, attempted_routes: list[int]) -> str:
        message = str(error) or type(error).__name__
        if not attempted_routes:
            return message
        labels = []
        for route in attempted_routes:
            proxy = self.proxy_routes.proxies[route]
            if proxy is None:
                labels.append("напрямую")
            else:
                scheme = urlsplit(proxy["server"]).scheme.upper()
                labels.append(f"прокси №{route} ({scheme})")
        return f"Маршрут: {' → '.join(labels)}. {message}"

    def run(self) -> None:
        total = len(self.links) * len(MARKETPLACES)
        index = 0
        layout_context: tuple[str, str, str] | None = None
        reported_layouts: set[tuple[str, str]] = set()

        def report_layout_change(layout_message: str) -> None:
            if layout_context is not None:
                signature = (layout_context[1], layout_message)
                if signature not in reported_layouts:
                    reported_layouts.add(signature)
                    self.layout_notice.emit(*layout_context, layout_message)

        try:
            with ExitStack() as resources:
                browsers: dict[int, BrowserPageAccess] = {}
                image_stores: dict[int, ImageStore] = {}
                wildberries_clients: dict[int, WildberriesClient] = {}
                preferred_routes = {
                    marketplace: candidates[0]
                    for marketplace, candidates in self.proxy_routes.candidates.items()
                }
                stopped_marketplaces: set[str] = set()

                def open_browser(route: int) -> BrowserPageAccess:
                    if route not in browsers:
                        profile = browser_profile_dir(self.site, self.installation.name)
                        if route:
                            profile = profile.with_name(f"{profile.name}-proxy-{route}")
                        first = next(iter(browsers.values()), None)
                        if first is not None:
                            self._pause(self.pause_seconds)
                            if self.stop_requested:
                                raise BrowserAccessCancelled("Обход страниц остановлен пользователем.")
                        access = resources.enter_context(BrowserPageAccess(
                            self.installation, self.pause_seconds, headless=self.headless,
                            sleep=self._pause, cancel_requested=self._stop_event.is_set,
                            profile_dir=profile, proxy=self.proxy_routes.proxies[route],
                            playwright=getattr(first, "playwright", None), strict_access=True,
                        ))
                        browsers[route] = access
                        image_stores[route] = ImageStore(
                            lambda: access.page.request, cancel_requested=self._stop_event.is_set,
                            sleep=self._pause,
                        )
                        notice = getattr(access, "launch_notice", None)
                        if notice:
                            self.browser_notice.emit(notice)
                    return browsers[route]

                def load_wildberries(
                    route: int, access: BrowserPageAccess, listing_url: str,
                ) -> tuple[WildberriesProduct, ...]:
                    if route not in wildberries_clients:
                        wildberries_clients[route] = resources.enter_context(WildberriesClient(
                            proxy=getattr(access, "effective_proxy", self.proxy_routes.proxies[route]),
                            playwright=getattr(access, "playwright", None),
                            on_layout_change=report_layout_change,
                        ))
                    client = wildberries_clients[route]
                    try:
                        document = access.fetch_wildberries_json(listing_url)
                    except (
                        BrowserAccessCancelled, BrowserAccessDenied, BrowserProxyAuthenticationRequired,
                        BrowserProxyConnectionFailed, BrowserSessionClosed,
                    ):
                        raise
                    except BrowserAccessError as browser_error:
                        try:
                            return client.fetch(listing_url)
                        except WildberriesError:
                            raise browser_error from None
                    return client.parse_document(document)

                for product in self.links:
                    for marketplace, url in zip(MARKETPLACES, (
                        product.ozon_url, product.wildberries_url, product.yandex_market_url,
                    ), strict=True):
                        if self.stop_requested:
                            self.cancelled = True
                            return
                        index += 1
                        if marketplace in stopped_marketplaces:
                            self.skipped += 1
                            continue
                        layout_context = (product.product_id, marketplace, url)
                        self.page_started.emit(product.product_id, marketplace, url, index, total)
                        preferred = preferred_routes[marketplace]
                        ordered_routes = (
                            preferred, *(route for route in self.proxy_routes.candidates[marketplace]
                                         if route != preferred),
                        )
                        if marketplace == "Яндекс Маркет":
                            ordered_routes = (*filter(lambda route: route != 0, ordered_routes), 0)
                        candidates = deque(ordered_routes)
                        attempted_routes: list[int] = []
                        try:
                            route_error: BrowserAccessError | None = None
                            while candidates:
                                route = candidates[0]
                                attempted_routes.append(route)
                                try:
                                    page_access = open_browser(route)
                                    if marketplace == "Wildberries":
                                        parsed_products = load_wildberries(route, page_access, url)
                                        status = 200
                                    elif marketplace == "Яндекс Маркет":
                                        remaining_attempts = 3
                                        while True:
                                            snapshot = page_access.fetch(url, attempts=remaining_attempts)
                                            remaining_attempts -= snapshot.attempts
                                            status = snapshot.status
                                            try:
                                                parsed_products = parse_yandex_search(
                                                    page_access, on_layout_change=report_layout_change,
                                                )
                                            except YandexCaptchaDenied:
                                                if remaining_attempts <= 0:
                                                    raise
                                                continue
                                            break
                                    else:
                                        status = page_access.fetch(url).status
                                except (BrowserProxyConnectionFailed, BrowserProxyAuthenticationRequired) as error:
                                    if route == 0:
                                        raise
                                    route_error = error
                                    candidates.popleft()
                                    if candidates:
                                        if isinstance(error, BrowserProxyAuthenticationRequired):
                                            self.browser_notice.emit(
                                                f"{marketplace}, товар {product.product_id}, прокси №{route}. {error}"
                                            )
                                        self.route_changed.emit(product.product_id, marketplace, candidates[0])
                                    continue
                                except YandexCaptchaDenied as error:
                                    route_error = error
                                    candidates.popleft()
                                    if candidates:
                                        self.route_changed.emit(product.product_id, marketplace, candidates[0])
                                    continue
                                except BrowserAccessDenied as error:
                                    if error.status == 429 or isinstance(error, BrowserCaptchaDenied):
                                        route_error = error
                                        candidates.popleft()
                                        if candidates:
                                            self.route_changed.emit(product.product_id, marketplace, candidates[0])
                                        continue
                                    if error.status in (401, 403, 498) or isinstance(error, BrowserLoginRequired):
                                        stopped_marketplaces.add(marketplace)
                                    raise
                                break
                            else:
                                assert route_error is not None
                                raise route_error

                            if marketplace == "Ozon":
                                parsed_products = parse_ozon_search(page_access, on_layout_change=report_layout_change)
                                self.ozon_results[product.product_id] = parsed_products
                                self.ozon_parsed.emit(product.product_id, parsed_products)
                            elif marketplace == "Wildberries":
                                self.wildberries_results[product.product_id] = parsed_products
                                self.wildberries_parsed.emit(product.product_id, parsed_products)
                            else:
                                self.yandex_results[product.product_id] = parsed_products
                                self.yandex_parsed.emit(product.product_id, parsed_products)

                            self.proxy_routes.remember(marketplace, route)
                            preferred_routes[marketplace] = route
                            batch = image_stores[route].save_products(
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
                        except (BrowserSessionClosed, ImageRequestContextClosed) as error:
                            raise BrowserAccessError(self._failure_message(error, attempted_routes)) from error
                        except YandexCaptchaDenied as error:
                            self.failed += 1
                            self.page_failed.emit(
                                product.product_id, marketplace, url, index, total,
                                self._failure_message(error, attempted_routes),
                            )
                        except (OzonLayoutError, WildberriesLayoutError, YandexMarketLayoutError) as error:
                            self.failed += 1
                            self.fatal_layout = str(error)
                            self.layout_failure.emit(product.product_id, marketplace, url, str(error))
                            return
                        except (
                            BrowserAccessError, OzonError, WildberriesError, YandexMarketError,
                        ) as error:
                            message = self._failure_message(error, attempted_routes)
                            if not browsers:
                                raise BrowserAccessError(message) from error
                            self.failed += 1
                            self.page_failed.emit(
                                product.product_id, marketplace, url, index, total, message,
                            )
                        else:
                            self.loaded += 1
                            self.page_loaded.emit(
                                product.product_id, marketplace, index, total, status, route == 0,
                            )
            self._upload_results(total)
        except (BrowserAccessError, OzonError, WildberriesError, YandexMarketError) as error:
            if self.stop_requested:
                self.cancelled = True
            else:
                self.error = str(error) or type(error).__name__

    def _upload_results(self, total: int) -> None:
        if self.stop_requested:
            self.cancelled = True
            return
        settings = self.ftp_settings
        if settings is None or not self.ftp_configured:
            return
        # Publication replaces the entire snapshot, including its photo references.
        if self.loaded != total or self.image_failures:
            self.ftp_skip_reason = (
                f"Загружено страниц: {self.loaded} из {total}. "
                f"Ошибок загрузки фото: {self.image_failures}."
            )
            return
        results = {
            "Ozon": self.ozon_results,
            "Wildberries": self.wildberries_results,
            "Яндекс Маркет": self.yandex_results,
        }
        if not any(products for marketplace in results.values() for products in marketplace.values()):
            self.ftp_skip_reason = "Нет собранных товаров."
            return
        self.ftp_upload_started.emit()
        try:
            self.ftp_result = export_run(
                self.site, self.links, results, self.saved_images, settings,
                cancel_requested=self._stop_event.is_set,
            )
        except FTPExportCancelled:
            self.cancelled = True
        except FTPExportError as error:
            self.ftp_error = str(error)


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
