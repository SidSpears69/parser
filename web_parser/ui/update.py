"""Background release checks and downloads for the desktop window."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from web_parser.updates import Release, UpdateError, download_installer, latest_release


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
