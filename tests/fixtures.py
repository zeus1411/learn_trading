import math
import random
from datetime import datetime, timedelta, timezone
from typing import List

from src.models import Bar


def synthetic_m15_bars(count: int = 1800, seed: int = 20261010) -> List[Bar]:
    """Deterministic causal test fixture; never valid as a research dataset."""

    rng = random.Random(seed)
    timestamp = datetime(2022, 1, 3, tzinfo=timezone.utc)
    price = 1800.0
    output: List[Bar] = []
    for index in range(count):
        cycle = 0.08 * math.sin(index / 19.0) + 0.04 * math.sin(index / 67.0)
        shock = rng.gauss(0.0, 0.18)
        if index % 137 == 0:
            shock += 1.4 if (index // 137) % 2 == 0 else -1.4
        open_ = price
        close = open_ + 0.015 + cycle + shock
        wick = 0.08 + abs(rng.gauss(0.0, 0.12))
        high = max(open_, close) + wick
        low = min(open_, close) - wick * (0.8 + 0.4 * rng.random())
        output.append(Bar(timestamp, open_, high, low, close))
        price = close
        timestamp += timedelta(minutes=15)
    return output

