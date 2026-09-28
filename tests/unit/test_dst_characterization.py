from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone, tzinfo

from theme_scheduler.config import AppConfig
from theme_scheduler.core import SystemClock
from theme_scheduler.scheduled_auto import upcoming_fixed_boundary


class EasternTestZone(tzinfo):
    @staticmethod
    def _is_daylight(value: datetime) -> bool:
        march_first = date(value.year, 3, 1)
        first_sunday = march_first + timedelta(days=(6 - march_first.weekday()) % 7)
        second_sunday = first_sunday + timedelta(days=7)
        november_first = date(value.year, 11, 1)
        first_november_sunday = november_first + timedelta(
            days=(6 - november_first.weekday()) % 7
        )
        return second_sunday <= value.date() < first_november_sunday

    def utcoffset(self, value: datetime | None) -> timedelta | None:
        if value is None:
            return timedelta(hours=-5)
        return timedelta(hours=-4 if self._is_daylight(value) else -5)

    def dst(self, value: datetime | None) -> timedelta | None:
        if value is None:
            return timedelta(0)
        return timedelta(hours=1 if self._is_daylight(value) else 0)

    def tzname(self, value: datetime | None) -> str:
        if value is not None and self._is_daylight(value):
            return "EDT"
        return "EST"


class DstCharacterizationTests(unittest.TestCase):
    def test_future_boundary_retains_fixed_offset_across_dst_transition(self) -> None:
        config = AppConfig.defaults()
        daylight_zone = EasternTestZone()
        previous_evening = datetime(2026, 3, 7, 23, 45, tzinfo=daylight_zone)
        daylight_boundary = upcoming_fixed_boundary(config, previous_evening)
        fixed_standard_time = datetime(
            2026,
            3,
            7,
            23,
            45,
            tzinfo=timezone(timedelta(hours=-5)),
        )
        fixed_offset_boundary = upcoming_fixed_boundary(config, fixed_standard_time)

        self.assertEqual(daylight_boundary.scheduled_at.date(), date(2026, 3, 8))
        self.assertEqual(
            daylight_boundary.scheduled_at.utcoffset(),
            timedelta(hours=-4),
        )
        self.assertEqual(
            fixed_offset_boundary.scheduled_at.utcoffset(),
            timedelta(hours=-5),
        )
        self.assertEqual(
            fixed_offset_boundary.scheduled_at.timestamp()
            - daylight_boundary.scheduled_at.timestamp(),
            3600,
        )

        # The configured wall-clock trigger runs after the transition and is
        # recalculated with the current offset, so this example does not show
        # that a normal 06:15 automatic application will be delayed.
        transition_morning = datetime(
            2026,
            3,
            8,
            6,
            10,
            tzinfo=timezone(timedelta(hours=-4)),
        )
        rechecked = upcoming_fixed_boundary(config, transition_morning)
        self.assertEqual(rechecked.scheduled_at.hour, 6)
        self.assertEqual(rechecked.scheduled_at.minute, 15)
        self.assertEqual(rechecked.scheduled_at.utcoffset(), timedelta(hours=-4))

    def test_system_clock_returns_a_fixed_current_offset_on_this_runtime(self) -> None:
        self.assertIsInstance(SystemClock().now().tzinfo, timezone)


if __name__ == "__main__":
    unittest.main()
