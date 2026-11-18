"""Read the first six Ozon search cards from the rendered browser page."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from web_parser.browser_access import BrowserPageAccess
from web_parser.layout_monitor import browser_fallback_fields, report_layout_fallback


_RESULT_LIMIT = 6
_CARD_SELECTOR = '[data-widget="tileGridDesktop"] .tile-root[data-index]'
_CARD_SELECTORS = (
    _CARD_SELECTOR,
    '.tile-root:has(a[href*="/product/"])',
    '[data-index]:has(a[href*="/product/"])',
)
_READY_CHECK = """(selectors) => {
    const groups = selectors.map(selector => [...document.querySelectorAll(selector)].slice(0, 6));
    const cards = groups.reduce((best, found) => found.length > best.length ? found : best, []);
    return cards.length === 6 && cards.every(card => {
        const name = card.querySelector('a[href*="/product/"] span.tsBody500Medium, '
            + '[data-auto="product-title"], a[href*="/product/"] [title], '
            + 'a[href*="/product/"] img[alt]');
        const price = card.querySelector('span.tsHeadline500Medium, '
            + '[data-auto="price-value"], [data-widget="webPrice"]');
        return !!card.querySelector('a[href*="/product/"]')
            && !!card.querySelector('img[src*="ir.ozone.ru/"], img[data-src*="ir.ozone.ru/"]')
            && !!name && !!price && price.textContent.includes('₽');
    });
}"""
_EXTRACT_CARDS = """(selectors) => {
    const groups = selectors.map(selector => [...document.querySelectorAll(selector)].slice(0, 6));
    const selected = groups.reduce((best, cards, index) =>
        cards.length > groups[best].length ? index : best, 0);
    const cards = groups[selected];
    return cards.map(card => {
        const fallbacks = selected > 0 ? ['карточки'] : [];
        const text = (field, candidates, required = false) => {
            for (let index = 0; index < candidates.length; index++) {
                const element = card.querySelector(candidates[index]);
                const value = element?.textContent?.trim() || element?.getAttribute('title')?.trim();
                if (value && (!required || value.includes('₽'))) {
                    if (index) fallbacks.push(field);
                    return value;
                }
            }
            return null;
        };
        const image = card.querySelector('a[href*="/product/"] img[src^="https://ir.ozone.ru/"]');
        const alternateImage = [...card.querySelectorAll('img[src], img[data-src]')]
            .flatMap(element => [element.getAttribute('src'), element.getAttribute('data-src')])
            .find(value => value?.startsWith('https://ir.ozone.ru/'));
        if (!image && alternateImage) fallbacks.push('фото');
        const small = [...card.querySelectorAll('span.tsBodyControl300XSmall')]
            .map(element => element.textContent.trim());
        const original = [...card.querySelectorAll('span.tsBodyControl400Small')]
            .find(element => element.textContent.includes('₽'));
        const alternateOriginal = original ? null : text('старая цена',
            ['[data-auto="old-price"]', '[aria-label*="Старая цена"]'], true);
        if (alternateOriginal) fallbacks.push('старая цена');
        const name = text('название', [
            'a[href*="/product/"] span.tsBody500Medium',
            '[data-auto="product-title"]',
            'a[href*="/product/"] [title]',
        ]) || card.querySelector('a[href*="/product/"] img[alt]')?.getAttribute('alt');
        if (!card.querySelector('a[href*="/product/"] span.tsBody500Medium') && name
            && !fallbacks.includes('название')) fallbacks.push('название');
        const primaryRating = small.find(value => /^\\d(?:[.,]\\d)?$/.test(value));
        const alternateRating = primaryRating ? null : text('рейтинг', [
            '[data-auto="rating"]', '[data-auto="rating-value"]',
        ])?.match(/\\d(?:[.,]\\d)?/)?.[0] ?? null;
        if (alternateRating) fallbacks.push('рейтинг');
        const primaryReviews = small.find(value => /отзыв|оценк/i.test(value));
        const alternateReviews = primaryReviews ? null : text('отзывы', [
            '[data-auto="rating-count"]', '[data-auto="reviews-count"]',
        ]);
        if (alternateReviews) fallbacks.push('отзывы');
        return {
            product_url: card.querySelector('a[href*="/product/"]')?.getAttribute('href') ?? null,
            image_url: image?.getAttribute('src') ?? alternateImage ?? null,
            current_price: text('цена', [
                'span.tsHeadline500Medium', '[data-auto="price-value"]',
                '[data-widget="webPrice"] span',
            ], true),
            original_price: original?.textContent ?? alternateOriginal ?? null,
            name: name ?? null,
            rating: primaryRating ?? alternateRating ?? null,
            reviews: primaryReviews ?? alternateReviews ?? null,
            _layout_fallbacks: fallbacks,
        };
    });
}"""
_EMPTY_CHECK = """() => /ничего не найдено|ничего не нашлось|по вашему запросу ничего/i
    .test(document.body?.innerText || '')"""
_MONEY = re.compile(r"(\d[\d\s\u00a0\u2009\u202f]*)(?:[,.](\d{1,2}))?\s*₽")
_REVIEWS = re.compile(r"(\d[\d\s\u00a0\u2009\u202f]*(?:[,.]\d+)?)\s*(тыс\.)?", re.I)


class OzonError(RuntimeError):
    """The search page does not contain usable product cards."""


class OzonLayoutError(OzonError):
    """An accessible search page does not match any known card layout."""


@dataclass(frozen=True, slots=True)
class OzonProduct:
    image_url: str
    price: Decimal
    discounted_price: Decimal
    name: str
    rating: Decimal | None
    reviews: int
    product_url: str


def _price(value: object, position: int, label: str) -> Decimal:
    if not isinstance(value, str):
        raise OzonError(f"Товар Ozon №{position}: нет поля «{label}».")
    match = _MONEY.search(value)
    if match is None:
        raise OzonError(f"Товар Ozon №{position}: неверное поле «{label}».")
    whole = re.sub(r"\D", "", match.group(1))
    fraction = (match.group(2) or "").ljust(2, "0")
    return Decimal(f"{whole}.{fraction or '00'}")


def _reviews(value: object, position: int) -> int:
    if value is None:
        return 0
    if not isinstance(value, str):
        raise OzonError(f"Товар Ozon №{position}: неверное число отзывов.")
    match = _REVIEWS.search(value)
    if match is None:
        raise OzonError(f"Товар Ozon №{position}: неверное число отзывов.")
    number = re.sub(r"\s", "", match.group(1)).replace(",", ".")
    count = Decimal(number) * (1000 if match.group(2) else 1)
    if count != int(count):
        raise OzonError(f"Товар Ozon №{position}: неверное число отзывов.")
    return int(count)


def _unscaled_image_url(image_url: str) -> str:
    """Remove Ozon's card-width transform from the primary image URL."""
    parts = urlsplit(image_url)
    path = re.sub(r"/wc\d+/", "/", parts.path, count=1)
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def parse_ozon_cards(raw_cards: object) -> tuple[OzonProduct, ...]:
    """Validate browser-extracted fields while retaining search result order."""
    if not isinstance(raw_cards, list):
        raise OzonLayoutError("Ozon изменил формат поисковой выдачи.")
    result: list[OzonProduct] = []
    for position, card in enumerate(raw_cards[:_RESULT_LIMIT], 1):
        if not isinstance(card, dict):
            raise OzonError(f"Товар Ozon №{position}: неверный формат карточки.")
        image_url = card.get("image_url")
        raw_product_url = card.get("product_url")
        name = card.get("name")
        if (not isinstance(image_url, str)
                or urlsplit(image_url).scheme != "https"
                or urlsplit(image_url).hostname != "ir.ozone.ru"):
            raise OzonError(f"Товар Ozon №{position}: нет основного фото.")
        if not isinstance(name, str) or not name.strip():
            raise OzonError(f"Товар Ozon №{position}: нет названия.")
        product_url = urljoin("https://www.ozon.ru", raw_product_url) if isinstance(raw_product_url, str) else ""
        product_parts = urlsplit(product_url)
        if (
            product_parts.scheme != "https" or product_parts.hostname not in {"ozon.ru", "www.ozon.ru"}
            or not product_parts.path.startswith("/product/")
            or product_parts.username or product_parts.password
        ):
            raise OzonError(f"Товар Ozon №{position}: нет ссылки на карточку.")
        discounted_price = _price(card.get("current_price"), position, "цена со скидкой")
        original = card.get("original_price")
        price = _price(original, position, "цена") if original is not None else discounted_price
        raw_rating = card.get("rating")
        rating: Decimal | None = None
        if raw_rating is not None:
            try:
                rating = Decimal(str(raw_rating).replace(",", "."))
            except (ValueError, ArithmeticError) as error:
                raise OzonError(f"Товар Ozon №{position}: неверный рейтинг.") from error
            if not 0 <= rating <= 5:
                raise OzonError(f"Товар Ozon №{position}: неверный рейтинг.")
        result.append(OzonProduct(
            _unscaled_image_url(image_url), price, discounted_price, name.strip(), rating,
            _reviews(card.get("reviews"), position), product_url,
        ))
    return tuple(result)


def parse_ozon_search(
    access: BrowserPageAccess, *, on_layout_change: Callable[[str], None] | None = None,
) -> tuple[OzonProduct, ...]:
    """Read product cards and report when fallback selectors were needed."""
    page = access.page
    try:
        try:
            page.wait_for_function(_READY_CHECK, arg=_CARD_SELECTORS, timeout=12_000)
        except PlaywrightTimeoutError:
            pass
        raw_cards = page.evaluate(_EXTRACT_CARDS, _CARD_SELECTORS)
        if not raw_cards:
            if page.evaluate(_EMPTY_CHECK):
                return ()
            raise OzonLayoutError("В выдаче Ozon не найдены карточки товаров; проверьте селекторы выдачи.")
        try:
            products = parse_ozon_cards(raw_cards)
        except OzonError as error:
            if browser_fallback_fields(raw_cards):
                raise OzonLayoutError(f"Запасные селекторы Ozon не прочитали выдачу: {error}") from error
            raise
        report_layout_fallback(on_layout_change, browser_fallback_fields(raw_cards))
        return products
    except PlaywrightError as error:
        raise OzonError(f"Не удалось прочитать выдачу Ozon: {error}") from error
