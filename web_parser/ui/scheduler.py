"""Qt timer that owns two independent schedules for one site tab."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from web_parser.scheduler import Schedule, first_due, next_due


class TabScheduler(QObject):
    source_due = Signal()
    analysis_due = Signal()
    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._source: Schedule | None = None
        self._analysis: Schedule | None = None
        self.next_source: datetime | None = None
        self.next_analysis: datetime | None = None
        self._source_anchor_day: int | None = None
        self._analysis_anchor_day: int | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.tick)

    def configure_source(self, schedule: Schedule | None, *, now: datetime | None = None) -> None:
        self._source = schedule
        self.next_source = first_due(now or datetime.now(), schedule) if schedule else None
        self._source_anchor_day = self.next_source.day if self.next_source else None
        self._update_timer()
        self.changed.emit()

    def configure_analysis(self, schedule: Schedule | None, *, now: datetime | None = None) -> None:
        self._analysis = schedule
        self.next_analysis = first_due(now or datetime.now(), schedule) if schedule else None
        self._analysis_anchor_day = self.next_analysis.day if self.next_analysis else None
        self._update_timer()
        self.changed.emit()

    def _update_timer(self) -> None:
        if self._source is not None or self._analysis is not None:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    @Slot()
    def tick(self, now: datetime | None = None) -> None:
        current = now or datetime.now()
        source_due = self.next_source is not None and self.next_source <= current
        analysis_due = self.next_analysis is not None and self.next_analysis <= current
        if source_due and self._source is not None and self._source_anchor_day is not None:
            self.next_source = next_due(
                self.next_source, current, self._source, self._source_anchor_day,
            )
        if analysis_due and self._analysis is not None and self._analysis_anchor_day is not None:
            self.next_analysis = next_due(
                self.next_analysis, current, self._analysis, self._analysis_anchor_day,
            )
        if source_due or analysis_due:
            self.changed.emit()
        if source_due:
            self.source_due.emit()
        if analysis_due:
            self.analysis_due.emit()

    def stop(self) -> None:
        self._timer.stop()
