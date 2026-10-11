import unittest
from datetime import datetime, timedelta, timezone

from src.labeling import label_signals
from src.models import Bar, Signal
from src.pipeline import _apply_unexpected_gap_lookback


def config():
    return {
        "labeling": {
            "reward_risk": 2.0,
            "max_holding_m15_bars": 32,
            "min_risk_atr": 0.8,
            "max_risk_atr": 3.0,
            "same_bar_sl_tp": "LOSS",
        },
        "costs": {"spread_usd": 0.30, "slippage_usd_per_trade": 0.10},
    }


def bar(ts, open_, high, low, close, source="HISTDATA_M1_BID"):
    return Bar(ts, open_, high, low, close, source)


class LabelingTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2024, 1, 8, tzinfo=timezone.utc)

    def test_long_enters_at_ask_plus_slippage(self):
        bars = [
            bar(self.start, 100, 101, 99.5, 100.5),
            bar(self.start + timedelta(minutes=15), 100, 103.5, 100, 103),
        ]
        signal = Signal(self.start, "A", "long", 1, 99.0, 1.0,
                        signal_bar_source="HISTDATA_M1_BID")
        trades, skipped, _ = label_signals(bars, [signal], config())
        self.assertFalse(skipped)
        self.assertAlmostEqual(100.40, trades[0].entry_price)
        self.assertEqual("WIN", trades[0].outcome)
        self.assertAlmostEqual(2.0, trades[0].result_r)

    def test_short_stop_is_checked_on_ask(self):
        bars = [
            bar(self.start, 100, 100.5, 99.5, 100),
            bar(self.start + timedelta(minutes=15), 100, 101.3, 98.5, 99),
        ]
        signal = Signal(self.start, "B", "short", 1, 101.5, 1.0,
                        signal_bar_source="HISTDATA_M1_BID")
        trades, skipped, _ = label_signals(bars, [signal], config())
        self.assertFalse(skipped)
        self.assertAlmostEqual(99.90, trades[0].entry_price)
        self.assertEqual("LOSS", trades[0].outcome)
        self.assertEqual("STOP_LOSS", trades[0].termination_reason)

    def test_same_bar_sl_and_tp_is_loss(self):
        bars = [
            bar(self.start, 100, 101, 99.5, 100.5),
            bar(self.start + timedelta(minutes=15), 100, 104, 98, 101),
        ]
        signal = Signal(self.start, "A", "long", 1, 99.0, 1.0,
                        signal_bar_source="HISTDATA_M1_BID")
        trades, _, _ = label_signals(bars, [signal], config())
        self.assertEqual("LOSS", trades[0].outcome)
        self.assertEqual("SL_AND_TP_SAME_BAR_LOSS", trades[0].termination_reason)

    def test_trade_closes_before_gap_as_timeout(self):
        bars = [
            bar(self.start, 100, 100.5, 99.5, 100),
            bar(self.start + timedelta(minutes=15), 100, 101, 99.5, 100.5),
            bar(self.start + timedelta(minutes=30), 100.5, 101, 100, 100.8),
            bar(self.start + timedelta(minutes=60), 101, 101.5, 100.5, 101),
        ]
        signal = Signal(self.start, "A", "long", 1, 98.0, 1.0,
                        signal_bar_source="HISTDATA_M1_BID")
        trades, _, audit = label_signals(bars, [signal], config())
        self.assertEqual("TIMEOUT", trades[0].outcome)
        self.assertEqual(2, trades[0].exit_index)
        self.assertEqual("GAP_OR_WEEKEND_BEFORE_NEXT_BAR", trades[0].termination_reason)
        self.assertEqual(1, audit["termination_reason_counts"]["GAP_OR_WEEKEND_BEFORE_NEXT_BAR"])

    def test_unexpected_gap_blocks_exactly_previous_100_bars(self):
        bars = []
        timestamp = self.start
        for index in range(107):
            if index == 5:
                timestamp += timedelta(minutes=15)
            bars.append(bar(timestamp, 100, 101, 99, 100))
            timestamp += timedelta(minutes=15)
        blocked = Signal(bars[104].timestamp, "A", "long", 105, 98, 1)
        allowed = Signal(bars[105].timestamp, "A", "long", 106, 98, 1)
        kept, audit = _apply_unexpected_gap_lookback(bars, [blocked, allowed], 100)
        self.assertEqual([allowed], kept)
        self.assertEqual(1, audit["suppressed_total"])


if __name__ == "__main__":
    unittest.main()
