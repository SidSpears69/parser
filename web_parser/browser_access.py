"""Basic Playwright access to marketplace pages in a selected browser."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Callable
from urllib.parse import parse_qs, quote, quote_plus, urlsplit
from uuid import uuid4

from playwright.sync_api import Browser, BrowserContext, Error as PlaywrightError
from playwright.sync_api import Page, Playwright, Response
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
from playwright_stealth import Stealth

from web_parser.browser_discovery import BrowserInstallation
from web_parser.config import BROWSERS, SITES
from web_parser.socks5_relay import Socks5AuthRelay


_NAVIGATION_TIMEOUT_MS = 30_000
_CONTENT_TIMEOUT_MS = 5_000
_OZON_CHALLENGE_TIMEOUT_MS = 20_000
_WILDBERRIES_CHALLENGE_TIMEOUT_MS = 20_000
_WILDBERRIES_JSON_FIRST_WAIT_SECONDS = 45
_WILDBERRIES_JSON_RETRY_WAIT_SECONDS = 10
_YANDEX_SEARCH_TIMEOUT_MS = 10_000
_YANDEX_SETTLE_MS = 750
_MAX_ATTEMPTS = 3
_BROWSER_LAUNCH_TIMEOUT_MS = 20_000
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
_YANDEX_SEARCH_STATE = """() => {
    const text = (document.body?.innerText || '').trim();
    if (document.querySelector('[data-auto="searchOrganic"]')) return 'results';
    if (/ничего не найдено|товары не найдены|ничего не нашлось/i.test(text)) return 'empty';
    if (text.startsWith('Что-то не сработало')
            && text.includes('Попробуйте обновить или зайдите позже')) return 'site_error';
    return null;
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


class _YandexCaptchaDetected(BrowserAccessDenied):
    """A Yandex captcha that may disappear on the next navigation attempt."""

    def __init__(self, status: int) -> None:
        super().__init__(status, "обнаружена капча Яндекс Маркета; страница недоступна.")


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
        raise _YandexCaptchaDetected(status)


def browser_profile_dir(site: str, browser: str) -> Path:
    """Keep each site/browser session outside the repository and other profiles."""
    if site not in SITES or browser not in BROWSERS:
        raise ValueError("Неизвестный сайт или браузер для профиля Playwright.")
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    browser_slug = ("yandex", "chrome", "edge", "firefox")[BROWSERS.index(browser)]
    site_slug = site.replace(".", "_").replace("-", "_")
    return base / "WebParser" / "BrowserProfiles" / site_slug / browser_slug


def _archive_yandex_saved_tabs(profile_dir: Path) -> None:
    """Keep old tabs recoverable while preventing Yandex's stalled restore."""
    sessions = profile_dir / "Default" / "Sessions"
    if sessions.is_symlink():
        raise BrowserAccessError(f"Недопустимый каталог сохранённых вкладок: {sessions}")
    if not sessions.exists():
        return
    if not sessions.is_dir() or not sessions.resolve().is_relative_to(profile_dir.resolve()):
        raise BrowserAccessError(f"Недопустимый каталог сохранённых вкладок: {sessions}")
    archive_dir = profile_dir / "WebParserSavedSessions"
    try:
        archive_dir.mkdir(exist_ok=True)
        if not archive_dir.resolve().is_relative_to(profile_dir.resolve()):
            raise BrowserAccessError(f"Недопустимый каталог архива вкладок: {archive_dir}")
        sessions.rename(archive_dir / uuid4().hex)
    except OSError as error:
        raise BrowserAccessError(
            f"Не удалось сохранить прежние вкладки Яндекс.Браузера: {error}"
        ) from error


def _browser_stealth() -> Stealth:
    """Apply minimal JavaScript stealth fixes without custom browser flags."""
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


def _chromium_socks_launch_options(
    proxy_options: dict[str, dict[str, str]],
) -> dict[str, list[str]]:
    """Filter the Playwright SOCKS DNS flag from Chromium launch arguments."""
    proxy = proxy_options.get("proxy")
    if proxy is None:
        return {}
    parsed = urlsplit(proxy["server"])
    if parsed.scheme != "socks5" or not parsed.hostname:
        return {}
    hostname = parsed.hostname
    if ":" in hostname:
        hostname = f"[{hostname}]"
    return {
        "ignore_default_args": [
            f'--host-resolver-rules="MAP * ~NOTFOUND , EXCLUDE {hostname}"',
        ],
    }


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
        proxy: dict[str, str] | None = None,
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
        self.proxy = proxy.copy() if proxy is not None else None
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._navigated_once = False
        self._proxy_relay: Socks5AuthRelay | None = None

    @property
    def playwright(self) -> Playwright:
        if self._playwright is None:
            raise BrowserAccessError("Сначала откройте сеанс Playwright.")
        return self._playwright

    @property
    def effective_proxy(self) -> dict[str, str] | None:
        """Proxy address that other requests must share with this browser session."""
        if self._playwright is None:
            raise BrowserAccessError("Сначала откройте сеанс Playwright.")
        if self.proxy is None:
            return None
        if self._proxy_relay is not None:
            return {"server": f"socks5://127.0.0.1:{self._proxy_relay.port}"}
        return self.proxy.copy()

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
            proxy_options = self._browser_proxy_options()
            if name == "Mozilla Firefox":
                firefox = self._playwright.firefox
                if not Path(firefox.executable_path).is_file():
                    raise BrowserAccessError(
                        "Сборка Firefox для Playwright не установлена. Выполните "
                        ".\\.venv\\Scripts\\python.exe -m playwright install firefox."
                    )
                if self.profile_dir is None:
                    self._browser = firefox.launch(headless=self.headless, **proxy_options)
                else:
                    self.profile_dir.mkdir(parents=True, exist_ok=True)
                    self._context = firefox.launch_persistent_context(
                        user_data_dir=str(self.profile_dir), headless=self.headless, locale="ru-RU",
                        **proxy_options,
                    )
            else:
                chromium_proxy_options = _chromium_socks_launch_options(proxy_options)
                if self.profile_dir is None:
                    self._browser = self._playwright.chromium.launch(
                        executable_path=str(binary), headless=self.headless,
                        chromium_sandbox=True, **proxy_options, **chromium_proxy_options,
                    )
                else:
                    self.profile_dir.mkdir(parents=True, exist_ok=True)
                    if name == "Яндекс Браузер":
                        _archive_yandex_saved_tabs(self.profile_dir)
                    self._context = self._playwright.chromium.launch_persistent_context(
                        user_data_dir=str(self.profile_dir), executable_path=str(binary),
                        headless=self.headless, locale="ru-RU",
                        chromium_sandbox=True,
                        timeout=_BROWSER_LAUNCH_TIMEOUT_MS,
                        **proxy_options, **chromium_proxy_options,
                    )
            if self._context is None and self._browser is not None:
                self._context = self._browser.new_context(locale="ru-RU")
            if self._context is None:
                raise BrowserAccessError("Не удалось создать контекст браузера.")
            _browser_stealth().apply_stealth_sync(self._context)
            pages = list(self._context.pages) if self.profile_dir is not None else []
            self._page = pages[0] if pages else self._context.new_page()
            for stale_page in pages[1:]:
                stale_page.close()
            self._navigated_once = False
        except BrowserAccessError:
            try:
                self.close()
            except BrowserAccessError:
                pass
            raise
        except PlaywrightTimeoutError as error:
            try:
                self.close()
            except BrowserAccessError:
                pass
            seconds = _BROWSER_LAUNCH_TIMEOUT_MS // 1000
            raise BrowserAccessError(
                f"Не удалось запустить {name}: браузер не ответил за {seconds} секунд."
            ) from error
        except (PlaywrightError, OSError) as error:
            try:
                self.close()
            except BrowserAccessError:
                pass
            raise BrowserAccessError(
                f"Не удалось запустить {name} через Playwright: {self._safe_error(error)}"
            ) from error
        return self

    def _browser_proxy_options(self) -> dict[str, dict[str, str]]:
        if self.proxy is None:
            return {}
        proxy = self.proxy.copy()
        parsed = urlsplit(proxy.get("server", ""))
        if parsed.scheme == "socks5" and proxy.get("username"):
            relay: Socks5AuthRelay | None = None
            try:
                relay = Socks5AuthRelay(
                    proxy["server"], proxy["username"], proxy["password"],
                )
                port = relay.start()
            except (OSError, RuntimeError, ValueError) as error:
                if relay is not None:
                    relay.close()
                raise BrowserAccessError(
                    f"Не удалось подготовить SOCKS5-прокси: {error}"
                ) from error
            self._proxy_relay = relay
            proxy = {"server": f"socks5://127.0.0.1:{port}"}
        return {"proxy": proxy}

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
                if (_is_yandex_market_url(url)
                        and urlsplit(url).path.lower().startswith("/search")
                        and page.evaluate(_YANDEX_SEARCH_STATE) == "site_error"):
                    last_problem = "Яндекс Маркет показал страницу «Что-то не сработало»"
                    continue
                html = page.content()
                self._check_cancelled()
                if not html.strip():
                    last_problem = "пустой HTML"
                    continue
                return PageSnapshot(url, page.url, response.status, html, attempt)
            except _YandexCaptchaDetected as error:
                if attempt < self.attempts:
                    continue
                raise BrowserAccessDenied(
                    error.status,
                    f"обнаружена капча Яндекс Маркета; попыток: {self.attempts}; "
                    "страница недоступна.",
                ) from error
            except BrowserAccessError:
                raise
            except PlaywrightError as error:
                self._check_cancelled()
                last_problem = f"ошибка браузера: {self._safe_error(error)}"
        raise BrowserAccessError(
            f"Не удалось получить страницу {url}. Попыток: {self.attempts}; {last_problem}."
        )

    def fetch_wildberries_json(self, url: str) -> object:
        """Capture search JSON even when the initial page response is HTTP 498."""
        url = _validate_url(url)
        page = self._page
        if page is None:
            raise BrowserAccessError("Сначала откройте сеанс Playwright.")
        documents: list[object] = []
        candidate_responses: deque[Response] = deque()
        search_query = parse_qs(urlsplit(url).query).get("search", [""])[0]

        def is_search_json(response: Response) -> bool:
            parts = urlsplit(response.url)
            return (
                response.status == 200
                and parts.hostname in {"www.wildberries.ru", "u-search.wb.ru", "search.wb.ru"}
                and "/exactmatch/" in parts.path
                and parts.path.endswith("/search")
                and parse_qs(parts.query).get("query", [""])[0] == search_query
            )

        def capture(response: Response) -> None:
            if is_search_json(response):
                candidate_responses.append(response)

        def read_candidates() -> None:
            while candidate_responses:
                response = candidate_responses.popleft()
                try:
                    document = json.loads(response.body())
                except (PlaywrightError, UnicodeError, ValueError):
                    continue
                if not isinstance(document, dict):
                    continue
                products = document.get("products")
                data = document.get("data")
                if products is None and isinstance(data, dict):
                    products = data.get("products")
                if isinstance(products, list):
                    documents.append(document)

        page.on("response", capture)
        try:
            last_status: int | None = None
            last_problem = "не появился JSON товаров"
            for attempt in range(1, self.attempts + 1):
                self._check_cancelled()
                if self._navigated_once:
                    self._sleep(self.pause_seconds)
                    self._check_cancelled()
                self._navigated_once = True
                try:
                    navigation_response = page.goto(
                        url, wait_until="domcontentloaded", timeout=_NAVIGATION_TIMEOUT_MS,
                    )
                except PlaywrightError as error:
                    read_candidates()
                    if documents:
                        return documents[-1]
                    last_problem = f"ошибка браузера: {self._safe_error(error)}"
                    continue
                read_candidates()
                if documents:
                    return documents[-1]
                if navigation_response is None:
                    last_problem = "пустой ответ браузера"
                    continue
                last_status = navigation_response.status
                if not navigation_response.ok and navigation_response.status not in (403, 498):
                    last_problem = f"HTTP {navigation_response.status}"
                    if _retryable_status(navigation_response.status):
                        continue
                    raise BrowserAccessError(f"Сервер вернул {last_problem}.")
                wait_seconds = (
                    _WILDBERRIES_JSON_FIRST_WAIT_SECONDS if attempt == 1
                    else _WILDBERRIES_JSON_RETRY_WAIT_SECONDS
                )
                deadline = time.monotonic() + wait_seconds
                while not documents and time.monotonic() < deadline:
                    self._check_cancelled()
                    try:
                        page.wait_for_timeout(1_000)
                    except PlaywrightError as error:
                        last_problem = f"ошибка браузера: {self._safe_error(error)}"
                        break
                    read_candidates()
                if documents:
                    return documents[-1]
            if last_status in (403, 498):
                raise BrowserAccessDenied(
                    last_status, "в браузере не появился JSON товаров Wildberries.",
                )
            raise BrowserAccessError(
                f"В открытой выдаче Wildberries {last_problem}. Попыток: {self.attempts}."
            )
        finally:
            page.remove_listener("response", capture)

    def _check_cancelled(self) -> None:
        if self._cancel_requested():
            raise BrowserAccessCancelled("Обход страниц остановлен пользователем.")

    def _safe_error(self, error: Exception) -> str:
        message = str(error)
        if self.proxy is not None:
            for credential in (self.proxy.get("password", ""), self.proxy.get("username", "")):
                for secret in {credential, quote(credential, safe=""), quote_plus(credential)}:
                    if secret:
                        message = message.replace(secret, "[данные прокси скрыты]")
        return message

    def close(self) -> None:
        """Release resources in reverse order, including after partial startup."""
        context, self._context = self._context, None
        browser, self._browser = self._browser, None
        playwright, self._playwright = self._playwright, None
        proxy_relay, self._proxy_relay = self._proxy_relay, None
        self._page = None
        self._navigated_once = False
        first_error: PlaywrightError | OSError | None = None
        if context is not None and self.profile_dir is not None:
            try:
                pages = list(context.pages)
            except (PlaywrightError, OSError) as error:
                pages = []
                first_error = error
            for page in pages:
                try:
                    page.close()
                except (PlaywrightError, OSError) as error:
                    if first_error is None:
                        first_error = error
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
        if proxy_relay is not None:
            try:
                proxy_relay.close()
            except OSError as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise BrowserAccessError(
                f"Не удалось закрыть сеанс Playwright: {self._safe_error(first_error)}"
            ) from first_error
