from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import main


class PlannerTests(unittest.TestCase):
    def test_parse_clock_minutes(self) -> None:
        self.assertEqual(main.parse_clock_minutes(0), 0)
        self.assertEqual(main.parse_clock_minutes(600), 6 * 60)
        self.assertEqual(main.parse_clock_minutes("06:00"), 6 * 60)
        self.assertEqual(main.parse_clock_minutes("2400", allow_2400=True), 24 * 60)

    def test_parse_backup_intervals_preserves_order(self) -> None:
        intervals = main.parse_backup_intervals("[2200;2400]; [1200;1300]")
        self.assertEqual(intervals, [(22 * 60, 24 * 60), (12 * 60, 13 * 60)])

    def test_plan_entries_prefers_later_full_window_over_split(self) -> None:
        day = datetime(2026, 7, 7, tzinfo=timezone.utc)
        windows = [
            main.TimeWindow("primary:00:00-00:10", day, day + timedelta(minutes=10)),
            main.TimeWindow("backup:00:20-00:35", day + timedelta(minutes=20), day + timedelta(minutes=35)),
        ]
        entries = [
            main.ParsedEntry("AppA", 5 * 60),
            main.ParsedEntry("AppB", 15 * 60),
        ]

        segments, carryover = main.plan_entries_into_windows(entries, windows)

        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].app_name, "AppA")
        self.assertEqual(segments[0].duration_seconds, 5 * 60)
        self.assertEqual(segments[0].start, day)
        self.assertEqual(segments[1].app_name, "AppB")
        self.assertEqual(segments[1].duration_seconds, 15 * 60)
        self.assertEqual(segments[1].start, day + timedelta(minutes=20))
        self.assertEqual(carryover, [])

    def test_plan_entries_splits_only_when_needed(self) -> None:
        day = datetime(2026, 7, 7, tzinfo=timezone.utc)
        windows = [
            main.TimeWindow("w1", day, day + timedelta(minutes=10)),
            main.TimeWindow("w2", day + timedelta(minutes=20), day + timedelta(minutes=25)),
        ]
        entries = [main.ParsedEntry("AppA", 12 * 60)]

        segments, carryover = main.plan_entries_into_windows(entries, windows)

        self.assertEqual(len(segments), 2)
        self.assertEqual([segment.duration_seconds for segment in segments], [10 * 60, 2 * 60])
        self.assertEqual([segment.start for segment in segments], [day, day + timedelta(minutes=20)])
        self.assertEqual(carryover, [])

    def test_plan_entries_fills_gap_with_later_block_without_forcing_split(self) -> None:
        day = datetime(2026, 7, 7, tzinfo=timezone.utc)
        windows = [
            main.TimeWindow("primary:00:00-00:10", day, day + timedelta(minutes=10)),
            main.TimeWindow("backup:00:30-01:00", day + timedelta(minutes=30), day + timedelta(minutes=60)),
        ]
        entries = [
            main.ParsedEntry("AppA", 15 * 60),
            main.ParsedEntry("AppB", 5 * 60),
            main.ParsedEntry("AppC", 10 * 60),
        ]

        segments, carryover = main.plan_entries_into_windows(entries, windows)

        self.assertEqual([segment.app_name for segment in segments], ["AppB", "AppA", "AppC"])
        self.assertEqual([segment.duration_seconds for segment in segments], [5 * 60, 15 * 60, 10 * 60])
        self.assertEqual(
            [segment.start for segment in segments],
            [day, day + timedelta(minutes=30), day + timedelta(minutes=45)],
        )
        self.assertEqual(carryover, [])

    def test_create_events_are_sorted_and_non_overlapping(self) -> None:
        entries = [
            main.ParsedEntry("AppA", 5 * 60),
            main.ParsedEntry("AppB", 10 * 60),
            main.ParsedEntry("AppC", 5 * 60),
        ]

        app_events, afk_events, carryover = main.create_events_for_day(
            "2026-07-07",
            entries,
            start_time=0,
            wake_up_time=10,
            backup_intervals=[(30, 40)],
        )

        self.assertEqual(len(app_events), len(afk_events))
        self.assertEqual([event.timestamp for event in app_events], sorted(event.timestamp for event in app_events))
        self.assertEqual(carryover, [])

        previous_end = None
        for event in app_events:
            if previous_end is not None:
                self.assertGreaterEqual(event.timestamp, previous_end)
            previous_end = event.timestamp + event.duration

    def test_continues_after_last_event_in_same_day(self) -> None:
        day1 = datetime(2026, 7, 7, tzinfo=main.LOCAL_TIMEZONE)
        entries = [
            main.ParsedEntry("AppA", 8 * 60),
            main.ParsedEntry("AppB", 3 * 60),
        ]

        app_events_day1, _, carryover = main.create_events_for_day(
            "2026-07-07",
            entries,
            start_time=0,
            wake_up_time=5,
            backup_intervals=[(20, 25)],
        )

        self.assertEqual(
            [(event.data["app"], event.timestamp.astimezone(main.LOCAL_TIMEZONE), event.duration) for event in app_events_day1],
            [
                ("AppB", day1, timedelta(minutes=3)),
                ("AppA", day1 + timedelta(minutes=3), timedelta(minutes=2)),
                ("AppA", day1 + timedelta(minutes=20), timedelta(minutes=6)),
            ],
        )
        self.assertEqual(carryover, [])

    def test_carryover_only_contains_time_beyond_midnight(self) -> None:
        entries = [main.ParsedEntry("AppA", 25 * 60 * 60)]

        with self.assertRaisesRegex(ValueError, "cannot fit all app time without crossing midnight"):
            main.create_events_for_day(
                "2026-07-07",
                entries,
                start_time=0,
                wake_up_time=5,
                backup_intervals=[(20, 25)],
            )


if __name__ == "__main__":
    unittest.main()
