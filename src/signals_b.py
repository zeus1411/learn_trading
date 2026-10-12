from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .features import atr
from .gaps import HOLIDAY_BREAK, UNEXPECTED, WEEKEND, classify_gap
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


def _generate_signals_b_v1(
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
                        pending_long.index,
                        bar.source,
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
                        pending_short.index,
                        bar.source,
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


_V2_REJECTED_GAPS = {WEEKEND, HOLIDAY_BREAK, UNEXPECTED}


def _has_rejected_gap(bars: Sequence[Bar], start: int, end: int) -> bool:
    """Return whether any rejected discontinuity occurs from start through end."""

    return any(
        classify_gap(bars[index - 1].timestamp, bars[index].timestamp)
        in _V2_REJECTED_GAPS
        for index in range(start + 1, end + 1)
    )


def _rejected_gap_prefix(bars: Sequence[Bar]) -> List[int]:
    """Cumulative rejected-gap arrivals for constant-time interval checks."""

    prefix = [0] * len(bars)
    for index in range(1, len(bars)):
        prefix[index] = prefix[index - 1] + int(
            classify_gap(bars[index - 1].timestamp, bars[index].timestamp)
            in _V2_REJECTED_GAPS
        )
    return prefix


def _prefix_has_rejected_gap(prefix: Sequence[int], start: int, end: int) -> bool:
    return end > start and prefix[end] > prefix[start]


def _generate_signals_b_v2(
    bars: Sequence[Bar], config: Dict[str, float]
) -> List[Signal]:
    """B_v2: only the first trade beyond a valid pivot can be its sweep."""

    left = int(config["pivot_left"])
    right = int(config["pivot_right"])
    max_age = int(config["pivot_max_age_bars"])
    confirmation = int(config["confirmation_window_bars"])
    sweep_buffer = float(config["sweep_buffer_atr"])
    sl_buffer = float(config["sl_buffer_atr"])
    atr_values = atr(bars, int(config["atr_length"]))
    rejected_gap_prefix = _rejected_gap_prefix(bars)
    pivot_lows: List[_Pivot] = []
    pivot_highs: List[_Pivot] = []
    pending_long: Optional[_Sweep] = None
    pending_short: Optional[_Sweep] = None
    output: List[Signal] = []

    for index, bar in enumerate(bars):
        confirm_index = index - right
        if confirm_index >= left:
            window_start = confirm_index - left
            window_end = confirm_index + right
            window_is_valid = not _prefix_has_rejected_gap(
                rejected_gap_prefix, window_start, window_end
            )
            if window_is_valid and _is_pivot_low(
                bars, confirm_index, left, right
            ):
                pivot_lows.append(_Pivot(confirm_index, bars[confirm_index].low))
            if window_is_valid and _is_pivot_high(
                bars, confirm_index, left, right
            ):
                pivot_highs.append(_Pivot(confirm_index, bars[confirm_index].high))

        if pending_long is not None:
            age = index - pending_long.index
            if 1 <= age <= confirmation and bar.close > pending_long.trigger:
                output.append(
                    Signal(
                        timestamp=bar.timestamp,
                        setup="B",
                        direction="long",
                        entry_index=index + 1,
                        sl=pending_long.extreme - sl_buffer * pending_long.atr,
                        atr=pending_long.atr,
                        reference_index=pending_long.pivot_index,
                        sweep_index=pending_long.index,
                        signal_bar_source=bar.source,
                        strategy_version="v2",
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
                        timestamp=bar.timestamp,
                        setup="B",
                        direction="short",
                        entry_index=index + 1,
                        sl=pending_short.extreme + sl_buffer * pending_short.atr,
                        atr=pending_short.atr,
                        reference_index=pending_short.pivot_index,
                        sweep_index=pending_short.index,
                        signal_bar_source=bar.source,
                        strategy_version="v2",
                    )
                )
                pending_short = None
            elif age > confirmation:
                pending_short = None

        current_atr = atr_values[index]
        if current_atr is None or current_atr <= 0:
            continue

        # A pivot is no longer eligible after expiry, a rejected gap, or its
        # first trade beyond the level. Daily metals breaks remain allowed by
        # the recorded B_v2 decision.
        pivot_lows = [
            pivot for pivot in pivot_lows
            if not pivot.swept
            and 0 <= index - pivot.index <= max_age
            and not _prefix_has_rejected_gap(rejected_gap_prefix, pivot.index, index)
        ]
        pivot_highs = [
            pivot for pivot in pivot_highs
            if not pivot.swept
            and 0 <= index - pivot.index <= max_age
            and not _prefix_has_rejected_gap(rejected_gap_prefix, pivot.index, index)
        ]

        long_candidate: Optional[_Sweep] = None
        for pivot in reversed(pivot_lows):
            if bar.low < pivot.price:
                # This is the first pierce. Invalidate even if it fails the
                # buffer or close-back condition; a later bar cannot sweep it.
                pivot.swept = True
                if (
                    long_candidate is None
                    and bar.low < pivot.price - sweep_buffer * current_atr
                    and bar.close > pivot.price
                ):
                    long_candidate = _Sweep(
                        index, bar.low, bar.high, pivot.index, current_atr
                    )
        if pending_long is None and long_candidate is not None:
            pending_long = long_candidate

        short_candidate: Optional[_Sweep] = None
        for pivot in reversed(pivot_highs):
            if bar.high > pivot.price:
                pivot.swept = True
                if (
                    short_candidate is None
                    and bar.high > pivot.price + sweep_buffer * current_atr
                    and bar.close < pivot.price
                ):
                    short_candidate = _Sweep(
                        index, bar.high, bar.low, pivot.index, current_atr
                    )
        if pending_short is None and short_candidate is not None:
            pending_short = short_candidate

    return output


def generate_signals_b(
    bars: Sequence[Bar], config: Dict[str, object]
) -> List[Signal]:
    version = str(config.get("version", "v1"))
    if version == "v1":
        return _generate_signals_b_v1(bars, config)  # type: ignore
    if version == "v2":
        return _generate_signals_b_v2(bars, config)  # type: ignore
    raise ValueError("Unsupported Setup B version: %s" % version)

