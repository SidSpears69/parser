"""Source JSON loading, link previews, and result links for one site."""

from datetime import datetime

from PySide6.QtCore import Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QTableWidgetItem

from web_parser.images import SavedImage
from web_parser.ozon import OzonProduct
from web_parser.product_links import ProductLinks
from web_parser.ui.links import LinksPreviewTable
from web_parser.ui.results import MarketplaceResultsTable
from web_parser.ui.site_page.base import SitePageBase
from web_parser.ui.workers import ProductLinksFetchThread
from web_parser.wildberries import WildberriesProduct
from web_parser.yandex_market import YandexMarketProduct


class SiteLinksPage(SitePageBase):
    @property
    def product_links(self) -> tuple[ProductLinks, ...]:
        """Current parsed links for this site, in source order."""
        return self._product_links

    @property
    def wildberries_results(self) -> dict[str, tuple[WildberriesProduct, ...]]:
        return self._wildberries_results.copy()

    @property
    def ozon_results(self) -> dict[str, tuple[OzonProduct, ...]]:
        return self._ozon_results.copy()

    @property
    def yandex_results(self) -> dict[str, tuple[YandexMarketProduct, ...]]:
        return self._yandex_results.copy()

    @property
    def saved_images(self) -> dict[tuple[str, str], tuple[SavedImage, ...]]:
        return self._saved_images.copy()

    def _clear_ozon_results(self) -> None:
        self._ozon_results = {}
        self.ozon_table.reset_link_hover()
        self.ozon_table.setRowCount(0)
        self.ozon_hint.setText("После запуска здесь появятся первые шесть товаров каждой выдачи.")

    def _clear_wildberries_results(self) -> None:
        self._wildberries_results = {}
        self.wildberries_table.reset_link_hover()
        self.wildberries_table.setRowCount(0)
        self.wildberries_hint.setText("После запуска здесь появятся первые шесть товаров каждой выдачи.")

    def _clear_yandex_results(self) -> None:
        self._yandex_results = {}
        self.yandex_table.reset_link_hover()
        self.yandex_table.setRowCount(0)
        self.yandex_hint.setText("После запуска здесь появятся первые шесть товаров каждой выдачи.")

    @property
    def links_fetch_in_progress(self) -> bool:
        return self._links_thread is not None and self._links_thread.isRunning()

    @property
    def links_fetch_pending(self) -> bool:
        return self._links_thread is not None

    def _clear_product_links(self) -> None:
        self._pending_scheduled_walk = False
        if self._links_thread is None:
            self._product_links = ()
            self._clear_ozon_results()
            self._clear_wildberries_results()
            self._clear_yandex_results()
            self._saved_images = {}
            self.links_preview.reset_link_hover()
            self.links_preview.setRowCount(0)
            self.links_status.setText("Ссылки ещё не загружены.")
            self.links_preview_hint.setText("Загрузите JSON, чтобы увидеть ID и ссылки товаров.")
            if self._walk_thread is None:
                self.state_label.setText("Не запущен")
                self.run_status.setText("Загрузите ссылки из JSON, затем нажмите «Запустить».")
        self._update_links_controls()

    def _update_links_controls(self) -> None:
        fetching = self._links_thread is not None
        walking = self._walk_thread is not None
        self.source_url.setEnabled(not fetching and not walking)
        self.token.setEnabled(not fetching and not walking)
        self.load_links_button.setEnabled(
            bool(self.source_url.text().strip() and self.token.text())
            and not fetching and not walking and not self._shutting_down
            and not self._emergency_stopped
        )
        self._update_browser_controls()

    def _fetch_product_links(self) -> None:
        if self._links_thread is not None or self._walk_thread is not None or self._emergency_stopped:
            return
        source_url = self.source_url.text().strip()
        token = self.token.text()
        if not source_url or not token:
            return
        self._product_links = ()
        self._clear_ozon_results()
        self._clear_wildberries_results()
        self._clear_yandex_results()
        self._saved_images = {}
        self.links_preview.reset_link_hover()
        self.links_preview.setRowCount(0)
        self._update_browser_controls()
        self.links_preview_hint.setText("Ожидание ответа источника…")
        self.links_status.setText("Загрузка JSON и разбор ссылок…")
        self.state_label.setText("Не запущен")
        self.run_status.setText("Загрузка ссылок из JSON…")
        thread = ProductLinksFetchThread(source_url, token, self)
        self._links_thread = thread
        thread.finished.connect(self._product_links_fetched)
        thread.start()
        self._update_links_controls()

    @Slot()
    def _product_links_fetched(self) -> None:
        thread = self._links_thread
        if thread is None:
            return
        self._links_thread = None
        if self._shutting_down or self._emergency_stopped:
            thread.token = ""
            thread.deleteLater()
            self.links_fetch_finished.emit()
            return
        source_changed = (
            self.source_url.text().strip() != thread.source_url
            or self.token.text() != thread.token
        )
        if source_changed:
            self.links_status.setText("Источник или токен изменились во время загрузки. Загрузите ссылки снова.")
            self.links_preview_hint.setText("Данные прежнего источника отброшены.")
            self.run_status.setText("Загрузите ссылки для текущего источника.")
        elif thread.error is not None or thread.links is None:
            message = thread.error or "Загрузка прервана из-за внутренней ошибки."
            self.links_status.setText(f"Не удалось загрузить ссылки: {message}")
            self.links_preview_hint.setText("Проверьте URL и токен доступа.")
            self.run_status.setText("Ссылки не загружены. Проверьте источник и токен.")
            self.log.append_error("", "", f"Сбор ссылок: {message}")
        else:
            self._product_links = tuple(thread.links)
            self._show_product_links()
            self.last_load_label.setText(
                f"Последняя успешная загрузка: {datetime.now():%d.%m.%Y %H:%M:%S}"
            )
        self._update_links_controls()
        thread.token = ""
        thread.deleteLater()
        if self._pending_scheduled_walk:
            self._pending_scheduled_walk = False
            if self._product_links:
                self._start_walk()
            else:
                self.log.append_error("", "", "Расписание анализа: ссылки не загружены; запуск пропущен.")
        self.links_fetch_finished.emit()

    def _show_product_links(self) -> None:
        total = len(self._product_links)
        shown = min(total, 20)
        self.links_preview.reset_link_hover()
        self.links_preview.setRowCount(shown)
        for row, links in enumerate(self._product_links[:shown]):
            values = (
                links.product_id, links.ozon_url, links.wildberries_url,
                links.yandex_market_url,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value or "—")
                if value:
                    item.setToolTip(value)
                if column > 0 and value:
                    item.setData(Qt.ItemDataRole.UserRole, value)
                    item.setForeground(LinksPreviewTable.LINK_COLOR)
                self.links_preview.setItem(row, column, item)
        self.links_status.setText(f"Загружено товаров: {total}")
        if total:
            self.run_status.setText(f"Готово к обходу: {total} товаров, {total * 3} страниц.")
        else:
            self.run_status.setText("В загруженном JSON нет ссылок для обхода.")
        self.links_preview_hint.setText(
            f"Показаны первые {shown} из {total} товаров." if total > shown
            else "Все загруженные товары показаны в таблице." if total
            else "В источнике нет товаров."
        )

    @Slot(int, int)
    def _open_preview_link(self, row: int, column: int) -> None:
        if column == 0:
            return
        item = self.links_preview.item(row, column)
        if item is not None:
            url = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(url, str):
                QDesktopServices.openUrl(QUrl(url))

    def _open_wildberries_photo(self, row: int, column: int) -> None:
        self._open_results_photo(self.wildberries_table, row, column)

    def _open_ozon_photo(self, row: int, column: int) -> None:
        self._open_results_photo(self.ozon_table, row, column)

    def _open_yandex_photo(self, row: int, column: int) -> None:
        self._open_results_photo(self.yandex_table, row, column)

    @staticmethod
    def _open_results_photo(table: MarketplaceResultsTable, row: int, column: int) -> None:
        if column != 6:
            return
        item = table.item(row, column)
        url = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(url, str):
            QDesktopServices.openUrl(QUrl(url))

    def shutdown_links(self) -> None:
        """Release a completed fetch worker when the window closes."""
        self._shutting_down = True
        self.scheduler.stop()
        if self._links_thread is not None:
            self._links_thread.wait()
