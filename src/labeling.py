"""Causal Stage 1 triple-barrier labeling on BID M15 bars."""

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Sequence, Tuple

from .gaps import DAILY_BREAK, HOLIDAY_BREAK, UNEXPECTED, WEEKEND, classify_gap
from .models import Bar, Signal


@dataclass(frozen=True)
class Trade:
    signal_timestamp: datetime
    entry_timestamp: datetime
    exit_timestamp: datetime
    setup: str
    direction: str
    signal_index: int
    entry_index: int
    exit_index: int
    entry_bid_open: float
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_usd: float
    result_r: float
    outcome: str
    bars_held: int
    mae_r: float
    mfe_r: float
    termination_reason: str
    spread_usd: float
    slippage_usd: float
    cost_scenario: str
    cost_multiplier: float
    truncated_by_gap: bool
    gap_type_after_exit: str
    signal_source: str
    entry_source: str
    exit_source: str
    reference_timestamp: str
    sweep_timestamp: str
    strategy_version: str = "v1"

    def to_dict(self) -> Dict[str, object]:
        result = asdict(self)
        for key in ("signal_timestamp", "entry_timestamp", "exit_timestamp"):
            result[key] = result[key].isoformat()
        return result


@dataclass(frozen=True)
class SkippedSignal:
    signal_timestamp: datetime
    setup: str
    direction: str
    reason: str
    signal_source: str

    def to_dict(self) -> Dict[str, object]:
        result = asdict(self)
        result["signal_timestamp"] = self.signal_timestamp.isoformat()
        return result


def _source_timestamp(bars: Sequence[Bar], index: int) -> str:
    return bars[index].timestamp.isoformat() if 0 <= index < len(bars) else ""


