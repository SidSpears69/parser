"""Read the first six Yandex Market products from a rendered search page."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Callable
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from web_parser.browser_access import BrowserPageAccess
from web_parser.layout_monitor import browser_fallback_fields, report_layout_fallback


_RESULT_LIMIT = 6
_CARD_SELECTOR = '[data-zone-name="productSnippet"]'
_CARD_SELECTORS = (
    _CARD_SELECTOR,
    '[data-auto="snippet"]',
    'article:has([data-auto="snippet-title"])',
)
_EXTRACT_CARDS = """(selectors) => {
    const groups = selectors.map(selector => [...document.querySelectorAll(selector)].slice(0, 6));
    const selected = groups.findIndex(cards => cards.length > 0);
    const cards = selected < 0 ? [] : groups[selected];
    return cards.map(card => {
        const fallbacks = selected > 0 ? ['карточки'] : [];
        const text = (field, candidates) => {
            for (let index = 0; index < candidates.length; index++) {
                const element = card.querySelector(candidates[index]);
                const value = element?.textContent?.trim() || element?.getAttribute('title')?.trim();
                if (value) {
                    if (index) fallbacks.push(field);
                    return value;
                }
            }
            return null;
        };
        const money = value => {
            const match = value?.match(/(\\d[\\d\\s\\u00a0\\u202f]*)(?:[,.](\\d{1,2}))?/);
            return match ? match[1].replace(/\\s/g, '') + (match[2] ? '.' + match[2] : '') : null;
        };
        let details = {};
        try {
            const parsed = JSON.parse(card.getAttribute('data-zone-data') || '{}');
            if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) details = parsed;
        }
        catch (_) { /* Validation below will report the incomplete card. */ }
        const discounts = Array.isArray(details.additionalPrices)
            ? details.additionalPrices : [];
        const discount = discounts.find(item => item && item.priceType === 'withDiscount');
        const rating = card.querySelector('[data-zone-name="rating"] .ds-rating__value');
        const ratingText = card.querySelector('[data-zone-name="rating"]')?.innerText || '';
        const title = card.querySelector('[data-auto="snippet-title"]')
            || card.querySelector('h3, h2');
        const gallery = card.querySelector('[data-auto="galleryLink"]');
        const link = title?.closest('a[href]') || title?.querySelector('a[href]')
            || gallery?.closest('a[href]') || gallery?.querySelector('a[href]')
            || card.querySelector('a[href*="/product"], a[href*="/card/"], a[href*="/offer/"]');
        const image = card.querySelector('[data-auto="galleryLink"] img[src^="https://avatars.mds.yandex.net/"]');
        const alternateImage = [...card.querySelectorAll('img[src], img[data-src]')]
            .flatMap(element => [element.getAttribute('src'), element.getAttribute('data-src')])
            .find(value => value?.startsWith('https://avatars.mds.yandex.net/'));
        if (!image && alternateImage) fallbacks.push('фото');
        const declaredPrice = ['number', 'string'].includes(typeof details.price) ? details.price : null;
        const declaredDiscount = ['number', 'string'].includes(typeof discount?.priceValue)
            ? discount.priceValue : null;
        const currentPrice = declaredPrice == null || declaredDiscount == null
            ? money(text('цена', [
                '[data-auto="snippet-price-current"]', '[data-auto="price-value"]',
                '[data-auto="price"]',
            ])) : null;
        const oldPrice = declaredPrice == null
            ? money(text('старая цена', [
                '[data-auto="snippet-price-old"]', '[data-auto="old-price"]',
            ])) : null;
        const price = declaredPrice ?? oldPrice ?? currentPrice;
        const discounted = declaredDiscount ?? currentPrice ?? price;
        if (declaredPrice == null && price != null) fallbacks.push('цена из DOM');
        if (discount && declaredDiscount == null && currentPrice != null)
            fallbacks.push('цена со скидкой из DOM');
        const primaryName = card.querySelector('[data-auto="snippet-title"]')?.textContent?.trim();
        const name = text('название', ['[data-auto="snippet-title"]', 'h3', 'h2'])
            || card.querySelector('img[alt]')?.getAttribute('alt') || null;
        if (!primaryName && name && !fallbacks.includes('название')) fallbacks.push('название');
        const alternateRating = rating ? null : text('рейтинг', [
            '[data-auto="rating-value"]', '[data-auto="rating"]',
        ])?.match(/\\d(?:[.,]\\d)?/)?.[0] ?? null;
        if (alternateRating) fallbacks.push('рейтинг');
        const primaryReviews = ratingText.match(/\\((\\d[\\d\\s\\u00a0\\u2009\\u202f]*)\\)/)?.[1];
        const alternateReviews = primaryReviews ? null : text('отзывы', [
            '[data-auto="rating-count"]', '[data-auto="reviews-count"]',
        ])?.match(/[\\d\\s\\u00a0\\u2009\\u202f]+/)?.[0] ?? null;
        if (alternateReviews) fallbacks.push('отзывы');
        return {
            product_url: link?.getAttribute('href') ?? null,
            image_url: image?.getAttribute('src') ?? alternateImage ?? null,
            price: price ?? null,
            discounted_price: discounted ?? null,
            name: name,
            rating: rating?.textContent ?? alternateRating ?? null,
            reviews: primaryReviews ?? alternateReviews ?? null,
            _layout_fallbacks: fallbacks,
        };
    });
}"""
_EMPTY_CHECK = """() => /ничего не найдено|товары не найдены|ничего не нашлось/i
    .test(document.body?.innerText || '')"""


class YandexMarketError(RuntimeError):
    """The search page does not contain usable product cards."""


class YandexMarketLayoutError(YandexMarketError):
    """An accessible search page does not match any known card layout."""


@dataclass(frozen=True, slots=True)
class YandexMarketProduct:
    image_url: str
    price: Decimal
    discounted_price: Decimal
    name: str
    rating: Decimal | None
    reviews: int
    product_url: str


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
        raise YandexMarketLayoutError("Яндекс Маркет изменил формат поисковой выдачи.")
    products: list[YandexMarketProduct] = []
    for position, card in enumerate(raw_cards[:_RESULT_LIMIT], 1):
        if not isinstance(card, dict):
            raise YandexMarketError(f"Товар Яндекс Маркета №{position}: неверный формат карточки.")
        image_url = card.get("image_url")
        raw_product_url = card.get("product_url")
        name = card.get("name")
        if (not isinstance(image_url, str)
                or urlsplit(image_url).scheme != "https"
                or urlsplit(image_url).hostname != "avatars.mds.yandex.net"):
            raise YandexMarketError(f"Товар Яндекс Маркета №{position}: нет основного фото.")
        if not isinstance(name, str) or not name.strip():
            raise YandexMarketError(f"Товар Яндекс Маркета №{position}: нет названия.")
        product_url = (
            urljoin("https://market.yandex.ru", raw_product_url)
            if isinstance(raw_product_url, str) else ""
        )
        product_parts = urlsplit(product_url)
        if (
            product_parts.scheme != "https" or product_parts.hostname != "market.yandex.ru"
            or product_parts.path in {"", "/"} or product_parts.username or product_parts.password
        ):
            raise YandexMarketError(f"Товар Яндекс Маркета №{position}: нет ссылки на карточку.")
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
            _reviews(card.get("reviews"), position), product_url,
        ))
    return tuple(products)


def parse_yandex_search(
    access: BrowserPageAccess, *, on_layout_change: Callable[[str], None] | None = None,
) -> tuple[YandexMarketProduct, ...]:
    """Extract visible cards and report when fallback selectors were needed."""
    page = access.page
    try:
        try:
            page.wait_for_selector(", ".join(_CARD_SELECTORS), timeout=8_000)
        except PlaywrightTimeoutError:
            pass
        access.check_yandex_captcha()
        raw_cards = page.evaluate(_EXTRACT_CARDS, _CARD_SELECTORS)
        if not raw_cards:
            if page.evaluate(_EMPTY_CHECK):
                return ()
            raise YandexMarketLayoutError(
                "В выдаче Яндекс Маркета не найдены карточки товаров; проверьте селекторы выдачи."
            )
        try:
            products = parse_yandex_cards(raw_cards)
        except YandexMarketError as error:
            if browser_fallback_fields(raw_cards):
                raise YandexMarketLayoutError(
                    f"Запасные селекторы Яндекс Маркета не прочитали выдачу: {error}"
                ) from error
            raise
        report_layout_fallback(on_layout_change, browser_fallback_fields(raw_cards))
        return products
    except PlaywrightError as error:
        raise YandexMarketError(f"Не удалось прочитать выдачу Яндекс Маркета: {error}") from error
