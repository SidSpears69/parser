"""Calendar calculations for the per-site source and analysis schedules."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, time, timedelta


@dataclass(frozen=True, slots=True)
class Schedule:
    start_time: time
    interval: int
    unit: str

    def __post_init__(self) -> None:
        if self.interval < 1 or self.unit not in {"hours", "days", "weeks", "months"}:
            raise ValueError("Недопустимый интервал расписания.")


def first_due(now: datetime, schedule: Schedule) -> datetime:
    """Choose the next local wall-clock occurrence after enabling a schedule."""
    candidate = datetime.combine(now.date(), schedule.start_time)
    if schedule.unit == "hours":
        if candidate <= now:
            step = timedelta(hours=schedule.interval)
            candidate += step * ((now - candidate) // step + 1)
    elif candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def next_due(previous: datetime, now: datetime, schedule: Schedule, anchor_day: int) -> datetime:
    """Move past missed occurrences without replaying an accumulated backlog."""
    if schedule.unit == "months":
        candidate = previous
        while candidate <= now:
            month_index = candidate.year * 12 + candidate.month - 1 + schedule.interval
            year, zero_month = divmod(month_index, 12)
            month = zero_month + 1
            day = min(anchor_day, monthrange(year, month)[1])
            candidate = candidate.replace(year=year, month=month, day=day)
        return candidate
    step = timedelta(
        hours=schedule.interval if schedule.unit == "hours" else 0,
        days=schedule.interval if schedule.unit == "days" else 7 * schedule.interval
        if schedule.unit == "weeks" else 0,
    )
    return previous + step * max(1, (now - previous) // step + 1)
