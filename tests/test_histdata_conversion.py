import unittest
from datetime import timezone

from src.convert_m1_to_m15 import _merge_occurrences, _parse_timestamp
from src.gaps import HOLIDAY_BREAK, classify_gap, is_scheduled_metals_break


class HistDataConversionTests(unittest.TestCase):
    def test_empirical_histdata_clock_uses_european_dst_dates(self):
        winter = _parse_timestamp("2020.01.15", "12:00").astimezone(timezone.utc)
        us_only_dst = _parse_timestamp("2020.03.15", "12:00").astimezone(timezone.utc)
        summer = _parse_timestamp("2020.07.15", "12:00").astimezone(timezone.utc)
        eu_ended = _parse_timestamp("2020.10.26", "12:00").astimezone(timezone.utc)
        self.assertEqual("2020-01-15T17:00:00+00:00", winter.isoformat())
        self.assertEqual("2020-03-15T17:00:00+00:00", us_only_dst.isoformat())
        self.assertEqual("2020-07-15T16:00:00+00:00", summer.isoformat())
        self.assertEqual("2020-10-26T17:00:00+00:00", eu_ended.isoformat())

    def test_conflicting_minute_fragments_merge_conservatively(self):
        merged = _merge_occurrences(
            [(100.0, 103.0, 99.0, 101.0), (101.0, 104.0, 98.0, 102.0)]
        )
        self.assertEqual((100.0, 104.0, 98.0, 102.0), merged)

    def test_known_utc_metals_breaks_are_scheduled(self):
        winter_before = _parse_timestamp("2020.01.15", "16:45").astimezone(timezone.utc)
        winter_after = _parse_timestamp("2020.01.15", "18:00").astimezone(timezone.utc)
        summer_before = _parse_timestamp("2020.07.15", "16:45").astimezone(timezone.utc)
        summer_after = _parse_timestamp("2020.07.15", "18:00").astimezone(timezone.utc)
        self.assertTrue(is_scheduled_metals_break(winter_before, winter_after))
        self.assertTrue(is_scheduled_metals_break(summer_before, summer_after))

    def test_us_holiday_early_close_is_not_unexpected(self):
        start = _parse_timestamp("2023.07.04", "14:15").astimezone(timezone.utc)
        end = _parse_timestamp("2023.07.04", "18:00").astimezone(timezone.utc)
        self.assertEqual(HOLIDAY_BREAK, classify_gap(start, end))


if __name__ == "__main__":
    unittest.main()
