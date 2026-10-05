"""Launch an installed browser through a matching Selenium WebDriver."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.service import Service as ChromeService

from web_parser.config import BROWSERS
from web_parser.yandex_driver import YandexDriverError, ensure_yandex_driver


class BrowserError(RuntimeError):
    """The selected installed browser could not be started or stopped."""


class _FixedFileInfo(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in (
        "signature", "struct_version", "file_version_ms", "file_version_ls",
        "product_version_ms", "product_version_ls", "flags_mask", "flags",
        "os", "type", "subtype", "date_ms", "date_ls",
    )]


def browser_file_version(path: Path) -> str:
    """Read the Windows executable product version without starting the browser."""
    if os.name != "nt":
        raise BrowserError("Версия браузера доступна только в Windows.")
    try:
        version = ctypes.WinDLL("version", use_last_error=True)
        version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        version.GetFileVersionInfoW.restype = wintypes.BOOL
        version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
        version.VerQueryValueW.restype = wintypes.BOOL
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            raise BrowserError(f"Не удалось прочитать версию браузера: {path}")
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, data):
            raise BrowserError(f"Не удалось прочитать версию браузера: {path}")
        info_pointer = ctypes.c_void_p()
        info_size = wintypes.UINT()
        if not version.VerQueryValueW(data, "\\", ctypes.byref(info_pointer), ctypes.byref(info_size)):
            raise BrowserError(f"Не удалось прочитать версию браузера: {path}")
        if info_size.value < ctypes.sizeof(_FixedFileInfo):
            raise BrowserError(f"Неверные данные версии браузера: {path}")
        info = ctypes.cast(info_pointer, ctypes.POINTER(_FixedFileInfo)).contents
        if info.signature != 0xFEEF04BD:
            raise BrowserError(f"Неверные данные версии браузера: {path}")
        high, low = info.product_version_ms, info.product_version_ls
        if not (high or low):
            high, low = info.file_version_ms, info.file_version_ls
        parts = (high >> 16, high & 0xFFFF, low >> 16, low & 0xFFFF)
        if not any(parts):
            raise BrowserError(f"Не удалось определить версию браузера: {path}")
        return ".".join(str(part) for part in parts)
    except OSError as error:
        raise BrowserError(f"Не удалось прочитать версию браузера: {error}") from error


class BrowserSessionManager:
    """Own one browser session for a site tab until the user closes it."""

    def __init__(self) -> None:
        self._driver: webdriver.Remote | None = None

    @property
    def is_running(self) -> bool:
        return self._driver is not None

    def launch(self, name: str, binary: Path) -> str:
        """Start an installed browser and return its browser/driver description."""
        if self.is_running:
            raise BrowserError("Браузер уже запущен на этой вкладке.")
        if name not in BROWSERS:
            raise BrowserError(f"Неизвестный браузер: {name}")
        binary = Path(binary)
        if not binary.is_file():
            raise BrowserError(f"Исполняемый файл браузера не найден: {binary}")

        try:
            installed_version: str | None = None
            if name == "Яндекс Браузер":
                installed_version = browser_file_version(binary)
                driver_path = ensure_yandex_driver(installed_version)
                options = webdriver.ChromeOptions()
                options.binary_location = str(binary)
                driver = webdriver.Chrome(
                    service=ChromeService(executable_path=str(driver_path)), options=options,
                )
            elif name == "Google Chrome":
                options = webdriver.ChromeOptions()
                options.binary_location = str(binary)
                driver = webdriver.Chrome(options=options)
            elif name == "Microsoft Edge":
                options = webdriver.EdgeOptions()
                options.binary_location = str(binary)
                driver = webdriver.Edge(options=options)
            else:
                options = webdriver.FirefoxOptions()
                options.binary_location = str(binary)
                driver = webdriver.Firefox(options=options)
            self._driver = driver
            version = installed_version or str(driver.capabilities.get("browserVersion", "неизвестна"))
            service_path = getattr(driver.service, "path", None) or "неизвестен"
            return f"{name} {version}; драйвер: {service_path}"
        except BrowserError:
            raise
        except (OSError, WebDriverException, YandexDriverError) as error:
            try:
                self.close()
            except BrowserError:
                pass
            raise BrowserError(
                f"Не удалось запустить {name}: {error}. "
                "Проверьте версию браузера и доступность драйвера."
            ) from error

    def close(self) -> None:
        """Close only the WebDriver session owned by this tab."""
        driver, self._driver = self._driver, None
        if driver is None:
            return
        try:
            driver.quit()
        except (OSError, WebDriverException) as error:
            raise BrowserError(f"Не удалось закрыть браузер: {error}") from error
