"""Background release checks and downloads for the desktop window."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from web_parser.updates import Release, UpdateError, download_installer, latest_release


class UpdateProgressDialog(QDialog):
    """Show download activity while the main window remains responsive."""

    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Загрузка обновления")
        self.setModal(False)
        self.setMinimumWidth(440)
        self._cancelling = False

        layout = QVBoxLayout(self)
        self.status_label = QLabel("Подключение к серверу обновлений…")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.clicked.connect(self._request_cancel)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(self.cancel_button)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)
        layout.addLayout(actions)

    def show_progress(self, received: int, total: int) -> None:
        if total <= 0:
            return
        percent = min(100, received * 100 // total)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(percent)
        if self._cancelling:
            return
        if received >= total:
            self.status_label.setText("Загрузка завершена. Проверка установщика…")
            self.cancel_button.setEnabled(False)
        else:
            megabyte = 1024 * 1024
            self.status_label.setText(
                f"Скачано {received / megabyte:.1f} из {total / megabyte:.1f} МБ"
            )

    def _request_cancel(self) -> None:
        if self._cancelling:
            return
        self._cancelling = True
        self.cancel_button.setEnabled(False)
        self.status_label.setText("Отмена загрузки…")
        self.cancel_requested.emit()

    def reject(self) -> None:
        self._request_cancel()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._request_cancel()
        event.ignore()


class UpdateThread(QThread):
    release_found = Signal(object)
    installer_ready = Signal(str)
    download_progress = Signal(int, int)
    failed = Signal(str)

    def __init__(self, release: Release | None = None, parent=None):
        super().__init__(parent)
        self.release = release

    def run(self) -> None:
        try:
            if self.release is None:
                self.release_found.emit(latest_release())
            else:
                installer = download_installer(
                    self.release,
                    progress=lambda received, total: self.download_progress.emit(received, total),
                    cancel_requested=self.isInterruptionRequested,
                )
                self.installer_ready.emit(str(installer))
        except UpdateError as error:
            self.failed.emit(str(error))
