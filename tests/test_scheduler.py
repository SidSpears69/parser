"""Local calendar arithmetic for recurring tab schedules."""

from datetime import datetime, time
import unittest

from web_parser.scheduler import Schedule, first_due, next_due


class ScheduleMathTests(unittest.TestCase):
    def test_hourly_schedule_uses_next_slot_after_start_time(self):
        schedule = Schedule(time(8, 30), 2, "hours")
        self.assertEqual(
            first_due(datetime(2026, 11, 6, 9, 15), schedule),
            datetime(2026, 11, 6, 10, 30),
        )
        due = datetime(2026, 11, 6, 10, 30)
        self.assertEqual(
            next_due(due, datetime(2026, 11, 6, 16, 30), schedule, due.day),
            datetime(2026, 11, 6, 18, 30),
        )

    def test_daily_and_weekly_intervals_skip_missed_runs(self):
        daily = Schedule(time(7, 15), 3, "days")
        first = first_due(datetime(2026, 11, 6, 8), daily)
        self.assertEqual(first, datetime(2026, 11, 7, 7, 15))
        self.assertEqual(
            next_due(first, datetime(2026, 11, 14, 9), daily, first.day),
            datetime(2026, 11, 16, 7, 15),
        )
        weekly = Schedule(time(8), 2, "weeks")
        self.assertEqual(
            next_due(datetime(2026, 11, 6, 8), datetime(2026, 11, 20, 8), weekly, 6),
            datetime(2026, 12, 4, 8),
        )

    def test_monthly_schedule_returns_to_original_day_after_short_month(self):
        monthly = Schedule(time(22), 1, "months")
        january = datetime(2027, 1, 31, 22)
        february = next_due(january, january, monthly, 31)
        march = next_due(february, february, monthly, 31)
        self.assertEqual(february, datetime(2027, 2, 28, 22))
        self.assertEqual(march, datetime(2027, 3, 31, 22))


if __name__ == "__main__":
    unittest.main()
