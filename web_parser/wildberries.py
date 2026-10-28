"""Read Wildberries search results from the storefront JSON, without a browser."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
from urllib.parse import parse_qs, urlencode, urlsplit

from playwright.sync_api import APIRequestContext, Error as PlaywrightError
from playwright.sync_api import Playwright, sync_playwright


_SEARCH_ENDPOINT = "https://www.wildberries.ru/__internal/u-search/exactmatch/ru/common/v18/search"
_CDN_ROUTES_URL = "https://cdn.wbbasket.ru/api/v3/upstreams"
_DEFAULT_DEST = "1259570991"
_TIMEOUT_MS = 30_000
_MAX_JSON_BYTES = 10 * 1024 * 1024
_RESULT_LIMIT = 6


class WildberriesError(RuntimeError):
    """The storefront JSON is unavailable or does not contain usable products."""


class WildberriesHTTPError(WildberriesError):
    def __init__(self, label: str, status: int) -> None:
        self.label = label
        self.status = status
        super().__init__(f"{label}: HTTP {status}.")


class WildberriesRequestError(WildberriesError):
    """The HTTP transport failed before yielding a usable status and body."""

    def __init__(self, label: str) -> None:
        self.label = label
        super().__init__(f"{label}: не удалось получить ответ.")


@dataclass(frozen=True, slots=True)
class WildberriesProduct:
    wb_id: int
    image_url: str
    price: Decimal
    discounted_price: Decimal
    name: str
    rating: Decimal | None
    reviews: int


@dataclass(frozen=True, slots=True)
class _BasketRoute:
    first_volume: int
    last_volume: int
    host: str


def search_api_url(search_url: str) -> str:
    """Translate a product-feed search URL to the matching JSON request."""
    try:
        parts = urlsplit(search_url)
    except ValueError as error:
        raise WildberriesError("Ожидается HTTPS-ссылка на поисковую выдачу Wildberries.") from error
    if (parts.scheme != "https" or parts.hostname not in {"wildberries.ru", "www.wildberries.ru"}
            or parts.username or parts.password or not parts.path.lower().endswith("/search.aspx")):
        raise WildberriesError("Ожидается HTTPS-ссылка на поисковую выдачу Wildberries.")
    parameters = parse_qs(parts.query)
    query = (parameters.get("search") or [""])[0].strip()
    if not query:
        raise WildberriesError("В ссылке Wildberries отсутствует поисковый запрос.")
    dest = (parameters.get("dest") or [_DEFAULT_DEST])[0]
    if not dest.lstrip("-").isdigit():
        raise WildberriesError("В ссылке Wildberries указан неверный регион доставки.")
    return _SEARCH_ENDPOINT + "?" + urlencode({
        "ab_testing": "false", "appType": "1", "curr": "rub", "dest": dest,
        "hide_vflags": "4294967296", "inheritFilters": "true", "lang": "ru",
        "locale": "ru", "query": query, "resultset": "catalog", "sort": "popular",
        "spp": "30", "suppressSpellcheck": "false",
    })


def _basket_routes(document: object) -> tuple[_BasketRoute, ...]:
    if not isinstance(document, dict):
        raise WildberriesError("Wildberries изменил формат маршрутов изображений.")
    origin = document.get("origin")
    if not isinstance(origin, dict):
        raise WildberriesError("Wildberries изменил формат маршрутов изображений.")
    route_maps = origin.get("mediabasket_route_map")
    if not isinstance(route_maps, list):
        raise WildberriesError("Wildberries изменил формат маршрутов изображений.")
    range_map = next(
        (item for item in route_maps if isinstance(item, dict) and item.get("method") == "range"),
        None,
    )
    hosts = range_map.get("hosts") if range_map is not None else None
    if not isinstance(hosts, list):
        raise WildberriesError("Wildberries изменил формат маршрутов изображений.")
    routes: list[_BasketRoute] = []
    for item in hosts:
        if not isinstance(item, dict):
            raise WildberriesError("Wildberries вернул неверные маршруты изображений.")
        first = item.get("vol_range_from")
        last = item.get("vol_range_to")
        host = item.get("host")
        if (
            not isinstance(first, int) or isinstance(first, bool)
            or not isinstance(last, int) or isinstance(last, bool)
            or first < 0 or last < first
            or not isinstance(host, str)
            or not host.startswith("basket-") or not host.endswith(".wbbasket.ru")
        ):
            raise WildberriesError("Wildberries вернул неверные маршруты изображений.")
        routes.append(_BasketRoute(first, last, host))
    if not routes:
        raise WildberriesError("Wildberries вернул неверные маршруты изображений.")
    return tuple(routes)


def _money(kopecks: object, field: str) -> Decimal:
    if not isinstance(kopecks, int) or isinstance(kopecks, bool) or kopecks < 0:
        raise WildberriesError(f"Товар Wildberries: неверное поле цены «{field}».")
    return Decimal(kopecks) / 100


def _price_fields(product: dict) -> tuple[Decimal, Decimal]:
    sizes = product.get("sizes")
    if isinstance(sizes, list):
        for size in sizes:
            if isinstance(size, dict) and isinstance(size.get("price"), dict):
                price = size["price"]
                return _money(price.get("basic"), "basic"), _money(price.get("product"), "product")
    return _money(product.get("priceU"), "priceU"), _money(product.get("salePriceU"), "salePriceU")


def parse_search_json(document: object, routes_document: object) -> tuple[WildberriesProduct, ...]:
    """Extract the first six results and their primary photo URLs."""
    if not isinstance(document, dict):
        raise WildberriesError("Wildberries вернул неверный JSON выдачи.")
    products = document.get("products")
    data = document.get("data")
    if products is None and isinstance(data, dict):
        products = data.get("products")
    if not isinstance(products, list):
        raise WildberriesError("В JSON Wildberries отсутствует список товаров.")
    if not products:
        return ()
    routes = _basket_routes(routes_document)
    result = []
    for position, raw in enumerate(products[:_RESULT_LIMIT], 1):
        if not isinstance(raw, dict):
            raise WildberriesError(f"Товар Wildberries №{position}: неверный формат.")
        wb_id = raw.get("id")
        pics = raw.get("pics")
        name = raw.get("name")
        reviews = raw.get("feedbacks")
        rating = raw.get("reviewRating", raw.get("rating"))
        if (
            not isinstance(wb_id, int) or isinstance(wb_id, bool) or wb_id <= 0
            or not isinstance(pics, int) or isinstance(pics, bool) or pics < 1
        ):
            raise WildberriesError(f"Товар Wildberries №{position}: нет артикула или фото.")
        if not isinstance(name, str) or not name.strip():
            raise WildberriesError(f"Товар Wildberries №{position}: нет названия.")
        if not isinstance(reviews, int) or isinstance(reviews, bool) or reviews < 0:
            raise WildberriesError(f"Товар Wildberries №{position}: неверное число отзывов.")
        if rating is not None and (type(rating) not in (int, float) or not 0 <= rating <= 5):
            raise WildberriesError(f"Товар Wildberries №{position}: неверный рейтинг.")
        volume = wb_id // 100_000
        route = next((item for item in routes if item.first_volume <= volume <= item.last_volume), None)
        if route is None:
            raise WildberriesError(f"Товар Wildberries №{position}: не найден CDN для фото.")
        image_url = (
            f"https://{route.host}/vol{volume}/part{wb_id // 1000}/{wb_id}/images/c516x688/1.webp"
        )
        pics_update = raw.get("picsUpdate")
        if type(pics_update) is int and pics_update >= 0:
            image_url += f"?picsUpdate={pics_update}"
        price, discounted_price = _price_fields(raw)
        result.append(WildberriesProduct(
            wb_id, image_url, price, discounted_price, name.strip(),
            Decimal(str(rating)) if rating is not None else None, reviews,
        ))
    return tuple(result)


class WildberriesClient:
    """Use Playwright's HTTP client without starting a browser process."""

    def __init__(
        self, proxy: dict[str, str] | None = None, *, playwright: Playwright | None = None,
    ) -> None:
        self.proxy = proxy.copy() if proxy is not None else None
        self._playwright = playwright
        self._owns_playwright = playwright is None
        self._request: APIRequestContext | None = None
        self._routes: object | None = None

    def __enter__(self) -> WildberriesClient:
        try:
            if self._playwright is None:
                self._playwright = sync_playwright().start()
            options = {"proxy": self.proxy} if self.proxy is not None else {}
            self._request = self._playwright.request.new_context(
                extra_http_headers={
                    "Accept": "*/*", "Accept-Language": "ru-RU,ru;q=0.9",
                    "Referer": "https://www.wildberries.ru/",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
                    ),
                },
                **options,
            )
        except PlaywrightError as error:
            self.close()
            raise WildberriesError("Не удалось запустить HTTP-клиент Wildberries.") from error
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        request, self._request = self._request, None
        playwright, self._playwright = self._playwright, None
        self._routes = None
        first_error: PlaywrightError | None = None
        try:
            if request is not None:
                request.dispose()
        except PlaywrightError as error:
            first_error = error
        finally:
            if playwright is not None and self._owns_playwright:
                try:
                    playwright.stop()
                except PlaywrightError as error:
                    if first_error is None:
                        first_error = error
        if first_error is not None:
            raise WildberriesError("Не удалось закрыть HTTP-клиент Wildberries.") from first_error

    def _json(self, url: str, label: str) -> object:
        if self._request is None:
            raise WildberriesError("HTTP-клиент Wildberries не открыт.")
        try:
            response = self._request.get(url, timeout=_TIMEOUT_MS)
            if not response.ok:
                raise WildberriesHTTPError(label, response.status)
            body = response.body()
        except PlaywrightError as error:
            raise WildberriesRequestError(label) from error
        if len(body) > _MAX_JSON_BYTES:
            raise WildberriesError(f"{label}: ответ JSON слишком велик.")
        try:
            return json.loads(body.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise WildberriesError(f"{label}: неверный JSON.") from error

    def fetch(self, search_url: str) -> tuple[WildberriesProduct, ...]:
        document = self._json(search_api_url(search_url), "Поиск Wildberries")
        return self.parse_document(document)

    def parse_document(self, document: object) -> tuple[WildberriesProduct, ...]:
        """Parse JSON obtained by the existing browser when direct HTTP is denied."""
        if isinstance(document, dict):
            data = document.get("data")
            if document.get("products") == [] or (
                isinstance(data, dict) and data.get("products") == []
            ):
                return ()
        if self._routes is None:
            self._routes = self._json(_CDN_ROUTES_URL, "Маршруты фото Wildberries")
        return parse_search_json(document, self._routes)
