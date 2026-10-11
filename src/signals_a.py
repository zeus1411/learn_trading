from typing import Dict, List, Optional, Sequence, Tuple

from .features import atr, ema, shifted_h1_features
from .models import Bar, Signal


def generate_signals_a(
    bars: Sequence[Bar], config: Dict[str, float]
) -> List[Signal]:
    atr_values = atr(bars, int(config["atr_length"]))
    ema20 = ema([bar.close for bar in bars], int(config["m15_ema_length"]))
    h1 = shifted_h1_features(
        bars,
        int(config["h1_fast_ema_length"]),
        int(config["h1_slow_ema_length"]),
        int(config["adx_length"]),
    )
    lookback = int(config["pullback_lookback"])
    output: List[Signal] = []
    for index, bar in enumerate(bars):
        current_atr = atr_values[index]
        trend = h1[index]
        if index < max(lookback - 1, 3) or current_atr is None or current_atr <= 0:
            continue
        if trend.ema50 is None or trend.ema200 is None or trend.adx14 is None:
            continue
        range_ = bar.high - bar.low
        if range_ <= 0:
            continue
        highs = [item.high for item in bars[index - lookback + 1 : index + 1]]
        lows = [item.low for item in bars[index - lookback + 1 : index + 1]]
        previous = bars[index - 3 : index]
        minimum = config["pullback_min_atr"] * current_atr
        maximum = config["pullback_max_atr"] * current_atr
        threshold = config["adx_threshold"]

        long_depth = max(highs) - bar.low
        long_condition = (
            trend.ema50 > trend.ema200
            and trend.adx14 > threshold
            and minimum <= long_depth <= maximum
            and bar.low <= ema20[index] + config["ema_touch_atr"] * current_atr  # type: ignore
            and bar.close > ema20[index]  # type: ignore
            and bar.close > bar.open
            and (bar.close - bar.low) / range_ >= 0.60
        )
        if long_condition:
            output.append(
                Signal(
                    bar.timestamp,
                    "A",
                    "long",
                    index + 1,
                    min([bar.low] + [item.low for item in previous])
                    - config["sl_buffer_atr"] * current_atr,
                    current_atr,
                )
            )

        short_depth = bar.high - min(lows)
        short_condition = (
            trend.ema50 < trend.ema200
            and trend.adx14 > threshold
            and minimum <= short_depth <= maximum
            and bar.high >= ema20[index] - config["ema_touch_atr"] * current_atr  # type: ignore
            and bar.close < ema20[index]  # type: ignore
            and bar.close < bar.open
            and (bar.close - bar.low) / range_ <= 0.40
        )
        if short_condition:
            output.append(
                Signal(
                    bar.timestamp,
                    "A",
                    "short",
                    index + 1,
                    max([bar.high] + [item.high for item in previous])
                    + config["sl_buffer_atr"] * current_atr,
                    current_atr,
                )
            )
    return output

