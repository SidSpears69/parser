"""Load marketplace pages and validate the rendered HTML."""

import os
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

from web_parser.browser_access.checks import (
    check_yandex_after_failed_navigation, is_ozon_url, is_wildberries_url, is_yandex_market_url,
    raise_if_captcha, raise_if_yandex_captcha, retryable_status, validate_url,
)
from web_parser.browser_access.models import (
    BrowserAccessCancelled, BrowserAccessDenied, BrowserAccessError, BrowserFetchFailed,
    PageSnapshot, YandexCaptchaDenied,
)
from web_parser.browser_access.wildberries import WildberriesBrowserSession
from web_parser.captcha_service import CaptchaServiceCancelled, CaptchaServiceError
from web_parser.yandex_captcha import solve_yandex_captcha


_CONTENT_TIMEOUT_MS = 5_000
_OZON_CHALLENGE_TIMEOUT_MS = 20_000
_WILDBERRIES_CHALLENGE_TIMEOUT_MS = 20_000
_YANDEX_SEARCH_TIMEOUT_MS = 10_000
_YANDEX_SETTLE_MS = 750
_YANDEX_SEARCH_STATE = """() => {
    const text = (document.body?.innerText || '').trim();
    if (document.querySelector('[data-auto="searchOrganic"]')) return 'results';
    if (/ничего не найдено|товары не найдены|ничего не нашлось/i.test(text)) return 'empty';
    if (text.startsWith('Что-то не сработало')
            && text.includes('Попробуйте обновить или зайдите позже')) return 'site_error';
    return null;
}"""


