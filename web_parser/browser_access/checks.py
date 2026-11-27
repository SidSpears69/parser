"""Validation of page addresses and checks for access challenges."""

from contextlib import suppress
from urllib.parse import urldefrag, urlsplit

from playwright.sync_api import Error as PlaywrightError, Page

from web_parser.browser_access.models import BrowserAccessError, BrowserCaptchaDenied, YandexCaptchaDenied


AUTH_OR_RATE_LIMIT_STATUSES = (401, 429)
_CAPTCHA_CHECK = """() => {
    const title = document.title || '';
    const text = (document.body?.innerText || '').slice(0, 3000).replace(/\\s+/g, ' ');
    if (/^(?:captcha|капча)(?:\\s|$|[.!?—-])|подтвердите.*не робот|проверка.*(?:безопасности|робот)/i.test(title)
            || /(?:подтвердите|докажите).{0,100}(?:не робот|человек)/i.test(text)
            || /запросы.{0,100}похожи на автоматические/i.test(text)
            || /(?:введите|укажите).{0,80}символы.{0,80}(?:картинк|изображени)/i.test(text)) {
        return true;
    }
    const hasResults = !!document.querySelector('[data-auto="searchOrganic"]');
    const visible = (element) => {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return rect.width > 0 && rect.height > 0
            && style.display !== 'none' && style.visibility !== 'hidden';
    };
    return [...document.querySelectorAll(
        '.smart-captcha, iframe[src*="smartcaptcha"], form[action*="showcaptcha"]'
    )].some((element) => {
        if (!visible(element)) return false;
        const rect = element.getBoundingClientRect();
        return !hasResults || (rect.width >= 250 && rect.height >= 180);
    });
}"""


def validate_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise BrowserAccessError("Укажите URL страницы.")
    url = url.strip()
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme.lower() in ("http", "https") and bool(parsed.hostname)
        if parsed.username or parsed.password:
            valid = False
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise BrowserAccessError("Ожидается HTTP(S)-ссылка без учётных данных.")
    return url


def retryable_status(status: int) -> bool:
    return status in (408, 429) or status >= 500


def is_wildberries_url(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == "wildberries.ru" or host.endswith(".wildberries.ru")


def is_ozon_url(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == "ozon.ru" or host.endswith(".ozon.ru")


def is_yandex_market_url(url: str) -> bool:
    return urlsplit(url).hostname == "market.yandex.ru"


def _is_captcha_redirect(url: str) -> bool:
    return urlsplit(url).path.lower().rstrip("/") in ("/showcaptcha", "/captcha")


def raise_if_yandex_captcha(page: Page, status: int | None = None) -> None:
    url = page.url
    host = urlsplit(url).hostname or ""
    if host == "yandex.ru" or host.endswith(".yandex.ru"):
        if _is_captcha_redirect(url) or page.evaluate(_CAPTCHA_CHECK):
            raise YandexCaptchaDenied(status, "Обнаружена капча Яндекс Маркета. Страница недоступна.")


def check_yandex_after_failed_navigation(page: Page, previous_url: str) -> None:
    # Only inspect a newly opened document. A failed request can leave the old DOM visible.
    if urldefrag(page.url).url != urldefrag(previous_url).url:
        with suppress(PlaywrightError):
            raise_if_yandex_captcha(page)


def raise_if_captcha(page: Page, status: int) -> None:
    if _is_captcha_redirect(page.url) or page.evaluate(_CAPTCHA_CHECK):
        raise BrowserCaptchaDenied(status, "страница требует прохождения капчи.")
