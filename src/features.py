from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from .models import Bar, HTFFeatures


def ema(values: Sequence[float], length: int) -> List[Optional[float]]:
    if length <= 0:
        raise ValueError("EMA length must be positive")
    alpha = 2.0 / (length + 1.0)
    result: List[Optional[float]] = []
    state: Optional[float] = None
    for value in values:
        state = value if state is None else alpha * value + (1.0 - alpha) * state
        result.append(state)
    return result


def true_ranges(bars: Sequence[Bar]) -> List[float]:
    result: List[float] = []
    previous_close: Optional[float] = None
    for bar in bars:
        if previous_close is None:
            value = bar.high - bar.low
        else:
            value = max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        result.append(value)
        previous_close = bar.close
    return result


def wilder(values: Sequence[float], length: int) -> List[Optional[float]]:
    if length <= 0:
        raise ValueError("Wilder length must be positive")
    result: List[Optional[float]] = [None] * len(values)
    if len(values) < length:
        return result
    state = sum(values[:length]) / length
    result[length - 1] = state
    for index in range(length, len(values)):
        state = ((length - 1) * state + values[index]) / length
        result[index] = state
    return result


def atr(bars: Sequence[Bar], length: int = 14) -> List[Optional[float]]:
    return wilder(true_ranges(bars), length)


def adx(bars: Sequence[Bar], length: int = 14) -> List[Optional[float]]:
    if not bars:
        return []
    tr = true_ranges(bars)
    plus_dm = [0.0]
    minus_dm = [0.0]
    for previous, current in zip(bars, bars[1:]):
        up = current.high - previous.high
        down = previous.low - current.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    atr_values = wilder(tr, length)
    plus_values = wilder(plus_dm, length)
    minus_values = wilder(minus_dm, length)
    dx: List[Optional[float]] = [None] * len(bars)
    for index in range(len(bars)):
        if atr_values[index] is None or not atr_values[index]:
            continue
        plus_di = 100.0 * plus_values[index] / atr_values[index]  # type: ignore
        minus_di = 100.0 * minus_values[index] / atr_values[index]  # type: ignore
        denominator = plus_di + minus_di
        dx[index] = 0.0 if denominator == 0 else 100.0 * abs(plus_di - minus_di) / denominator
    result: List[Optional[float]] = [None] * len(bars)
    valid = [(index, value) for index, value in enumerate(dx) if value is not None]
    if len(valid) < length:
        return result
    start_position = length - 1
    seed_index = valid[start_position][0]
    state = sum(value for _, value in valid[:length]) / length  # type: ignore
    result[seed_index] = state
    for index, value in valid[length:]:
        state = ((length - 1) * state + value) / length  # type: ignore
        result[index] = state
    return result


def _bucket_start(timestamp: datetime, hours: int) -> datetime:
    utc = timestamp.astimezone(timezone.utc)
    return utc.replace(hour=(utc.hour // hours) * hours, minute=0, second=0, microsecond=0)


def resample_ohlc(bars: Sequence[Bar], hours: int) -> List[Bar]:
    if hours not in (1, 4):
        raise ValueError("Only H1 and H4 resampling is supported")
    output: List[Bar] = []
    current_bucket: Optional[datetime] = None
    current: Optional[Bar] = None
    for bar in bars:
        bucket = _bucket_start(bar.timestamp, hours)
        if bucket != current_bucket:
            if current is not None:
                output.append(current)
            current_bucket = bucket
            current = Bar(bucket, bar.open, bar.high, bar.low, bar.close)
        else:
            assert current is not None
            current = Bar(
                current.timestamp,
                current.open,
                max(current.high, bar.high),
                min(current.low, bar.low),
                bar.close,
            )
    if current is not None:
        output.append(current)
    return output


def shifted_h1_features(
    m15_bars: Sequence[Bar], fast_length: int = 50, slow_length: int = 200, adx_length: int = 14
) -> List[HTFFeatures]:
    """Map prior H1-bucket features to M15; current H1 is never visible."""

    h1 = resample_ohlc(m15_bars, 1)
    fast = ema([bar.close for bar in h1], fast_length)
    slow = ema([bar.close for bar in h1], slow_length)
    strength = adx(h1, adx_length)
    index_by_bucket = {bar.timestamp: index for index, bar in enumerate(h1)}
    result: List[HTFFeatures] = []
    for bar in m15_bars:
        bucket = _bucket_start(bar.timestamp, 1)
        current_index = index_by_bucket[bucket]
        source_index = current_index - 1
        if source_index < 0:
            result.append(HTFFeatures(None, None, None))
        else:
            result.append(
                HTFFeatures(fast[source_index], slow[source_index], strength[source_index])
            )
    return result


def shifted_h4_bars(m15_bars: Sequence[Bar]) -> List[Optional[Bar]]:
    """Return the prior complete H4 bucket for every M15 bar."""

    h4 = resample_ohlc(m15_bars, 4)
    index_by_bucket = {bar.timestamp: index for index, bar in enumerate(h4)}
    result: List[Optional[Bar]] = []
    for bar in m15_bars:
        current_index = index_by_bucket[_bucket_start(bar.timestamp, 4)]
        result.append(None if current_index == 0 else h4[current_index - 1])
    return result

