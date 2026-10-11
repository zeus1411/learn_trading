import csv
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.data_quality import validate_m15_csv


class DataQualityTests(unittest.TestCase):
    def _write(self, rows):
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / "bars.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["timestamp", "open", "high", "low", "close"])
            writer.writeheader()
            writer.writerows(rows)
        return temp, path

    def test_naive_timestamp_is_rejected_without_opt_in(self):
        rows = [{"timestamp": "2024-01-01 00:00:00", "open": 1, "high": 2, "low": 1, "close": 2}]
        temp, path = self._write(rows)
        try:
            _, report = validate_m15_csv(path, minimum_years=0)
            self.assertGreater(report.bad_tick_rows, 0)
            self.assertTrue(report.fatal_errors)
        finally:
            temp.cleanup()

    def test_exact_duplicate_is_logged_and_dropped(self):
        timestamp = "2024-01-01T00:00:00+00:00"
        row = {"timestamp": timestamp, "open": 1, "high": 2, "low": 1, "close": 2}
        temp, path = self._write([row, row])
        try:
            bars, report = validate_m15_csv(path, minimum_years=0)
            self.assertEqual(1, len(bars))
            self.assertEqual(1, report.exact_duplicate_rows)
            self.assertEqual("DROP_EXACT_DUPLICATE_ROW", report.corrections[0].action)
        finally:
            temp.cleanup()

    def test_gap_is_reported_not_filled(self):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        rows = []
        for offset in (0, 15, 45):
            rows.append(
                {
                    "timestamp": (start + timedelta(minutes=offset)).isoformat(),
                    "open": 1,
                    "high": 2,
                    "low": 1,
                    "close": 2,
                }
            )
        temp, path = self._write(rows)
        try:
            bars, report = validate_m15_csv(path, minimum_years=0)
            self.assertEqual(3, len(bars))
            self.assertEqual(1, report.total_gap_events)
            self.assertEqual(1, report.estimated_missing_bars)
        finally:
            temp.cleanup()

    def test_blank_m1_count_is_tracked_as_partial_metadata(self):
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / "bars.csv"
        rows = [
            {
                "timestamp": "2024-01-01T00:00:00+00:00",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 2,
                "m1_count": "15",
            },
            {
                "timestamp": "2024-01-01T00:15:00+00:00",
                "open": 2,
                "high": 3,
                "low": 2,
                "close": 3,
                "m1_count": "",
            },
        ]
        try:
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            bars, report = validate_m15_csv(path, minimum_years=0)
            self.assertEqual(2, len(bars))
            self.assertEqual("PARTIAL", report.m1_count_status)
            self.assertEqual(1, report.missing_m1_count_rows)
            self.assertFalse(report.fatal_errors)
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
