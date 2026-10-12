import unittest
from datetime import datetime, timedelta, timezone

from src.models import Bar
from src.signals_b import _has_rejected_gap, generate_signals_b


def config(version):
    return {
        "version": version,
        "atr_length": 14,
        "pivot_left": 5,
        "pivot_right": 5,
        "pivot_max_age_bars": 60,
        "sweep_buffer_atr": 0.1,
        "confirmation_window_bars": 3,
        "sl_buffer_atr": 0.25,
    }


def first_pierce_bars(shallow_first):
    start = datetime(2024, 1, 8, tzinfo=timezone.utc)
    bars = [
        Bar(start + timedelta(minutes=15 * index), 100, 101, 99, 100, "TEST")
        for index in range(30)
    ]
    bars[10] = Bar(bars[10].timestamp, 99, 100.5, 95, 99, "TEST")
    first_low = 94.99 if shallow_first else 94.0
    bars[16] = Bar(bars[16].timestamp, 99, 100, first_low, 96, "TEST")
    if shallow_first:
        bars[17] = Bar(bars[17].timestamp, 99, 100, 94, 96, "TEST")
        bars[18] = Bar(bars[18].timestamp, 100, 101, 99, 100.5, "TEST")
    else:
        bars[17] = Bar(bars[17].timestamp, 100, 101, 99, 100.5, "TEST")
    return bars


class SetupBV2Tests(unittest.TestCase):
    def test_failed_first_pierce_invalidates_pivot(self):
        bars = first_pierce_bars(shallow_first=True)
        v1 = generate_signals_b(bars, config("v1"))
        v2 = generate_signals_b(bars, config("v2"))
        self.assertTrue(any(signal.reference_index == 10 for signal in v1))
        self.assertFalse(any(signal.reference_index == 10 for signal in v2))

    def test_valid_first_pierce_can_signal(self):
        bars = first_pierce_bars(shallow_first=False)
        signals = generate_signals_b(bars, config("v2"))
        matching = [signal for signal in signals if signal.reference_index == 10]
        self.assertEqual(1, len(matching))
        self.assertEqual(16, matching[0].sweep_index)
        self.assertEqual("v2", matching[0].strategy_version)

    def test_weekend_rejected_but_daily_break_allowed(self):
        friday = datetime(2024, 1, 5, 20, 45, tzinfo=timezone.utc)
        sunday = datetime(2024, 1, 7, 22, 0, tzinfo=timezone.utc)
        weekend = [
            Bar(friday, 100, 101, 99, 100),
            Bar(sunday, 100, 101, 99, 100),
        ]
        self.assertTrue(_has_rejected_gap(weekend, 0, 1))

        daily = [
            Bar(datetime(2024, 1, 8, 20, 45, tzinfo=timezone.utc), 100, 101, 99, 100),
            Bar(datetime(2024, 1, 8, 22, 0, tzinfo=timezone.utc), 100, 101, 99, 100),
        ]
        self.assertFalse(_has_rejected_gap(daily, 0, 1))


if __name__ == "__main__":
    unittest.main()
