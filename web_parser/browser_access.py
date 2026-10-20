"""Basic Playwright access to marketplace pages in a selected browser."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Callable
from urllib.parse import urlsplit

from playwright.sync_api import Browser, BrowserContext, Error as PlaywrightError
from playwright.sync_api import Page, Playwright, TimeoutError as PlaywrightTimeoutError, sync_playwright
from playwright_stealth import Stealth

from web_parser.browser_discovery import BrowserInstallation
from web_parser.config import BROWSERS, SITES


_NAVIGATION_TIMEOUT_MS = 30_000
_CONTENT_TIMEOUT_MS = 5_000
_OZON_CHALLENGE_TIMEOUT_MS = 20_000
_WILDBERRIES_CHALLENGE_TIMEOUT_MS = 20_000
_YANDEX_SEARCH_TIMEOUT_MS = 10_000
_YANDEX_SETTLE_MS = 750
_MAX_ATTEMPTS = 3
_CHROMIUM_ARGS = ["--disable-blink-features=AutomationControlled"]
_YANDEX_CAPTCHA_CHECK = """() => {
    const title = document.title || '';
    const text = (document.body?.innerText || '').slice(0, 3000).replace(/\\s+/g, ' ');
    if (/^(?:captcha|капча)(?:\\s|$|[.!?—-])|подтвердите.*не робот|проверка.*(?:безопасности|робот)/i.test(title)
            || /(?:подтвердите|докажите).{0,100}(?:не робот|человек)|запросы.{0,100}похожи на автоматические|(?:введите|укажите).{0,80}символы.{0,80}(?:картинк|изображени)/i.test(text)) {
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


class BrowserAccessError(RuntimeError):
    """The selected browser could not return a usable page."""


class BrowserAccessCancelled(BrowserAccessError):
    """The user stopped the page sequence."""


class BrowserAccessDenied(BrowserAccessError):
    """The site did not provide a usable page after an access check."""

    def __init__(self, status: int, reason: str | None = None) -> None:
        self.status = status
        super().__init__(f"HTTP {status}: {reason or 'страница не прошла проверку доступа.'}")


@dataclass(frozen=True, slots=True)
class PageSnapshot:
    requested_url: str
    final_url: str
    status: int
    html: str
    attempts: int


def _validate_url(url: str) -> str:
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


def _retryable_status(status: int) -> bool:
    return status in (408, 429) or status >= 500


def _is_wildberries_url(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == "wildberries.ru" or host.endswith(".wildberries.ru")


def _is_ozon_url(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return host == "ozon.ru" or host.endswith(".ozon.ru")


def _is_yandex_market_url(url: str) -> bool:
    return urlsplit(url).hostname == "market.yandex.ru"


def _is_yandex_captcha_redirect(url: str) -> bool:
    return urlsplit(url).path.lower().rstrip("/") in ("/showcaptcha", "/captcha")


def _raise_if_yandex_captcha(page: Page, requested_url: str, status: int) -> None:
    if _is_yandex_market_url(requested_url) and (
        _is_yandex_captcha_redirect(page.url) or page.evaluate(_YANDEX_CAPTCHA_CHECK)
    ):
        raise BrowserAccessDenied(status, "обнаружена капча Яндекс Маркета; страница недоступна.")


def browser_profile_dir(site: str, browser: str) -> Path:
    """Keep each site/browser session outside the repository and other profiles."""
    if site not in SITES or browser not in BROWSERS:
        raise ValueError("Неизвестный сайт или браузер для профиля Playwright.")
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    browser_slug = ("yandex", "chrome", "edge", "firefox")[BROWSERS.index(browser)]
    site_slug = site.replace(".", "_").replace("-", "_")
    return base / "WebParser" / "BrowserProfiles" / site_slug / browser_slug


def _firefox_stealth() -> Stealth:
    """Hide the automation flag without adding Chromium-only browser properties."""
    return Stealth(
        chrome_app=False,
        chrome_csi=False,
        chrome_load_times=False,
        chrome_runtime=False,
        hairline=False,
        iframe_content_window=False,
        media_codecs=False,
        navigator_hardware_concurrency=False,
        navigator_languages=True,
        navigator_permissions=False,
        navigator_platform=True,
        navigator_plugins=False,
        navigator_user_agent=False,
        navigator_user_agent_data=False,
        navigator_vendor=False,
        navigator_webdriver=True,
        error_prototype=False,
        sec_ch_ua=False,
        webgl_vendor=False,
        navigator_languages_override=("ru-RU", "ru"),
        init_scripts_only=True,
    )


class BrowserPageAccess:
    """Keep one Playwright context for a sequence of URLs in one worker thread.

    Open, fetch and close must be called from the same thread because the
    synchronous Playwright API is thread-affine. The Qt GUI must use a worker.
    """

    def __init__(
        self,
        installation: BrowserInstallation,
        pause_seconds: float,
        *,
        attempts: int = _MAX_ATTEMPTS,
        headless: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        cancel_requested: Callable[[], bool] = lambda: False,
        profile_dir: Path | None = None,
    ) -> None:
        if not 0 <= pause_seconds <= 3600:
            raise ValueError("Пауза должна быть от 0 до 3600 секунд.")
        if not 1 <= attempts <= 10:
            raise ValueError("Число попыток должно быть от 1 до 10.")
        self.installation = installation
        self.pause_seconds = pause_seconds
        self.attempts = attempts
        self.headless = headless
        self._sleep = sleep
        self._cancel_requested = cancel_requested
        self.profile_dir = Path(profile_dir) if profile_dir is not None else None
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._navigated_once = False

    def __enter__(self) -> BrowserPageAccess:
        if self._page is not None:
            raise BrowserAccessError("Сеанс Playwright уже открыт.")
        name = self.installation.name
        if name not in BROWSERS:
            raise BrowserAccessError(f"Неизвестный браузер: {name}")
        binary = Path(self.installation.path)
        if name != "Mozilla Firefox":
            if not binary.is_file():
                raise BrowserAccessError(f"Исполняемый файл браузера не найден: {binary}")

        try:
            self._playwright = sync_playwright().start()
            if name == "Mozilla Firefox":
                firefox = self._playwright.firefox
                if not Path(firefox.executable_path).is_file():
                    raise BrowserAccessError(
                        "Сборка Firefox для Playwright не установлена. Выполните "
                        ".\\.venv\\Scripts\\python.exe -m playwright install firefox."
                    )
                if self.profile_dir is None:
                    self._browser = firefox.launch(headless=self.headless)
                else:
                    self.profile_dir.mkdir(parents=True, exist_ok=True)
                    self._context = firefox.launch_persistent_context(
                        user_data_dir=str(self.profile_dir), headless=self.headless, locale="ru-RU",
                    )
            else:
                if self.profile_dir is None:
                    self._browser = self._playwright.chromium.launch(
                        executable_path=str(binary), headless=self.headless, args=_CHROMIUM_ARGS,
                    )
                else:
                    self.profile_dir.mkdir(parents=True, exist_ok=True)
                    self._context = self._playwright.chromium.launch_persistent_context(
                        user_data_dir=str(self.profile_dir), executable_path=str(binary),
                        headless=self.headless, locale="ru-RU", args=_CHROMIUM_ARGS,
                    )
            if self._context is None and self._browser is not None:
                self._context = self._browser.new_context(locale="ru-RU")
            if self._context is None:
                raise BrowserAccessError("Не удалось создать контекст браузера.")
            if name == "Mozilla Firefox":
                _firefox_stealth().apply_stealth_sync(self._context)
            pages = self._context.pages if self.profile_dir is not None else []
            self._page = pages[0] if pages else self._context.new_page()
            self._navigated_once = False
        except BrowserAccessError:
            try:
                self.close()
            except BrowserAccessError:
                pass
            raise
        except (PlaywrightError, OSError) as error:
            try:
                self.close()
            except BrowserAccessError:
                pass
            raise BrowserAccessError(f"Не удалось запустить {name} через Playwright: {error}") from error
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        if _value is None:
            self.close()
        else:
            try:
                self.close()
            except BrowserAccessError:
                pass

    def fetch(self, url: str) -> PageSnapshot:
        """Return nonempty HTML or fail after bounded retries and delays."""
        url = _validate_url(url)
        page = self._page
        if page is None:
            raise BrowserAccessError("Сначала откройте сеанс Playwright.")

        last_problem = "ответ отсутствует"
        for attempt in range(1, self.attempts + 1):
            self._check_cancelled()
            if self._navigated_once:
                self._sleep(self.pause_seconds)
                self._check_cancelled()
            self._navigated_once = True
            try:
                response = page.goto(
                    url, wait_until="load", timeout=_NAVIGATION_TIMEOUT_MS,
                )
                self._check_cancelled()
                if response is None:
                    last_problem = "пустой ответ браузера"
                    continue
                _raise_if_yandex_captcha(page, url, response.status)
                ozon_challenge = response.status == 403 and _is_ozon_url(url)
                wildberries_challenge = response.status == 498 and _is_wildberries_url(url)
                if not response.ok and not (ozon_challenge or wildberries_challenge):
                    last_problem = f"HTTP {response.status}"
                    if response.status in (403, 498):
                        raise BrowserAccessDenied(response.status)
                    if _retryable_status(response.status):
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
                    elif _is_yandex_market_url(url) and urlsplit(url).path.lower().startswith("/search"):
                        page.wait_for_function(
                            """() => {
                                const text = document.body?.innerText || '';
                                return !!document.querySelector('[data-auto="searchOrganic"]')
                                    || /ничего не найдено|товары не найдены|ничего не нашлось/i.test(text);
                            }""",
                            timeout=_YANDEX_SEARCH_TIMEOUT_MS,
                        )
                        page.wait_for_timeout(_YANDEX_SETTLE_MS)
                    else:
                        page.wait_for_function(
                            "document.body && document.body.innerText.trim().length > 0",
                            timeout=_CONTENT_TIMEOUT_MS,
                        )
                except PlaywrightTimeoutError:
                    _raise_if_yandex_captcha(page, url, response.status)
                    if ozon_challenge or wildberries_challenge:
                        raise BrowserAccessDenied(response.status) from None
                    last_problem = (
                        "результаты Яндекс Маркета не появились"
                        if _is_yandex_market_url(url) else "пустая страница"
                    )
                    continue
                self._check_cancelled()
                _raise_if_yandex_captcha(page, url, response.status)
                html = page.content()
                self._check_cancelled()
                if not html.strip():
                    last_problem = "пустой HTML"
                    continue
                return PageSnapshot(url, page.url, response.status, html, attempt)
            except BrowserAccessError:
                raise
            except PlaywrightError as error:
                self._check_cancelled()
                last_problem = f"ошибка браузера: {error}"
        raise BrowserAccessError(
            f"Не удалось получить страницу {url}. Попыток: {self.attempts}; {last_problem}."
        )

    def _check_cancelled(self) -> None:
        if self._cancel_requested():
            raise BrowserAccessCancelled("Обход страниц остановлен пользователем.")

    def close(self) -> None:
        """Release resources in reverse order, including after partial startup."""
        context, self._context = self._context, None
        browser, self._browser = self._browser, None
        playwright, self._playwright = self._playwright, None
        self._page = None
        self._navigated_once = False
        first_error: PlaywrightError | OSError | None = None
        closers = (
            context.close if context is not None else None,
            browser.close if browser is not None else None,
            playwright.stop if playwright is not None else None,
        )
        for closer in closers:
            if closer is None:
                continue
            try:
                closer()
            except (PlaywrightError, OSError) as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise BrowserAccessError(f"Не удалось закрыть сеанс Playwright: {first_error}") from first_error
