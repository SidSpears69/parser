"""Playwright session lifecycle and single-page navigation."""

from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from typing import Callable, Self
from urllib.parse import quote, quote_plus, urlsplit

from playwright.sync_api import Browser, BrowserContext, Error as PlaywrightError
from playwright.sync_api import Page, Playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
from playwright_stealth import Stealth

from web_parser.browser_access.models import BrowserAccessCancelled, BrowserAccessError
from web_parser.browser_access.profiles import archive_yandex_saved_tabs, retry_yandex_profile
from web_parser.browser_discovery import BrowserInstallation
from web_parser.config import BROWSERS
from web_parser.socks5_relay import Socks5AuthRelay


_MAX_ATTEMPTS = 3
_BROWSER_LAUNCH_TIMEOUT_MS = 20_000


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


class BrowserSession:
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
        playwright: Playwright | None = None,
        strict_access: bool = False,
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
        self._strict_access = strict_access
        self._provided_playwright = playwright
        self._owns_playwright = playwright is None
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._navigated_once = False
        self._proxy_relay: Socks5AuthRelay | None = None
        self._temporary_profile: TemporaryDirectory | None = None
        self.launch_notice: str | None = None

    @property
    def playwright(self) -> Playwright:
        if self._playwright is None:
            raise BrowserAccessError("Сначала откройте сеанс Playwright.")
        return self._playwright

    @property
    def page(self) -> Page:
        """Current browser tab, for reading the rendered marketplace DOM."""
        if self._page is None:
            raise BrowserAccessError("Сначала откройте сеанс Playwright.")
        return self._page

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

    def __enter__(self) -> Self:
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
            self._playwright = (
                sync_playwright().start() if self._provided_playwright is None
                else self._provided_playwright
            )
            proxy_options = self._browser_proxy_options()
            if name == "Mozilla Firefox":
                firefox = self._playwright.firefox
                if not Path(firefox.executable_path).is_file():
                    if getattr(sys, "frozen", False):
                        raise BrowserAccessError(
                            "Сборка Firefox отсутствует в установленном приложении. "
                            "Переустановите Web Parser из актуального установщика; "
                            "рядом с WebParser.exe должен быть каталог ms-playwright с Firefox."
                        )
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
                        archive_yandex_saved_tabs(self.profile_dir)
                    chromium = self._playwright.chromium

                    def launch_persistent(profile: str) -> BrowserContext:
                        return chromium.launch_persistent_context(
                            user_data_dir=profile, executable_path=str(binary),
                            headless=self.headless, locale="ru-RU",
                            chromium_sandbox=True,
                            timeout=_BROWSER_LAUNCH_TIMEOUT_MS,
                            **proxy_options, **chromium_proxy_options,
                        )

                    try:
                        self._context = launch_persistent(str(self.profile_dir))
                    except PlaywrightError as error:
                        if name != "Яндекс Браузер" or not retry_yandex_profile(error):
                            raise
                        self._temporary_profile = TemporaryDirectory(
                            prefix="web-parser-yandex-profile-", ignore_cleanup_errors=True,
                        )
                        try:
                            self._context = launch_persistent(self._temporary_profile.name)
                        except PlaywrightError as retry_error:
                            try:
                                self._browser = chromium.launch(
                                    executable_path=str(binary), headless=self.headless,
                                    chromium_sandbox=True, timeout=_BROWSER_LAUNCH_TIMEOUT_MS,
                                    **proxy_options, **chromium_proxy_options,
                                )
                            except PlaywrightError as clean_error:
                                raise BrowserAccessError(
                                    "Не удалось запустить Яндекс Браузер. "
                                    f"Основной профиль: {self._launch_problem(error)}; "
                                    f"временный профиль: {self._launch_problem(retry_error)}; "
                                    f"чистый сеанс: {self._launch_problem(clean_error)}."
                                ) from clean_error
                            self.launch_notice = (
                                "Основной и временный профили Яндекс Браузера не открылись; "
                                "обход запущен в чистом сеансе без сохранённых cookies."
                            )
                        else:
                            self.launch_notice = (
                                "Основной профиль Яндекс Браузера не открылся: "
                                f"{self.profile_dir}. Обход запущен с временным профилем "
                                "без сохранённых cookies."
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

    def _launch_problem(self, error: PlaywrightError) -> str:
        message = self._safe_error(error).strip()
        if not message:
            return type(error).__name__
        lines = message.splitlines()
        parts = [lines[0].split(": ", 1)[-1].strip()]
        stderr = next((line.split("[err]", 1)[1].strip() for line in lines if "[err]" in line), "")
        if stderr and "\ufffd" not in stderr:
            parts.append(f"stderr: {stderr[:180]}")
        exit_line = next((line for line in lines if "process did exit:" in line), "")
        if "exitCode=" in exit_line:
            exit_code = exit_line.split("exitCode=", 1)[1].split(",", 1)[0].strip()
            parts.append(f"exitCode={exit_code[:12]}")
        return "; ".join(parts)[:400]

    def close(self) -> None:
        """Release resources in reverse order, including after partial startup."""
        context, self._context = self._context, None
        browser, self._browser = self._browser, None
        playwright = self._playwright if self._owns_playwright else None
        self._playwright = None
        proxy_relay, self._proxy_relay = self._proxy_relay, None
        temporary_profile, self._temporary_profile = self._temporary_profile, None
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
        if temporary_profile is not None:
            try:
                temporary_profile.cleanup()
            except OSError as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise BrowserAccessError(
                f"Не удалось закрыть сеанс Playwright: {self._safe_error(first_error)}"
            ) from first_error
