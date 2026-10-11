import json
import random
from collections import Counter
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .features import shifted_h1_features, shifted_h4_bars
from .gaps import UNEXPECTED, classify_gap
from .models import Bar, Signal
from .signals_a import generate_signals_a
from .signals_b import generate_signals_b


def load_config(path: Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _apply_unexpected_gap_lookback(
    bars: Sequence[Bar], signals: Sequence[Signal], lookback: int
) -> Tuple[List[Signal], Dict[str, object]]:
    last_unexpected = -10 ** 9
    blocked = [False] * len(bars)
    unexpected_arrival_indices: List[int] = []
    for index in range(1, len(bars)):
        if classify_gap(bars[index - 1].timestamp, bars[index].timestamp) == UNEXPECTED:
            last_unexpected = index
            unexpected_arrival_indices.append(index)
        blocked[index] = index - last_unexpected < lookback
    kept: List[Signal] = []
    suppressed = Counter()
    for signal in signals:
        signal_index = signal.entry_index - 1
        if 0 <= signal_index < len(blocked) and blocked[signal_index]:
            suppressed[(signal.setup, signal.direction)] += 1
        else:
            kept.append(signal)
    return kept, {
        "lookback_m15_bars": lookback,
        "unexpected_gap_events": len(unexpected_arrival_indices),
        "suppressed_total": sum(suppressed.values()),
        "suppressed_by_setup_direction": {
            "%s_%s" % key: value for key, value in sorted(suppressed.items())
        },
    }


def generate_all_signals_with_audit(
    bars: Sequence[Bar], config: Dict[str, object]
) -> Tuple[List[Signal], Dict[str, object]]:
    setup_a = generate_signals_a(bars, config["setup_a"])  # type: ignore
    setup_b = generate_signals_b(bars, config["setup_b"])  # type: ignore
    raw = setup_a + setup_b
    lookback = int(
        config.get("gap_policy", {}).get(  # type: ignore
            "signal_block_unexpected_gap_lookback_m15_bars", 100
        )
    )
    output, gap_audit = _apply_unexpected_gap_lookback(bars, raw, lookback)
    output.sort(key=lambda signal: (signal.timestamp, signal.setup, signal.direction))
    raw_counts = Counter((signal.setup, signal.direction) for signal in raw)
    kept_counts = Counter((signal.setup, signal.direction) for signal in output)
    return output, {
        "raw_signal_count": len(raw),
        "kept_signal_count": len(output),
        "raw_by_setup_direction": {
            "%s_%s" % key: value for key, value in sorted(raw_counts.items())
        },
        "kept_by_setup_direction": {
            "%s_%s" % key: value for key, value in sorted(kept_counts.items())
        },
        "gap_policy": gap_audit,
    }


def generate_all_signals(bars: Sequence[Bar], config: Dict[str, object]) -> List[Signal]:
    output, _ = generate_all_signals_with_audit(bars, config)
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
    full_h1 = shifted_h1_features(
        bars,
        int(config["setup_a"]["h1_fast_ema_length"]),  # type: ignore
        int(config["setup_a"]["h1_slow_ema_length"]),  # type: ignore
        int(config["setup_a"]["adx_length"]),  # type: ignore
    )
    full_h4 = shifted_h4_bars(bars)
    mismatches: List[Dict[str, object]] = []
    for index in chosen:
        prefix_bars = bars[: index + 1]
        prefix = generate_all_signals(prefix_bars, config)
        expected = _signals_at_index(full, index)
        actual = _signals_at_index(prefix, index)
        prefix_h1 = shifted_h1_features(
            prefix_bars,
            int(config["setup_a"]["h1_fast_ema_length"]),  # type: ignore
            int(config["setup_a"]["h1_slow_ema_length"]),  # type: ignore
            int(config["setup_a"]["adx_length"]),  # type: ignore
        )[-1]
        prefix_h4 = shifted_h4_bars(prefix_bars)[-1]
        if (
            actual != expected
            or prefix_h1 != full_h1[index]
            or prefix_h4 != full_h4[index]
        ):
            mismatches.append(
                {
                    "index": index,
                    "timestamp": bars[index].timestamp.isoformat(),
                    "prefix": actual,
                    "full": expected,
                    "h1_prefix": repr(prefix_h1),
                    "h1_full": repr(full_h1[index]),
                    "h4_prefix": repr(prefix_h4),
                    "h4_full": repr(full_h4[index]),
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
        "signal_function": "src.pipeline.generate_all_signals",
        "component_coverage": [
            "Setup A real signal function",
            "Setup B real signal function",
            "confirmed M15 pivots with right-bar delay",
            "previous closed H1 EMA50/EMA200/ADX features",
            "previous closed H4 bar mapping (verified although not a setup condition)",
            "100-M15-bar unexpected-gap signal filter",
        ],
    }

