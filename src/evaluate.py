"""Stage 2 evaluation primitives.

The Stage 2 command remains gated until explicit Stage 1 approval.  These
helpers make the pre-registered cost-scenario and truncated-trade views part of
the implementation contract before any validation result is inspected.
"""

from collections import Counter
from math import inf
from typing import Dict, Mapping, Sequence

from .labeling import Trade


def _summary(trades: Sequence[Trade]) -> Dict[str, object]:
    returns = [trade.result_r for trade in trades]
    positive = sum(value for value in returns if value > 0)
    negative = -sum(value for value in returns if value < 0)
    outcomes = Counter(trade.outcome for trade in trades)
    return {
        "n_trades": len(trades),
        "n_truncated": sum(1 for trade in trades if trade.truncated_by_gap),
        "outcome_counts": dict(sorted(outcomes.items())),
        "expectancy_r": (sum(returns) / len(returns)) if returns else None,
        "profit_factor": (
            positive / negative if negative > 0 else (inf if positive > 0 else None)
        ),
    }


def evaluate_cost_scenario_views(
    scenario_trades: Mapping[str, Sequence[Trade]],
) -> Dict[str, Dict[str, Dict[str, object]]]:
    """Return mandatory include/exclude-truncation views for every cost case."""

    output: Dict[str, Dict[str, Dict[str, object]]] = {}
    for scenario, trades in scenario_trades.items():
        included = list(trades)
        excluded = [trade for trade in trades if not trade.truncated_by_gap]
        output[scenario] = {
            "include_truncated": _summary(included),
            "exclude_truncated": _summary(excluded),
        }
    return output

