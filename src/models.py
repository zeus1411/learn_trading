from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class Bar:
    """One UTC OHLC bar. Timestamp is the bar-open time."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    source: str = "UNSPECIFIED"


@dataclass(frozen=True)
class Signal:
    timestamp: datetime
    setup: str
    direction: str
    entry_index: int
    sl: float
    atr: float
    reference_index: Optional[int] = None
    sweep_index: Optional[int] = None
    signal_bar_source: str = "UNSPECIFIED"

    def comparable(self) -> Dict[str, Any]:
        result = asdict(self)
        result["timestamp"] = self.timestamp.isoformat()
        return result


@dataclass(frozen=True)
class HTFFeatures:
    ema50: Optional[float]
    ema200: Optional[float]
    adx14: Optional[float]

