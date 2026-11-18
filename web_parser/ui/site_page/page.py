"""Public source-site widget: scheduling, settings, and UI wiring."""

from datetime import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QTime
from PySide6.QtWidgets import QWidget

from web_parser.scheduler import Schedule
from web_parser.ui.site_page.walk import SiteWalkPage


class SitePage(SiteWalkPage):
    def __init__(
        self, site: str, parent: QWidget | None = None,
        *, proxy_provider: Callable[[], dict[str, str] | None] = lambda: None,
        proxy_secrets: Callable[[], tuple[str, str]] = lambda: ("", ""),
        extra_secrets: Callable[[], tuple[str, ...]] = lambda: (),
        log_root: Path | None = None,
    ):
        super().__init__(
            site, parent, proxy_provider=proxy_provider, proxy_secrets=proxy_secrets,
            extra_secrets=extra_secrets, log_root=log_root,
        )
        self.source_schedule_enabled.toggled.connect(self._toggle_source_schedule)
        self.schedule_enabled.toggled.connect(self._toggle_schedule)
        self._toggle_source_schedule(False)
        self._toggle_schedule(False)
        self.links_preview.cellClicked.connect(self._open_preview_link)
        self.ozon_table.cellClicked.connect(self._open_ozon_photo)
        self.wildberries_table.cellClicked.connect(self._open_wildberries_photo)
        self.yandex_table.cellClicked.connect(self._open_yandex_photo)
        self.start_button.clicked.connect(self._start_walk)
        self.stop_button.clicked.connect(self._stop_walk)
        self.browser.currentIndexChanged.connect(self._update_browser_controls)
        self.refresh_browsers_button.clicked.connect(lambda: self.refresh_browsers_requested.emit())
        self.source_url.textChanged.connect(self._clear_product_links)
        self.token.textChanged.connect(self._clear_product_links)
        self.load_links_button.clicked.connect(self._fetch_product_links)
        self.scheduler.source_due.connect(self._scheduled_source_due)
        self.scheduler.analysis_due.connect(self._scheduled_analysis_due)
        self.scheduler.changed.connect(self._update_schedule_status)
        self.source_schedule_enabled.toggled.connect(self._configure_source_schedule)
        self.source_schedule_time.timeChanged.connect(self._configure_source_schedule)
        self.source_schedule_interval.valueChanged.connect(self._configure_source_schedule)
        self.source_schedule_unit.currentIndexChanged.connect(self._configure_source_schedule)
        self.schedule_enabled.toggled.connect(self._configure_analysis_schedule)
        self.start_time.timeChanged.connect(self._configure_analysis_schedule)
        self.interval.valueChanged.connect(self._configure_analysis_schedule)
        self.interval_unit.currentIndexChanged.connect(self._configure_analysis_schedule)
        self._configure_source_schedule()
        self._configure_analysis_schedule()

    def emergency_stop(self) -> None:
        """Stop all automatic work until an operator explicitly clears the stop."""
        self._emergency_stopped = True
        self._pending_scheduled_walk = False
        self.schedule_enabled.setChecked(False)
        self.source_schedule_enabled.setChecked(False)
        self.schedule_enabled.setEnabled(False)
        self.source_schedule_enabled.setEnabled(False)
        self.scheduler.stop()
        if self._walk_thread is not None:
            self._walk_thread.stop()
        self.state_label.setText("Аварийная остановка")
        self.run_status.setText("Структура выдачи изменилась. Запуски и расписания отключены.")
        self._update_links_controls()

    def clear_emergency_stop(self) -> None:
        self._emergency_stopped = False
        self.schedule_enabled.setEnabled(True)
        self.source_schedule_enabled.setEnabled(True)
        self._configure_source_schedule()
        self._configure_analysis_schedule()
        self.state_label.setText("Не запущен")
        self.run_status.setText("Аварийная остановка снята. Расписания включите вручную.")
        self._update_links_controls()

    def resizeEvent(self, event) -> None:
        # QScrollArea may keep the page wider than its viewport to satisfy a
        # two-column minimum width; use the visible width to break that cycle.
        viewport = self.parentWidget()
        compact = (viewport.width() if viewport is not None else self.width()) < 1200
        if compact != self._compact:
            self._compact = compact
            for panel in self._panels:
                self._settings_grid.removeWidget(panel)
            for index, panel in enumerate(self._panels):
                row, column = (index, 0) if compact else (index % 2, index // 2)
                self._settings_grid.addWidget(panel, row, column)
            self._settings_grid.setColumnStretch(1, 0 if compact else 1)
        super().resizeEvent(event)

    def _toggle_schedule(self, enabled: bool) -> None:
        for widget in (self.start_time, self.interval, self.interval_unit):
            widget.setEnabled(enabled)

    def _toggle_source_schedule(self, enabled: bool) -> None:
        for widget in (
            self.source_schedule_time, self.source_schedule_interval, self.source_schedule_unit,
        ):
            widget.setEnabled(enabled)

    def _configure_source_schedule(self) -> None:
        schedule = None
        if self.source_schedule_enabled.isChecked():
            qtime = self.source_schedule_time.time()
            schedule = Schedule(
                time(qtime.hour(), qtime.minute()), self.source_schedule_interval.value(),
                str(self.source_schedule_unit.currentData()),
            )
        self.scheduler.configure_source(schedule)

    def _configure_analysis_schedule(self) -> None:
        schedule = None
        if self.schedule_enabled.isChecked():
            qtime = self.start_time.time()
            schedule = Schedule(
                time(qtime.hour(), qtime.minute()), self.interval.value(),
                str(self.interval_unit.currentData()),
            )
        else:
            self._pending_scheduled_walk = False
        self.scheduler.configure_analysis(schedule)

    def _update_schedule_status(self) -> None:
        source = self.scheduler.next_source
        analysis = self.scheduler.next_analysis
        self.source_next_label.setText(
            f"Следующая загрузка: {source:%d.%m.%Y %H:%M}." if source else
            "Следующая загрузка: выключена."
        )
        self.analysis_next_label.setText(
            f"Следующий анализ: {analysis:%d.%m.%Y %H:%M}." if analysis else
            "Следующий анализ: выключен."
        )

    def _scheduled_source_due(self) -> None:
        if self._shutting_down or self._emergency_stopped:
            return
        if self._links_thread is not None or self._walk_thread is not None:
            self.log.append_error("", "", "Расписание JSON: предыдущая операция ещё идёт; запуск пропущен.")
            return
        if not self.source_url.text().strip() or not self.token.text():
            self.log.append_error("", "", "Расписание JSON: укажите URL источника и токен.")
            return
        self._fetch_product_links()

    def _scheduled_analysis_due(self) -> None:
        if self._shutting_down or self._emergency_stopped:
            return
        if self._walk_thread is not None:
            self.log.append_error("", "", "Расписание анализа: предыдущий обход ещё идёт; запуск пропущен.")
            return
        if self._selected_installation() is None:
            self.log.append_error("", "", "Расписание анализа: выбранный браузер не найден.")
            return
        if self._links_thread is not None:
            self._pending_scheduled_walk = True
            self.run_status.setText("Анализ по расписанию ожидает загрузки ссылок…")
            return
        if not self._product_links:
            if not self.source_url.text().strip() or not self.token.text():
                self.log.append_error("", "", "Расписание анализа: укажите URL источника и токен.")
                return
            self._pending_scheduled_walk = True
            self._fetch_product_links()
            return
        self._start_walk()
        if self._walk_thread is None:
            self.log.append_error("", "", "Расписание анализа: запуск обхода не удался.")

    def settings(self) -> dict:
        """Return the settings currently shown on this site's tab."""
        return {
            "site": self.site,
            "source_url": self.source_url.text().strip(),
            "token": self.token.text(),
            "source_schedule_enabled": self.source_schedule_enabled.isChecked(),
            "source_schedule_time": self.source_schedule_time.time().toString("HH:mm"),
            "source_schedule_interval": self.source_schedule_interval.value(),
            "source_schedule_unit": self.source_schedule_unit.currentData(),
            "browser": self._selected_browser_name(),
            "pause_seconds": self.pause.value(),
            "ftp": {
                "host": self.ftp_host.text().strip(), "port": self.ftp_port.value(),
                "username": self.ftp_user.text(), "password": self.ftp_password.text(),
                "remote_path": self.remote_path.text().strip(),
            },
            "schedule": {
                "enabled": self.schedule_enabled.isChecked(),
                "start_time": self.start_time.time().toString("HH:mm"),
                "interval": self.interval.value(), "unit": self.interval_unit.currentData(),
            },
        }

    def apply_settings(self, settings: dict) -> None:
        """Restore a validated site's settings to its controls."""
        self.source_url.setText(settings["source_url"])
        self.token.setText(settings["token"])
        self.source_schedule_enabled.setChecked(settings.get("source_schedule_enabled", False))
        schedule = settings["schedule"]
        self.source_schedule_time.setTime(QTime.fromString(
            settings.get("source_schedule_time", schedule["start_time"]), "HH:mm",
        ))
        self.source_schedule_interval.setValue(
            settings.get("source_schedule_interval", schedule["interval"])
        )
        self.source_schedule_unit.setCurrentIndex(self.source_schedule_unit.findData(
            settings.get("source_schedule_unit", schedule["unit"])
        ))
        self.browser.setCurrentIndex(self.browser.findData(settings["browser"]))
        self.pause.setValue(settings["pause_seconds"])
        ftp = settings["ftp"]
        self.ftp_host.setText(ftp["host"])
        self.ftp_port.setValue(ftp["port"])
        self.ftp_user.setText(ftp["username"])
        self.ftp_password.setText(ftp["password"])
        self.remote_path.setText(ftp["remote_path"])
        self.schedule_enabled.setChecked(schedule["enabled"])
        self.start_time.setTime(QTime.fromString(schedule["start_time"], "HH:mm"))
        self.interval.setValue(schedule["interval"])
        self.interval_unit.setCurrentIndex(self.interval_unit.findData(schedule["unit"]))
