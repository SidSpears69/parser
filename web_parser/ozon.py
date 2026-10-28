"""Read the first six Ozon search cards from the rendered browser page."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from web_parser.browser_access import BrowserPageAccess


_RESULT_LIMIT = 6
_CARD_SELECTOR = '[data-widget="tileGridDesktop"] .tile-root[data-index]'
_READY_CHECK = """(selector) => {
    const card = document.querySelector(selector);
    return !!card && !!card.querySelector('a[href*="/product/"] span.tsBody500Medium')
        && !!card.querySelector('span.tsHeadline500Medium');
}"""
_EXTRACT_CARDS = """(selector) => {
    const cards = [...document.querySelectorAll(selector)].slice(0, 6);
    return cards.map(card => {
        const small = [...card.querySelectorAll('span.tsBodyControl300XSmall')]
            .map(element => element.textContent.trim());
        const original = [...card.querySelectorAll('span.tsBodyControl400Small')]
            .find(element => element.textContent.includes('₽'));
        return {
            image_url: card.querySelector('a[href*="/product/"] img[src^="https://ir.ozone.ru/"]')
                ?.getAttribute('src') ?? null,
            current_price: card.querySelector('span.tsHeadline500Medium')?.textContent ?? null,
            original_price: original?.textContent ?? null,
            name: card.querySelector('a[href*="/product/"] span.tsBody500Medium')
                ?.textContent ?? null,
            rating: small.find(value => /^\\d(?:[.,]\\d)?$/.test(value)) ?? null,
            reviews: small.find(value => /отзыв|оценк/i.test(value)) ?? null,
        };
    });
}"""
_EMPTY_CHECK = """() => /ничего не найдено|ничего не нашлось|по вашему запросу ничего/i
    .test(document.body?.innerText || '')"""
_MONEY = re.compile(r"(\d[\d\s\u00a0\u2009\u202f]*)(?:[,.](\d{1,2}))?\s*₽")
_REVIEWS = re.compile(r"(\d[\d\s\u00a0\u2009\u202f]*(?:[,.]\d+)?)\s*(тыс\.)?", re.I)


class OzonError(RuntimeError):
    """The search page does not contain usable product cards."""


@dataclass(frozen=True, slots=True)
class OzonProduct:
    image_url: str
    price: Decimal
    discounted_price: Decimal
    name: str
    rating: Decimal | None
    reviews: int


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


def parse_ozon_cards(raw_cards: object) -> tuple[OzonProduct, ...]:
    """Validate browser-extracted fields while retaining search result order."""
    if not isinstance(raw_cards, list):
        raise OzonError("Ozon изменил формат поисковой выдачи.")
    result: list[OzonProduct] = []
    for position, card in enumerate(raw_cards[:_RESULT_LIMIT], 1):
        if not isinstance(card, dict):
            raise OzonError(f"Товар Ozon №{position}: неверный формат карточки.")
        image_url = card.get("image_url")
        name = card.get("name")
        if (not isinstance(image_url, str)
                or urlsplit(image_url).scheme != "https"
                or urlsplit(image_url).hostname != "ir.ozone.ru"):
            raise OzonError(f"Товар Ozon №{position}: нет основного фото.")
        if not isinstance(name, str) or not name.strip():
            raise OzonError(f"Товар Ozon №{position}: нет названия.")
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
            image_url, price, discounted_price, name.strip(), rating,
            _reviews(card.get("reviews"), position),
        ))
    return tuple(result)


def parse_ozon_search(access: BrowserPageAccess) -> tuple[OzonProduct, ...]:
    """Use direct selectors in the tab most recently opened by BrowserPageAccess."""
    page = access.page
    try:
        try:
            page.wait_for_function(_READY_CHECK, arg=_CARD_SELECTOR, timeout=8_000)
        except PlaywrightTimeoutError:
            pass
        raw_cards = page.evaluate(_EXTRACT_CARDS, _CARD_SELECTOR)
        if not raw_cards:
            if page.evaluate(_EMPTY_CHECK):
                return ()
            raise OzonError("В выдаче Ozon не найдены карточки товаров.")
        return parse_ozon_cards(raw_cards)
    except PlaywrightError as error:
        raise OzonError(f"Не удалось прочитать выдачу Ozon: {error}") from error
