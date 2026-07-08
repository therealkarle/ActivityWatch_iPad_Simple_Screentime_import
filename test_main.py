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

    def test_format_activitywatch_app_name_without_suffix_keeps_original(self) -> None:
        self.assertEqual(main.format_activitywatch_app_name("Safari"), "Safari")

    def test_format_activitywatch_app_name_uses_global_suffix(self) -> None:
        self.assertEqual(
            main.format_activitywatch_app_name("Safari", global_suffix=" - FlorianIPad"),
            "Safari - FlorianIPad",
        )

    def test_format_activitywatch_app_name_prefers_per_app_override(self) -> None:
        self.assertEqual(
            main.format_activitywatch_app_name(
                "Safari",
                global_suffix=" - FlorianIPad",
                per_app_suffixes={"Safari": " - Private"},
            ),
            "Safari - Private",
        )
        self.assertEqual(
            main.format_activitywatch_app_name(
                "Notes",
                global_suffix=" - FlorianIPad",
                per_app_suffixes={"Safari": " - Private"},
            ),
            "Notes - FlorianIPad",
        )

    def test_apply_activitywatch_app_discount_factors_uses_global_factor(self) -> None:
        entries = [
            main.ParsedEntry("Safari", 10 * 60),
            main.ParsedEntry("Notes", 5 * 60),
        ]

        discounted = main.apply_activitywatch_app_discount_factors(entries, global_factor=0.5)

        self.assertEqual(
            discounted,
            [
                main.ParsedEntry("Safari", 5 * 60),
                main.ParsedEntry("Notes", 2 * 60 + 30),
            ],
        )

    def test_apply_activitywatch_app_discount_factors_prefers_per_app_override(self) -> None:
        entries = [
            main.ParsedEntry("Safari", 10 * 60),
            main.ParsedEntry("Notes", 5 * 60),
        ]

        discounted = main.apply_activitywatch_app_discount_factors(
            entries,
            global_factor=0.5,
            per_app_factors={"Safari": 0.25},
        )

        self.assertEqual(
            discounted,
            [
                main.ParsedEntry("Safari", 2 * 60 + 30),
                main.ParsedEntry("Notes", 2 * 60 + 30),
            ],
        )

    def test_apply_activitywatch_app_discount_factors_rounds_and_drops_zero_values(self) -> None:
        entries = [
            main.ParsedEntry("Safari", 1),
            main.ParsedEntry("Notes", 3),
            main.ParsedEntry("Music", 4),
        ]

        discounted = main.apply_activitywatch_app_discount_factors(
            entries,
            global_factor=0.25,
        )

        self.assertEqual(
            discounted,
            [
                main.ParsedEntry("Notes", 1),
            ],
        )

    def test_get_activitywatch_app_discount_factor_config_reads_global_and_overrides(self) -> None:
        config = {
            "activitywatch_app_discount_factor": "0.75",
            "activitywatch_app_discount_factor_overrides": {
                "Safari": 0.5,
            },
        }

        global_factor, overrides = main.get_activitywatch_app_discount_factor_config(config)

        self.assertEqual(global_factor, 0.75)
        self.assertEqual(overrides, {"Safari": 0.5})

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
            app_name_suffix=" - FlorianIPad",
            app_name_suffix_overrides={"AppB": " - Private"},
        )

        self.assertEqual([event.timestamp for event in app_events], sorted(event.timestamp for event in app_events))
        self.assertEqual(carryover, [])
        self.assertEqual([event.data["app"] for event in app_events], [event.data["title"] for event in app_events])
        self.assertIn("AppA - FlorianIPad", [event.data["app"] for event in app_events])
        self.assertIn("AppB - Private", [event.data["app"] for event in app_events])
        self.assertIn("AppC - FlorianIPad", [event.data["app"] for event in app_events])

        not_afk_events = [event for event in afk_events if event.data["status"] == "not-afk"]
        afk_only_events = [event for event in afk_events if event.data["status"] == "afk"]
        primary_end = datetime(2026, 7, 7, tzinfo=main.LOCAL_TIMEZONE) + timedelta(minutes=10)
        morning_app_events = [event for event in app_events if event.timestamp < primary_end]

        self.assertEqual(len(not_afk_events), len(app_events))
        self.assertEqual(len(afk_only_events), 1)
        self.assertEqual(
            [(event.timestamp, event.duration) for event in not_afk_events],
            [(event.timestamp, event.duration) for event in app_events],
        )
        self.assertEqual(
            afk_only_events[0].timestamp,
            morning_app_events[-1].timestamp + morning_app_events[-1].duration,
        )
        self.assertEqual(afk_only_events[0].duration, timedelta(minutes=10))

    def test_create_events_applies_discount_factors_before_planning(self) -> None:
        entries = [
            main.ParsedEntry("AppA", 10 * 60),
            main.ParsedEntry("AppB", 6 * 60),
        ]

        app_events, afk_events, carryover = main.create_events_for_day(
            "2026-07-07",
            entries,
            start_time=0,
            wake_up_time=20,
            backup_intervals=[],
            app_name_suffix=" - FlorianIPad",
            app_name_suffix_overrides={"AppB": " - Private"},
            app_name_discount_factor=0.5,
            app_name_discount_factor_overrides={"AppB": 0.25},
        )

        self.assertEqual(carryover, [])
        self.assertEqual(len(app_events), 2)
        self.assertEqual(
            [event.data["app"] for event in app_events],
            ["AppA - FlorianIPad", "AppB - Private"],
        )
        self.assertEqual([event.data["usage_seconds"] for event in app_events], [5 * 60, 90])
        self.assertEqual([event.duration for event in app_events], [timedelta(minutes=5), timedelta(seconds=90)])
        not_afk_events = [event for event in afk_events if event.data["status"] == "not-afk"]
        afk_only_events = [event for event in afk_events if event.data["status"] == "afk"]

        self.assertEqual(len(not_afk_events), len(app_events))
        self.assertEqual(len(afk_only_events), 1)
        self.assertEqual(
            [(event.timestamp, event.duration) for event in not_afk_events],
            [(event.timestamp, event.duration) for event in app_events],
        )
        self.assertEqual(
            afk_only_events[0].timestamp,
            app_events[-1].timestamp + app_events[-1].duration,
        )
        self.assertEqual(afk_only_events[0].duration, timedelta(minutes=10))

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
                ("AppA", day1 + timedelta(minutes=3), timedelta(minutes=3)),
                ("AppA", day1 + timedelta(minutes=20), timedelta(minutes=5)),
            ],
        )
        self.assertEqual(carryover, [])

    def test_overflow_mode_still_uses_backup_windows(self) -> None:
        day = datetime(2026, 7, 7, tzinfo=timezone.utc)
        windows = [
            main.TimeWindow("primary:00:00-00:05", day, day + timedelta(minutes=5)),
            main.TimeWindow("backup:00:20-00:25", day + timedelta(minutes=20), day + timedelta(minutes=25)),
        ]
        entries = [
            main.ParsedEntry("AppA", 8 * 60),
            main.ParsedEntry("AppB", 3 * 60),
            main.ParsedEntry("AppC", 5 * 60),
        ]

        segments, carryover = main.plan_entries_into_windows(entries, windows)

        self.assertEqual([segment.app_name for segment in segments], ["AppB", "AppA", "AppC"])
        self.assertEqual([segment.duration_seconds for segment in segments], [3 * 60, 8 * 60, 5 * 60])
        self.assertEqual([segment.start for segment in segments], [day, day + timedelta(minutes=3), day + timedelta(minutes=20)])
        self.assertEqual(carryover, [])

    def test_overflow_extends_morning_window_after_fallbacks_are_full(self) -> None:
        day = datetime(2026, 7, 7, tzinfo=timezone.utc)
        windows = [
            main.TimeWindow("primary:00:00-06:00", day, day + timedelta(hours=6)),
            main.TimeWindow("backup:22:00-24:00", day + timedelta(hours=22), day + timedelta(hours=24)),
        ]
        entries = [main.ParsedEntry("AppA", 9 * 60 * 60)]

        segments, carryover = main.plan_entries_into_windows(entries, windows)

        self.assertEqual([segment.app_name for segment in segments], ["AppA", "AppA"])
        self.assertEqual([segment.duration_seconds for segment in segments], [7 * 60 * 60, 2 * 60 * 60])
        self.assertEqual([segment.start for segment in segments], [day, day + timedelta(hours=22)])
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