def label_signals(
    bars: Sequence[Bar],
    signals: Sequence[Signal],
    config: Dict[str, object],
    cost_multiplier: float = 1.0,
    cost_scenario: str = "normal",
) -> Tuple[List[Trade], List[SkippedSignal], Dict[str, object]]:
    labeling = config["labeling"]  # type: ignore
    costs = config["costs"]  # type: ignore
    reward_risk = float(labeling["reward_risk"])
    max_holding = int(labeling["max_holding_m15_bars"])
    min_risk_atr = float(labeling["min_risk_atr"])
    max_risk_atr = float(labeling["max_risk_atr"])
    if cost_multiplier <= 0:
        raise ValueError("cost_multiplier must be positive")
    base_spread = float(costs["spread_usd"])
    base_slippage = float(costs["slippage_usd_per_trade"])
    spread = base_spread * cost_multiplier
    slippage = base_slippage * cost_multiplier
    trades: List[Trade] = []
    skipped: List[SkippedSignal] = []
    busy_until: Dict[Tuple[str, str], int] = {}

    for signal in sorted(signals, key=lambda item: (item.entry_index, item.setup, item.direction)):
        signal_index = signal.entry_index - 1
        entry_index = signal.entry_index
        key = (signal.setup, signal.direction)
        if entry_index <= busy_until.get(key, -1):
            skipped.append(
                SkippedSignal(signal.timestamp, signal.setup, signal.direction,
                              "POSITION_ALREADY_OPEN", signal.signal_bar_source)
            )
            continue
        if signal_index < 0 or entry_index >= len(bars):
            skipped.append(
                SkippedSignal(signal.timestamp, signal.setup, signal.direction,
                              "NO_ENTRY_BAR_WITHIN_DEVELOPMENT_PARTITION",
                              signal.signal_bar_source)
            )
            continue
        if bars[entry_index].timestamp - bars[signal_index].timestamp != timedelta(minutes=15):
            skipped.append(
                SkippedSignal(signal.timestamp, signal.setup, signal.direction,
                              "SKIPPED_ENTRY_GAP", signal.signal_bar_source)
            )
            continue

        entry_bid = bars[entry_index].open
        if signal.direction == "long":
            entry_price = entry_bid + spread + slippage
            risk = entry_price - signal.sl
        else:
            entry_price = entry_bid - slippage
            risk = signal.sl - entry_price
        if risk <= 0 or risk < min_risk_atr * signal.atr:
            skipped.append(
                SkippedSignal(signal.timestamp, signal.setup, signal.direction,
                              "RISK_BELOW_MIN_ATR", signal.signal_bar_source)
            )
            continue
        if risk > max_risk_atr * signal.atr:
            skipped.append(
                SkippedSignal(signal.timestamp, signal.setup, signal.direction,
                              "RISK_ABOVE_MAX_ATR", signal.signal_bar_source)
            )
            continue

        take_profit = (
            entry_price + reward_risk * risk
            if signal.direction == "long"
            else entry_price - reward_risk * risk
        )
        exit_index = -1
        exit_price = 0.0
        outcome = ""
        termination = ""
        favorable = 0.0
        adverse = 0.0
        complete_window = False
        gap_type_after_exit = ""

        for offset in range(max_holding):
            index = entry_index + offset
            if index >= len(bars):
                break
            if (index > entry_index and
                    bars[index].timestamp - bars[index - 1].timestamp != timedelta(minutes=15)):
                exit_index = index - 1
                gap_type_after_exit = classify_gap(
                    bars[exit_index].timestamp, bars[index].timestamp
                )
                exit_price = (bars[exit_index].close if signal.direction == "long"
                              else bars[exit_index].close + spread)
                outcome = "TIMEOUT"
                termination = "GAP_OR_WEEKEND_BEFORE_NEXT_BAR"
                complete_window = True
                break

            bar = bars[index]
            if signal.direction == "long":
                favorable = max(favorable, bar.high - entry_price)
                adverse = min(adverse, bar.low - entry_price)
                stop_hit = bar.low <= signal.sl
                target_hit = bar.high >= take_profit
            else:
                ask_high = bar.high + spread
                ask_low = bar.low + spread
                favorable = max(favorable, entry_price - ask_low)
                adverse = min(adverse, entry_price - ask_high)
                stop_hit = ask_high >= signal.sl
                target_hit = ask_low <= take_profit

            if stop_hit:
                exit_index = index
                exit_price = signal.sl
                outcome = "LOSS"
                termination = "SL_AND_TP_SAME_BAR_LOSS" if target_hit else "STOP_LOSS"
                complete_window = True
                break
            if target_hit:
                exit_index = index
                exit_price = take_profit
                outcome = "WIN"
                termination = "TAKE_PROFIT"
                complete_window = True
                break
            if offset == max_holding - 1:
                exit_index = index
                exit_price = bar.close if signal.direction == "long" else bar.close + spread
                outcome = "TIMEOUT"
                termination = "MAX_HOLDING_BARS"
                complete_window = True
                break

        if not complete_window:
            skipped.append(
                SkippedSignal(signal.timestamp, signal.setup, signal.direction,
                              "INCOMPLETE_DEVELOPMENT_HORIZON",
                              signal.signal_bar_source)
            )
            continue

        pnl = exit_price - entry_price if signal.direction == "long" else entry_price - exit_price
        trade = Trade(
            signal_timestamp=signal.timestamp,
            entry_timestamp=bars[entry_index].timestamp,
            exit_timestamp=bars[exit_index].timestamp,
            setup=signal.setup,
            direction=signal.direction,
            signal_index=signal_index,
            entry_index=entry_index,
            exit_index=exit_index,
            entry_bid_open=entry_bid,
            entry_price=entry_price,
            stop_loss=signal.sl,
            take_profit=take_profit,
            risk_usd=risk,
            result_r=pnl / risk,
            outcome=outcome,
            bars_held=exit_index - entry_index + 1,
            mae_r=adverse / risk,
            mfe_r=favorable / risk,
            termination_reason=termination,
            spread_usd=spread,
            slippage_usd=slippage,
            cost_scenario=cost_scenario,
            cost_multiplier=cost_multiplier,
            truncated_by_gap=bool(gap_type_after_exit),
            gap_type_after_exit=gap_type_after_exit,
            signal_source=signal.signal_bar_source,
            entry_source=bars[entry_index].source,
            exit_source=bars[exit_index].source,
            reference_timestamp=(_source_timestamp(bars, signal.reference_index)
                                 if signal.reference_index is not None else ""),
            sweep_timestamp=(_source_timestamp(bars, signal.sweep_index)
                             if signal.sweep_index is not None else ""),
            strategy_version=signal.strategy_version,
        )
        trades.append(trade)
        busy_until[key] = exit_index

    outcomes = Counter((trade.setup, trade.direction, trade.outcome) for trade in trades)
    reasons = Counter(item.reason for item in skipped)
    terminations = Counter(trade.termination_reason for trade in trades)
    truncated = Counter(
        trade.gap_type_after_exit for trade in trades if trade.truncated_by_gap
    )
    truncated_counts = {
        WEEKEND: truncated.get(WEEKEND, 0),
        DAILY_BREAK: truncated.get(DAILY_BREAK, 0),
        HOLIDAY_BREAK: truncated.get(HOLIDAY_BREAK, 0),
        UNEXPECTED: truncated.get(UNEXPECTED, 0),
    }
    audit = {
        "cost_scenario": cost_scenario,
        "cost_multiplier": cost_multiplier,
        "input_signals": len(signals),
        "labeled_trades": len(trades),
        "skipped_signals": len(skipped),
        "outcome_counts_by_setup_direction": {
            "%s_%s_%s" % key: value for key, value in sorted(outcomes.items())
        },
        "skip_reason_counts": dict(sorted(reasons.items())),
        "termination_reason_counts": dict(sorted(terminations.items())),
        "truncated_trade_counts_by_gap_type": truncated_counts,
        "truncated_trade_count": sum(truncated_counts.values()),
        "bid_side_cost_model": {
            "base_spread_usd": base_spread,
            "base_slippage_usd_per_trade": base_slippage,
            "cost_multiplier": cost_multiplier,
            "spread_usd": spread,
            "slippage_usd_per_trade": slippage,
            "long_entry": "bid open + spread + slippage",
            "long_barriers": "bid OHLC",
            "short_entry": "bid open - slippage",
            "short_barriers_and_exit": "ask OHLC synthesized as bid + spread",
        },
    }
    return trades, skipped, audit
