import unittest
from dataclasses import replace
from datetime import datetime, timezone

from src.evaluate import evaluate_cost_scenario_views
from src.labeling import Trade


def trade(result_r, truncated=False, scenario="normal"):
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return Trade(
        signal_timestamp=now,
        entry_timestamp=now,
        exit_timestamp=now,
        setup="A",
        direction="long",
        signal_index=0,
        entry_index=1,
        exit_index=1,
        entry_bid_open=100.0,
        entry_price=100.4,
        stop_loss=99.0,
        take_profit=103.2,
        risk_usd=1.4,
        result_r=result_r,
        outcome="WIN" if result_r > 0 else "LOSS",
        bars_held=1,
        mae_r=-0.1,
        mfe_r=0.2,
        termination_reason=("GAP_OR_WEEKEND_BEFORE_NEXT_BAR" if truncated else "TAKE_PROFIT"),
        spread_usd=0.3,
        slippage_usd=0.1,
        cost_scenario=scenario,
        cost_multiplier=1.0,
        truncated_by_gap=truncated,
        gap_type_after_exit=("SCHEDULED_DAILY_METALS_BREAK" if truncated else ""),
        signal_source="TEST",
        entry_source="TEST",
        exit_source="TEST",
        reference_timestamp="",
        sweep_timestamp="",
    )


class EvaluateContractTests(unittest.TestCase):
    def test_every_cost_scenario_has_both_truncation_views(self):
        normal = [trade(1.0), trade(-1.0, truncated=True)]
        plus_50 = [replace(item, cost_scenario="plus_50_percent", cost_multiplier=1.5)
                   for item in normal]
        plus_100 = [replace(item, cost_scenario="plus_100_percent", cost_multiplier=2.0)
                    for item in normal]
        report = evaluate_cost_scenario_views({
            "normal": normal,
            "plus_50_percent": plus_50,
            "plus_100_percent": plus_100,
        })
        self.assertEqual(
            {"normal", "plus_50_percent", "plus_100_percent"}, set(report)
        )
        for details in report.values():
            self.assertEqual(2, details["include_truncated"]["n_trades"])
            self.assertEqual(1, details["exclude_truncated"]["n_trades"])
            self.assertEqual(0, details["exclude_truncated"]["n_truncated"])


if __name__ == "__main__":
    unittest.main()
