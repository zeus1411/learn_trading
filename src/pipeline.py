import json
import random
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .models import Bar, Signal
from .signals_a import generate_signals_a
from .signals_b import generate_signals_b


def load_config(path: Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def generate_all_signals(bars: Sequence[Bar], config: Dict[str, object]) -> List[Signal]:
    output = generate_signals_a(bars, config["setup_a"])  # type: ignore
    output.extend(generate_signals_b(bars, config["setup_b"]))  # type: ignore
    output.sort(key=lambda signal: (signal.timestamp, signal.setup, signal.direction))
    return output


def _signals_at_index(signals: Sequence[Signal], index: int) -> List[Dict[str, object]]:
    return [signal.comparable() for signal in signals if signal.entry_index == index + 1]


def test_no_lookahead(
    bars: Sequence[Bar], config: Dict[str, object], samples: int = 50
) -> Dict[str, object]:
    """Required prefix-invariance test for signals at 50 seeded timestamps."""

    if len(bars) < samples:
        raise AssertionError("Need at least %d bars for no-lookahead test" % samples)
    seed = int(config["random_seed"])
    rng = random.Random(seed)
    population_start = min(len(bars) - 1, 20)
    candidates = list(range(population_start, len(bars)))
    if len(candidates) < samples:
        candidates = list(range(len(bars)))
    chosen = sorted(rng.sample(candidates, samples))
    full = generate_all_signals(bars, config)
    mismatches: List[Dict[str, object]] = []
    for index in chosen:
        prefix = generate_all_signals(bars[: index + 1], config)
        expected = _signals_at_index(full, index)
        actual = _signals_at_index(prefix, index)
        if actual != expected:
            mismatches.append(
                {
                    "index": index,
                    "timestamp": bars[index].timestamp.isoformat(),
                    "prefix": actual,
                    "full": expected,
                }
            )
    if mismatches:
        raise AssertionError(
            "No-lookahead failed at %d/%d timestamps: %s"
            % (len(mismatches), samples, json.dumps(mismatches[:3], indent=2))
        )
    return {
        "status": "PASS",
        "seed": seed,
        "samples": samples,
        "sample_indices": chosen,
        "sample_timestamps": [bars[index].timestamp.isoformat() for index in chosen],
        "full_signal_count": len(full),
    }