class BrowserPageAccess(WildberriesBrowserSession):
    """Load HTML and product responses in the current browser session."""

    _last_yandex_url: str | None = None

    def fetch(self, url: str, *, attempts: int | None = None) -> PageSnapshot:
        """Return nonempty HTML or fail after bounded retries and delays."""
        max_attempts = self.attempts if attempts is None else attempts
        if not 1 <= max_attempts <= self.attempts:
            raise ValueError("Число попыток должно быть от 1 до настроенного максимума.")
        url = validate_url(url)
        page = self._page
        if page is None:
            raise BrowserAccessError("Сначала откройте сеанс Playwright.")

        yandex_market = is_yandex_market_url(url)
        yandex_search = yandex_market and urlsplit(url).path.lower().startswith("/search")
        last_problem = "ответ отсутствует"
        attempt = 0
        captcha_solved: bool | None = None
        last_captcha_error: YandexCaptchaDenied | None = None
        while attempt < max_attempts:
            attempt += 1
            self._begin_navigation()
            try:
                previous_url = page.url if yandex_market else ""
                try:
                    response = self._navigate(page, url, wait_until="domcontentloaded" if yandex_market else "load")
                except PlaywrightError:
                    self._check_cancelled()
                    if yandex_market:
                        check_yandex_after_failed_navigation(page, previous_url)
                    raise
                self._check_cancelled()
                if response is None:
                    if yandex_market:
                        check_yandex_after_failed_navigation(page, previous_url)
                    last_problem = "пустой ответ браузера"
                    continue
                if yandex_market:
                    raise_if_yandex_captcha(page, response.status)
                self._check_access_response(page, response.status)
                if self._strict_access and not yandex_market:
                    raise_if_captcha(page, response.status)
                ozon_challenge = response.status == 403 and is_ozon_url(url)
                wildberries_challenge = response.status == 498 and is_wildberries_url(url)
                if not response.ok and not (ozon_challenge or wildberries_challenge):
                    last_problem = f"HTTP {response.status}"
                    if response.status in (403, 498):
                        raise BrowserAccessDenied(response.status)
                    if retryable_status(response.status):
                        continue
                    raise BrowserAccessError(f"Сервер вернул {last_problem}.")
                try:
                    if ozon_challenge:
                        # The initial 403 can be a browser check that later
                        # renders the requested search results in the same tab.
                        search_page = urlsplit(url).path.lower().startswith("/search")
                        page.wait_for_function(
                            """(searchPage) => {
                                const text = document.body?.innerText.trim() || '';
                                const searchReady = !searchPage
                                    || !!document.querySelector('a[href*="/product/"]')
                                    || /ничего не найдено|ничего не нашлось|по вашему запросу ничего/i.test(text);
                                return document.title.toLowerCase().includes('ozon')
                                    && text.length >= 500
                                    && !text.includes('Похоже, нет соединения')
                                    && searchReady;
                            }""",
                            arg=search_page,
                            timeout=_OZON_CHALLENGE_TIMEOUT_MS,
                        )
                    elif wildberries_challenge:
                        # Wildberries can return 498 for its initial browser check,
                        # then replace the document with a usable search page.
                        search_page = urlsplit(url).path.lower().endswith("/search.aspx")
                        page.wait_for_function(
                            """(searchPage) => {
                                const text = document.body?.innerText.trim() || '';
                                const searchReady = !searchPage
                                    || !!document.querySelector('a[href*="/catalog/"][href*="/detail.aspx"]')
                                    || /ничего не найдено|товары не найдены|ничего не нашлось/i.test(text);
                                return document.title.includes('Wildberries')
                                    && text.length >= 100
                                    && !text.includes('Проверяем браузер')
                                    && searchReady;
                            }""",
                            arg=search_page,
                            timeout=_WILDBERRIES_CHALLENGE_TIMEOUT_MS,
                        )
                    elif yandex_search:
                        page.wait_for_function(
                            _YANDEX_SEARCH_STATE,
                            timeout=_YANDEX_SEARCH_TIMEOUT_MS,
                        )
                        page.wait_for_timeout(_YANDEX_SETTLE_MS)
                    else:
                        page.wait_for_function(
                            "document.body && document.body.innerText.trim().length > 0",
                            timeout=_CONTENT_TIMEOUT_MS,
                        )
                except PlaywrightTimeoutError:
                    self._check_cancelled()
                    if yandex_market:
                        raise_if_yandex_captcha(page, response.status)
                    self._check_access_response(page, response.status)
                    if self._strict_access and not yandex_market:
                        raise_if_captcha(page, response.status)
                    if ozon_challenge:
                        raise BrowserAccessDenied(
                            response.status,
                            "капча или проверка браузера Ozon не пройдена; страница недоступна.",
                        ) from None
                    if wildberries_challenge:
                        raise BrowserAccessDenied(response.status) from None
                    last_problem = (
                        "результаты Яндекс Маркета не появились"
                        if yandex_market else "пустая страница"
                    )
                    continue
                self._check_cancelled()
                if yandex_market:
                    raise_if_yandex_captcha(page, response.status)
                self._check_access_response(page, response.status)
                if self._strict_access and not yandex_market:
                    raise_if_captcha(page, response.status)
                if yandex_search and page.evaluate(_YANDEX_SEARCH_STATE) == "site_error":
                    last_problem = "Яндекс Маркет показал страницу «Что-то не сработало»"
                    continue
                html = page.content()
                self._check_cancelled()
                if not html.strip():
                    last_problem = "пустой HTML"
                    continue
                if yandex_market:
                    self._last_yandex_url = url
                return PageSnapshot(url, page.url, response.status, html, attempt)
            except YandexCaptchaDenied as error:
                if captcha_solved is None:
                    try:
                        captcha_solved = self._solve_yandex_captcha(error)
                    except YandexCaptchaDenied as service_error:
                        error = service_error
                        captcha_solved = False
                    last_captcha_error = error
            except BrowserAccessError:
                raise
            except PlaywrightError as error:
                self._check_browser_interruption(error)
                last_problem = f"ошибка браузера: {self._safe_error(error)}"
        if last_captcha_error is not None:
            # Preserve any solver error while reporting the shared navigation budget.
            last_captcha_error.attempts = attempt
            last_captcha_error.args = (
                f"{last_captcha_error} Попыток: {attempt}. Страница недоступна.",
            )
            raise last_captcha_error
        raise BrowserFetchFailed(
            f"Не удалось получить страницу {url}. Попыток: {max_attempts}; {last_problem}."
        )

    def check_yandex_captcha(self) -> None:
        """Detect a challenge that appeared after navigation, while cards were loading."""
        self._check_cancelled()
        try:
            raise_if_yandex_captcha(self.page)
        except YandexCaptchaDenied as error:
            if not self._last_yandex_url or not self._solve_yandex_captcha(error):
                raise
            # The caller owns the navigation budget and must reload the page.
            error.solved = True
            raise

    def _solve_yandex_captcha(self, error: YandexCaptchaDenied) -> bool:
        api_key = os.environ.get("WEB_PARSER_2CAPTCHA_API_KEY", "").strip()
        if not api_key:
            return False
        try:
            solved = solve_yandex_captcha(
                self.page, api_key=api_key, cancelled=self._cancel_requested,
            )
            self._check_cancelled()
            return solved
        except CaptchaServiceCancelled:
            raise BrowserAccessCancelled("Обход остановлен пользователем.") from None
        except CaptchaServiceError as service_error:
            raise YandexCaptchaDenied(
                error.status, f"Не удалось решить капчу Яндекс Маркета: {service_error}"
            ) from None
