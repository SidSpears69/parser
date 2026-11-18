"""Browser walk lifecycle, results, and error events for one site."""

from pathlib import Path
from typing import TypeVar

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import QLabel

from web_parser import config
from web_parser.browser_discovery import BrowserInstallation
from web_parser.ozon import OzonProduct
from web_parser.ui.results import MarketplaceResultsTable
from web_parser.ui.site_page.base import BROWSERS, _soft_wrap
from web_parser.ui.site_page.links import SiteLinksPage
from web_parser.ui.workers import BrowserWalkThread
from web_parser.wildberries import WildberriesProduct
from web_parser.yandex_market import YandexMarketProduct

_Product = TypeVar("_Product", OzonProduct, WildberriesProduct, YandexMarketProduct)


class SiteWalkPage(SiteLinksPage):
    @property
    def walk_pending(self) -> bool:
        """Whether a traversal worker still belongs to this page."""
        return self._walk_thread is not None

    def prepare_walk_shutdown(self) -> None:
        self._shutting_down = True
        self.scheduler.stop()
        if self._walk_thread is not None:
            self._walk_thread.stop()

    def set_available_browsers(self, available: dict[str, BrowserInstallation]) -> None:
        """Refresh installation hints while retaining canonical config values."""
        self._available_browsers = available.copy()
        for index, browser_name in enumerate(BROWSERS):
            installation = available.get(browser_name)
            suffix = (
                "сборка Playwright" if browser_name == "Mozilla Firefox"
                else "найден" if installation else "не найден"
            )
            self.browser.setItemText(index, f"{browser_name} — {suffix}")
            self.browser.setItemData(
                index,
                "Используется сборка Playwright" if browser_name == "Mozilla Firefox"
                else str(installation.path) if installation else "",
                Qt.ItemDataRole.ToolTipRole,
            )
        self._update_browser_controls()

    def _selected_browser_name(self) -> str:
        return str(self.browser.currentData())

    def _selected_installation(self) -> BrowserInstallation | None:
        name = self._selected_browser_name()
        if name == "Mozilla Firefox":
            return self._available_browsers.get(name) or BrowserInstallation(name, Path())
        return self._available_browsers.get(name)

    def _update_browser_controls(self) -> None:
        name = self._selected_browser_name()
        installation = self._available_browsers.get(name)
        path_text = (
            "Firefox: используется сборка Playwright; наличие проверяется при запуске."
            if name == "Mozilla Firefox" else
            f"Путь: {_soft_wrap(str(installation.path))}"
            if installation else "Установленный браузер не найден."
        )
        self.browser_path_label.setText(path_text)
        self.browser_path_label.setToolTip(str(installation.path) if installation and name != "Mozilla Firefox" else "")
        walking = self._walk_thread is not None
        self.browser.setEnabled(not walking and not self._shutting_down)
        self.pause.setEnabled(not walking and not self._shutting_down)
        self.refresh_browsers_button.setEnabled(not walking and not self._shutting_down)
        for widget in (
            self.ftp_host, self.ftp_port, self.ftp_user, self.ftp_password, self.remote_path,
        ):
            widget.setEnabled(not walking and not self._shutting_down)
        self.start_button.setEnabled(
            bool(self._product_links) and self._selected_installation() is not None
            and not walking and self._links_thread is None and not self._shutting_down
            and not self._emergency_stopped
        )
        self.stop_button.setEnabled(
            walking and not self._shutting_down
            and self._walk_thread is not None and not self._walk_thread.stop_requested
        )

    def _start_walk(self) -> None:
        installation = self._selected_installation()
        if (
            installation is None or not self._product_links or self._walk_thread is not None
            or self._links_thread is not None or self._shutting_down or self._emergency_stopped
        ):
            return
        try:
            proxy = self._proxy_provider()
        except config.ConfigError as error:
            self.state_label.setText("Ошибка прокси")
            self.run_status.setText(str(error))
            return
        thread = BrowserWalkThread(
            self.site, installation, self._product_links, self.pause.value(), self,
            proxy=proxy, ftp_settings=self.settings()["ftp"],
        )
        self._walk_thread = thread
        self._clear_ozon_results()
        self._clear_wildberries_results()
        self._clear_yandex_results()
        self._saved_images = {}
        self.ftp_status.setText("Ожидание завершения сбора данных…")
        self.state_label.setText("Запуск…")
        self.run_status.setText(
            f"Запускается {installation.name}; заданий в очереди: "
            f"{len(self._product_links) * 3}."
        )
        thread.page_started.connect(self._walk_page_started)
        thread.page_loaded.connect(self._walk_page_loaded)
        thread.page_failed.connect(self._walk_page_failed)
        thread.layout_notice.connect(self._walk_layout_notice)
        thread.layout_failure.connect(self._walk_layout_failure)
        thread.ozon_parsed.connect(self._walk_ozon_parsed)
        thread.wildberries_parsed.connect(self._walk_wildberries_parsed)
        thread.yandex_parsed.connect(self._walk_yandex_parsed)
        thread.image_failed.connect(self._walk_image_failed)
        thread.ozon_recovery_started.connect(self._walk_ozon_recovery_started)
        thread.ozon_direct_started.connect(self._walk_ozon_direct_started)
        thread.yandex_direct_started.connect(self._walk_yandex_direct_started)
        thread.ftp_upload_started.connect(self._walk_ftp_upload_started)
        thread.browser_notice.connect(self._walk_browser_notice)
        thread.finished.connect(self._walk_finished)
        thread.start()
        self._update_links_controls()
        self.walk_state_changed.emit()

    def _stop_walk(self) -> None:
        thread = self._walk_thread
        if thread is None:
            return
        thread.stop()
        self.state_label.setText("Остановка…")
        self.run_status.setText("Ожидание завершения текущей загрузки…")
        self._update_browser_controls()

    @Slot(str, str, str, int, int)
    def _walk_page_started(self, product_id: str, marketplace: str, url: str, index: int, total: int) -> None:
        if self._shutting_down:
            return
        self.state_label.setText(f"Обход {index}/{total}")
        action = (
            "Запрашивается JSON Wildberries"
            if marketplace == "Wildberries" else f"Открывается {marketplace}"
        )
        self.run_status.setText(f"{action}, товар {product_id}: {_soft_wrap(url)}")
        row = (index - 1) // 3
        if row < self.links_preview.rowCount():
            self.links_preview.setCurrentCell(row, (index - 1) % 3 + 1)

    @Slot(str, str, int, int, int, bool)
    def _walk_page_loaded(
        self, product_id: str, marketplace: str, index: int, total: int, status: int,
        direct: bool,
    ) -> None:
        if not self._shutting_down:
            if marketplace == "Wildberries":
                detail = f"JSON: {len(self._wildberries_results.get(product_id, ()))} товаров"
            elif marketplace == "Ozon":
                access_detail = (
                    "страница открылась после проверки браузера (первичный HTTP 403)"
                    if status == 403 else f"HTTP {status}"
                )
                detail = f"карточек: {len(self._ozon_results.get(product_id, ()))}; {access_detail}"
            else:
                detail = f"карточек: {len(self._yandex_results.get(product_id, ()))}; HTTP {status}"
            if direct:
                detail += "; без прокси"
            self.run_status.setText(f"Загружено {index}/{total}: {marketplace}, товар {product_id}, {detail}.")

    @Slot(str)
    def _walk_browser_notice(self, message: str) -> None:
        if not self._shutting_down:
            self.log.append_error("", "", f"Браузер: {message}")

    @Slot(str)
    def _walk_ozon_recovery_started(self, product_id: str) -> None:
        if not self._shutting_down:
            self.run_status.setText(
                f"Ozon показал капчу для товара {product_id}; повторная попытка в новом профиле браузера…"
            )

    @Slot(str)
    def _walk_ozon_direct_started(self, product_id: str) -> None:
        if not self._shutting_down:
            self.run_status.setText(
                f"Ozon показал капчу для товара {product_id} и в новом профиле; "
                "повторная попытка без прокси…"
            )

    @Slot(str)
    def _walk_yandex_direct_started(self, product_id: str) -> None:
        if not self._shutting_down:
            self.run_status.setText(
                f"Яндекс Маркет не открылся через прокси для товара {product_id}; "
                "повторная попытка без прокси…"
            )

    @Slot()
    def _walk_ftp_upload_started(self) -> None:
        if not self._shutting_down:
            self.ftp_status.setText("Загружаются фотографии и JSON на FTP…")
            self.run_status.setText("Сбор завершён; идёт выгрузка на FTP…")

    def _show_parsed_products(
        self, product_id: str, products: tuple[_Product, ...],
        results: dict[str, tuple[_Product, ...]],
        table: MarketplaceResultsTable, hint: QLabel,
    ) -> None:
        if self._shutting_down:
            return
        results[product_id] = products
        table.append_products(product_id, products)
        hint.setText(
            f"Собрано товаров: {sum(len(items) for items in results.values())}; "
            f"показано: {table.rowCount()}."
        )

    @Slot(str, object)
    def _walk_ozon_parsed(
        self, product_id: str, products: tuple[OzonProduct, ...],
    ) -> None:
        self._show_parsed_products(
            product_id, products, self._ozon_results, self.ozon_table, self.ozon_hint,
        )

    @Slot(str, object)
    def _walk_wildberries_parsed(
        self, product_id: str, products: tuple[WildberriesProduct, ...],
    ) -> None:
        self._show_parsed_products(
            product_id, products, self._wildberries_results,
            self.wildberries_table, self.wildberries_hint,
        )

    @Slot(str, object)
    def _walk_yandex_parsed(
        self, product_id: str, products: tuple[YandexMarketProduct, ...],
    ) -> None:
        self._show_parsed_products(
            product_id, products, self._yandex_results, self.yandex_table,
            self.yandex_hint,
        )

    @Slot(str, str, str, str)
    def _walk_image_failed(
        self, product_id: str, marketplace: str, image_url: str, message: str,
    ) -> None:
        if not self._shutting_down:
            self.log.append_error(product_id, image_url, f"{marketplace}: {message}")

    @Slot(str, str, str, int, int, str)
    def _walk_page_failed(
        self, product_id: str, marketplace: str, url: str, index: int, total: int, message: str,
    ) -> None:
        if not self._shutting_down:
            self.run_status.setText(f"Ошибка {index}/{total}: {marketplace}, товар {product_id}.")
            self.log.append_error(product_id, url, f"{marketplace}: {message}")

    @Slot(str, str, str, str)
    def _walk_layout_notice(
        self, product_id: str, marketplace: str, url: str, message: str,
    ) -> None:
        if not self._shutting_down:
            self.log.append_error(product_id, url, f"{marketplace}: Предупреждение: {message}")

    @Slot(str, str, str, str)
    def _walk_layout_failure(
        self, product_id: str, marketplace: str, url: str, message: str,
    ) -> None:
        if not self._shutting_down:
            self.fatal_layout.emit(self.site, product_id, marketplace, url, message)

    @Slot()
    def _walk_finished(self) -> None:
        thread = self._walk_thread
        if thread is None:
            return
        self._walk_thread = None
        self._saved_images = thread.saved_images.copy()
        if not self._shutting_down:
            if thread.ftp_result is not None:
                result = thread.ftp_result
                self.ftp_status.setText(
                    f"Выгружено: {result.products} ID, {result.uploaded_images} фото; "
                    f"JSON: {_soft_wrap(result.remote_json)}"
                )
            elif thread.ftp_error is not None:
                self.ftp_status.setText(f"Ошибка выгрузки: {thread.ftp_error}")
                self.log.append_error("", "", f"FTP: {thread.ftp_error}")
            elif self._emergency_stopped:
                self.ftp_status.setText("Выгрузка не выполнена: аварийная остановка.")
            elif thread.cancelled:
                self.ftp_status.setText("Выгрузка не завершена: обход остановлен.")
            else:
                self.ftp_status.setText("FTP не настроен; данные остались локально.")
            if self._emergency_stopped:
                self.state_label.setText("Аварийная остановка")
                self.run_status.setText("Структура выдачи изменилась. Запуски и расписания отключены.")
            elif thread.error:
                self.state_label.setText("Ошибка")
                self.run_status.setText(f"Не удалось выполнить обход: {thread.error}")
                self.log.append_error("", "", f"Обход страниц: {thread.error}")
            else:
                self.state_label.setText("Остановлен" if thread.cancelled else "Завершён")
                ending = "Остановлено" if thread.cancelled else "Обход завершён"
                self.run_status.setText(
                    f"{ending}. Загружено: {thread.loaded}; ошибок: {thread.failed}; "
                    f"товаров Ozon: {sum(len(items) for items in self._ozon_results.values())}; "
                    f"товаров Wildberries: {sum(len(items) for items in self._wildberries_results.values())}; "
                    f"товаров Яндекс Маркета: {sum(len(items) for items in self._yandex_results.values())}; "
                    f"фото: {sum(len(items) for items in self._saved_images.values())}; "
                    f"ошибок фото: {thread.image_failures}."
                    + (
                        f" FTP: {thread.ftp_result.uploaded_images} фото и JSON выгружены."
                        if thread.ftp_result is not None else
                        " FTP: ошибка выгрузки."
                        if thread.ftp_error is not None else ""
                    )
                )
            self._update_links_controls()
        thread.deleteLater()
        self.walk_state_changed.emit()
        self.walk_finished.emit()
