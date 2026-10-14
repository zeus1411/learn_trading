"""Stage 2 public API: boundary-checked development trades, no market-file IO."""

from .stage2_data import DevelopmentTrades
from .stage2_metrics import evaluate_development, summarize


def evaluate_cost_scenario_views(scenario_trades, boundary):
    """Return mandatory views only after checking every trade's boundary."""
    bundle = DevelopmentTrades(boundary, tuple(
        (name, tuple(trades)) for name, trades in scenario_trades.items()))
    return {name: {"include_truncated": summarize(trades, bootstrap=False),
                   "exclude_truncated": summarize([t for t in trades if not t.truncated_by_gap], bootstrap=False)}
            for name, trades in bundle.scenarios}

