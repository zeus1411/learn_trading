import unittest
from pathlib import Path

from src.features import shifted_h1_features
from src.pipeline import generate_all_signals, load_config, test_no_lookahead
from src.signals_b import generate_signals_b
from tests.fixtures import synthetic_m15_bars


ROOT = Path(__file__).resolve().parents[1]


class NoLookaheadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(ROOT / "config" / "research.json")
        cls.bars = synthetic_m15_bars()

    def test_no_lookahead(self) -> None:
        """Mandated 50-timestamp full-history versus prefix comparison."""

        result = test_no_lookahead(self.bars, self.config, samples=50)
        self.assertEqual("PASS", result["status"])
        self.assertEqual(50, result["samples"])

    def test_h1_features_use_previous_bucket(self) -> None:
        baseline = shifted_h1_features(self.bars[:120])
        mutated = list(self.bars[:120])
        target = 83
        bar = mutated[target]
        mutated[target] = type(bar)(
            bar.timestamp,
            bar.open,
            bar.high + 500.0,
            bar.low - 500.0,
            bar.close + 400.0,
        )
        changed = shifted_h1_features(mutated)
        # Other bars in the same H1 bucket cannot observe the mutation.
        bucket_start = target - target % 4
        for index in range(bucket_start, bucket_start + 4):
            self.assertEqual(baseline[index], changed[index])

    def test_pivots_are_not_available_before_right_delay(self) -> None:
        cfg = self.config["setup_b"]
        right = int(cfg["pivot_right"])
        full = generate_signals_b(self.bars[:400], cfg)
        for signal in full:
            if signal.reference_index is not None:
                signal_index = signal.entry_index - 1
                self.assertGreaterEqual(signal_index, signal.reference_index + right)

    def test_future_bar_mutation_cannot_change_past_signals(self) -> None:
        cutoff = 900
        prefix_signals = generate_all_signals(self.bars[: cutoff + 1], self.config)
        mutated = list(self.bars)
        future = mutated[cutoff + 50]
        mutated[cutoff + 50] = type(future)(
            future.timestamp,
            future.open,
            future.high + 1000.0,
            future.low - 1000.0,
            future.close + 900.0,
        )
        full_signals = generate_all_signals(mutated, self.config)
        expected = [s.comparable() for s in prefix_signals]
        actual = [
            s.comparable()
            for s in full_signals
            if s.entry_index - 1 <= cutoff
        ]
        self.assertEqual(expected, actual)


if __name__ == "__main__":
    unittest.main()

