"""Read the first six Yandex Market products from a rendered search page."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from web_parser.browser_access import BrowserPageAccess


_RESULT_LIMIT = 6
_CARD_SELECTOR = '[data-zone-name="productSnippet"]'
_EXTRACT_CARDS = """(selector) => [...document.querySelectorAll(selector)].slice(0, 6)
    .map(card => {
        let details = {};
        try { details = JSON.parse(card.getAttribute('data-zone-data') || '{}'); }
        catch (_) { /* Validation below will report the incomplete card. */ }
        const discounts = Array.isArray(details.additionalPrices)
            ? details.additionalPrices : [];
        const discount = discounts.find(item => item.priceType === 'withDiscount');
        const rating = card.querySelector('[data-zone-name="rating"] .ds-rating__value');
        const ratingText = card.querySelector('[data-zone-name="rating"]')?.innerText || '';
        return {
            image_url: card.querySelector('[data-auto="galleryLink"] img')
                ?.getAttribute('src') ?? null,
            price: details.price ?? null,
            discounted_price: discount?.priceValue ?? details.price ?? null,
            name: card.querySelector('[data-auto="snippet-title"]')?.textContent ?? null,
            rating: rating?.textContent ?? null,
            reviews: ratingText.match(/\\((\\d[\\d\\s\\u00a0\\u2009\\u202f]*)\\)/)?.[1] ?? null,
        };
    })"""
_EMPTY_CHECK = """() => /ничего не найдено|товары не найдены|ничего не нашлось/i
    .test(document.body?.innerText || '')"""


class YandexMarketError(RuntimeError):
    """The search page does not contain usable product cards."""


@dataclass(frozen=True, slots=True)
class YandexMarketProduct:
    image_url: str
    price: Decimal
    discounted_price: Decimal
    name: str
    rating: Decimal | None
    reviews: int


def _money(value: object, position: int, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise YandexMarketError(f"Товар Яндекс Маркета №{position}: нет поля «{label}».")
    try:
        number = Decimal(str(value).replace("\u00a0", "").replace("\u202f", "").replace(" ", ""))
    except InvalidOperation as error:
        raise YandexMarketError(
            f"Товар Яндекс Маркета №{position}: неверное поле «{label}»."
        ) from error
    if not number.is_finite() or number < 0:
        raise YandexMarketError(f"Товар Яндекс Маркета №{position}: неверное поле «{label}».")
    return number


def _reviews(value: object, position: int) -> int:
    if value is None:
        return 0
    if not isinstance(value, str) or not re.fullmatch(r"[\d\s\u00a0\u2009\u202f]+", value):
        raise YandexMarketError(f"Товар Яндекс Маркета №{position}: неверное число оценок.")
    return int(re.sub(r"\s", "", value))


def parse_yandex_cards(raw_cards: object) -> tuple[YandexMarketProduct, ...]:
    """Validate browser extracted cards while retaining the order on the page."""
    if not isinstance(raw_cards, list):
        raise YandexMarketError("Яндекс Маркет изменил формат поисковой выдачи.")
    products: list[YandexMarketProduct] = []
    for position, card in enumerate(raw_cards[:_RESULT_LIMIT], 1):
        if not isinstance(card, dict):
            raise YandexMarketError(f"Товар Яндекс Маркета №{position}: неверный формат карточки.")
        image_url = card.get("image_url")
        name = card.get("name")
        if (not isinstance(image_url, str)
                or urlsplit(image_url).scheme != "https"
                or urlsplit(image_url).hostname != "avatars.mds.yandex.net"):
            raise YandexMarketError(f"Товар Яндекс Маркета №{position}: нет основного фото.")
        if not isinstance(name, str) or not name.strip():
            raise YandexMarketError(f"Товар Яндекс Маркета №{position}: нет названия.")
        price = _money(card.get("price"), position, "цена")
        discounted_price = _money(card.get("discounted_price"), position, "цена со скидкой")
        raw_rating = card.get("rating")
        rating: Decimal | None = None
        if raw_rating is not None:
            try:
                rating = Decimal(str(raw_rating).replace(",", "."))
            except InvalidOperation as error:
                raise YandexMarketError(
                    f"Товар Яндекс Маркета №{position}: неверный рейтинг."
                ) from error
            if not rating.is_finite() or not 0 <= rating <= 5:
                raise YandexMarketError(f"Товар Яндекс Маркета №{position}: неверный рейтинг.")
        products.append(YandexMarketProduct(
            image_url, price, discounted_price, name.strip(), rating,
            _reviews(card.get("reviews"), position),
        ))
    return tuple(products)


def parse_yandex_search(access: BrowserPageAccess) -> tuple[YandexMarketProduct, ...]:
    """Extract visible product cards from the page opened by BrowserPageAccess."""
    page = access.page
    try:
        try:
            page.wait_for_selector(_CARD_SELECTOR, timeout=8_000)
        except PlaywrightTimeoutError:
            pass
        raw_cards = page.evaluate(_EXTRACT_CARDS, _CARD_SELECTOR)
        if not raw_cards:
            if page.evaluate(_EMPTY_CHECK):
                return ()
            raise YandexMarketError("В выдаче Яндекс Маркета не найдены карточки товаров.")
        return parse_yandex_cards(raw_cards)
    except PlaywrightError as error:
        raise YandexMarketError(f"Не удалось прочитать выдачу Яндекс Маркета: {error}") from error
