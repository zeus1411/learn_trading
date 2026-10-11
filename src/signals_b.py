from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .features import atr
from .models import Bar, Signal


@dataclass
class _Pivot:
    index: int
    price: float
    swept: bool = False


@dataclass
class _Sweep:
    index: int
    extreme: float
    trigger: float
    pivot_index: int
    atr: float


def _is_pivot_low(bars: Sequence[Bar], index: int, left: int, right: int) -> bool:
    center = bars[index].low
    return all(
        center < bars[other].low
        for other in range(index - left, index + right + 1)
        if other != index
    )


def _is_pivot_high(bars: Sequence[Bar], index: int, left: int, right: int) -> bool:
    center = bars[index].high
    return all(
        center > bars[other].high
        for other in range(index - left, index + right + 1)
        if other != index
    )


def generate_signals_b(
    bars: Sequence[Bar], config: Dict[str, float]
) -> List[Signal]:
    left = int(config["pivot_left"])
    right = int(config["pivot_right"])
    max_age = int(config["pivot_max_age_bars"])
    confirmation = int(config["confirmation_window_bars"])
    atr_values = atr(bars, int(config["atr_length"]))
    pivot_lows: List[_Pivot] = []
    pivot_highs: List[_Pivot] = []
    pending_long: Optional[_Sweep] = None
    pending_short: Optional[_Sweep] = None
    output: List[Signal] = []

    for index, bar in enumerate(bars):
        confirm_index = index - right
        if confirm_index >= left:
            if _is_pivot_low(bars, confirm_index, left, right):
                pivot_lows.append(_Pivot(confirm_index, bars[confirm_index].low))
            if _is_pivot_high(bars, confirm_index, left, right):
                pivot_highs.append(_Pivot(confirm_index, bars[confirm_index].high))

        if pending_long is not None:
            age = index - pending_long.index
            if 1 <= age <= confirmation and bar.close > pending_long.trigger:
                output.append(
                    Signal(
                        bar.timestamp,
                        "B",
                        "long",
                        index + 1,
                        pending_long.extreme
                        - config["sl_buffer_atr"] * pending_long.atr,
                        pending_long.atr,
                        pending_long.pivot_index,
                    )
                )
                pending_long = None
            elif age > confirmation:
                pending_long = None
        if pending_short is not None:
            age = index - pending_short.index
            if 1 <= age <= confirmation and bar.close < pending_short.trigger:
                output.append(
                    Signal(
                        bar.timestamp,
                        "B",
                        "short",
                        index + 1,
                        pending_short.extreme
                        + config["sl_buffer_atr"] * pending_short.atr,
                        pending_short.atr,
                        pending_short.pivot_index,
                    )
                )
                pending_short = None
            elif age > confirmation:
                pending_short = None

        current_atr = atr_values[index]
        if current_atr is None or current_atr <= 0:
            continue
        # Expired and swept pivots can never become eligible again. Pruning
        # here preserves the rules while avoiding an O(n^2) scan over the full
        # history on every bar.
        pivot_lows = [
            pivot
            for pivot in pivot_lows
            if not pivot.swept and 0 <= index - pivot.index <= max_age
        ]
        pivot_highs = [
            pivot
            for pivot in pivot_highs
            if not pivot.swept and 0 <= index - pivot.index <= max_age
        ]
        if pending_long is None:
            for pivot in reversed(pivot_lows):
                if (
                    bar.low < pivot.price - config["sweep_buffer_atr"] * current_atr
                    and bar.close > pivot.price
                ):
                    pivot.swept = True
                    pending_long = _Sweep(
                        index, bar.low, bar.high, pivot.index, current_atr
                    )
                    break
        if pending_short is None:
            for pivot in reversed(pivot_highs):
                if (
                    bar.high > pivot.price + config["sweep_buffer_atr"] * current_atr
                    and bar.close < pivot.price
                ):
                    pivot.swept = True
                    pending_short = _Sweep(
                        index, bar.high, bar.low, pivot.index, current_atr
                    )
                    break
    return output

