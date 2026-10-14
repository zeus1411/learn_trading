import copy
import csv
import io
import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np

import run
from src.evaluate import evaluate_cost_scenario_views, evaluate_development
from src.stage2_data import (DevelopmentBoundary, DevelopmentTrades, SCENARIO_FILES,
                             assert_locked_integrity, load_development_trades, parse_development_trade)
from src.stage2_metrics import block_bootstrap, frozen_gate_check, metrics_cube, setup_decisions, summarize, wilson_interval
from src.walkforward import rolling_oos
from tests.test_evaluate import trade


ROOT = Path(__file__).resolve().parents[1]


class Stage2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((ROOT / "config" / "research.json").read_text())
        cls.boundary = DevelopmentBoundary.from_config(cls.config)

    def valid_trade(self, value=-1, when=None, index=1):
        when = when or self.boundary.validation_start + timedelta(days=1)
        return replace(trade(value), signal_timestamp=when, entry_timestamp=when + timedelta(minutes=15),
                       exit_timestamp=when + timedelta(minutes=15), signal_index=index,
                       entry_index=index + 1, exit_index=index + 1)

    def bundle(self, trades):
        return DevelopmentTrades(self.boundary, tuple(
            (name, tuple(replace(t, cost_scenario=name) for t in trades)) for name in SCENARIO_FILES))

    def test_test_signal_rejected_before_pnl_is_read(self):
        class Trap(dict):
            def __getitem__(self, key):
                if key != "signal_timestamp":
                    raise AssertionError("Test result accessed: " + key)
                return self.get(key)
        with self.assertRaisesRegex(ValueError, "outside development"):
            parse_development_trade(Trap(signal_timestamp=self.boundary.test_start.isoformat()), self.boundary)

    def test_test_entry_exit_reference_sweep_and_index_are_rejected(self):
        item = self.valid_trade()
        for field in ("signal_timestamp", "entry_timestamp", "exit_timestamp"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.bundle([replace(item, **{field: self.boundary.test_start})])
        for field in ("reference_timestamp", "sweep_timestamp"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.bundle([replace(item, **{field: self.boundary.test_start.isoformat()})])
        with self.assertRaises(ValueError):
            self.bundle([replace(item, exit_index=self.boundary.n_rows)])

    def test_b_v1_rejected(self):
        with self.assertRaisesRegex(ValueError, "B_v1"):
            self.bundle([replace(self.valid_trade(), setup="B", strategy_version="v1")])

    def test_metrics_cube_rejects_test_trade_even_without_bundle(self):
        poisoned = replace(self.valid_trade(), exit_timestamp=self.boundary.test_start)
        with self.assertRaises(ValueError):
            metrics_cube({"normal": [poisoned]}, self.config, "test")

    def test_evaluation_opens_no_files(self):
        bundle = self.bundle([self.valid_trade()])
        with patch.object(Path, "open", side_effect=AssertionError("Evaluator attempted file access")), \
             patch("builtins.open", side_effect=AssertionError("Evaluator attempted file access")):
            actual = evaluate_development(bundle, self.config)
        self.assertEqual(1, actual["validation"]["A"]["normal"]["include_truncated"]["combined"]["n_trades"])

    def test_public_evaluator_rejects_raw_or_tampered_test_input(self):
        with self.assertRaises(TypeError):
            evaluate_development([self.valid_trade()], self.config)
        bundle = self.bundle([self.valid_trade()])
        poisoned = replace(self.valid_trade(), exit_timestamp=self.boundary.test_start)
        object.__setattr__(bundle, "scenarios", tuple((name, (replace(poisoned, cost_scenario=name),)) for name in SCENARIO_FILES))
        with self.assertRaises(ValueError):
            evaluate_development(bundle, self.config)
        with self.assertRaises(ValueError):
            rolling_oos(bundle, self.config)

    def test_loader_opens_only_development_trade_files(self):
        row = self.valid_trade().to_dict()
        streams = {}
        for scenario, filename in SCENARIO_FILES.items():
            data = dict(row, cost_scenario=scenario)
            handle = io.StringIO()
            writer = csv.DictWriter(handle, fieldnames=list(data))
            writer.writeheader()
            writer.writerow(data)
            streams[filename] = handle.getvalue()
        opened = []
        def safe_open(path, *args, **kwargs):
            opened.append(path.name)
            if path.name not in streams:
                raise AssertionError("Attempted market/test read")
            return io.StringIO(streams[path.name])
        with patch.object(Path, "open", safe_open):
            result = load_development_trades(ROOT / "reports", self.config)
        self.assertEqual(set(SCENARIO_FILES.values()), set(opened))
        self.assertEqual(3, len(result.scenarios))

    def test_full_frozen_dataset_integrity_checker_exposes_only_hashes(self):
        result = assert_locked_integrity(ROOT / self.config["data_source"]["normalized_file"], self.config)
        self.assertEqual(0, result["test_rows_exposed_to_evaluator"])
        self.assertEqual(self.config["split"]["test"]["partition_sha256"], result["test_partition_sha256"])
        changed = copy.deepcopy(self.config)
        changed["split"]["test"]["partition_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            assert_locked_integrity(ROOT / changed["data_source"]["normalized_file"], changed)

    def test_wilson_known_values_and_zero_case(self):
        actual = wilson_interval(5, 10)
        self.assertAlmostEqual(0.2365930905, actual[0])
        self.assertAlmostEqual(0.7634069095, actual[1])
        self.assertEqual([None, None], wilson_interval(0, 0))

    def test_metrics_signed_timeout_drawdown_and_streak(self):
        start = self.boundary.validation_start
        items = [replace(self.valid_trade(value, start + timedelta(hours=i), 10+i*3), outcome=outcome)
                 for i, (value, outcome) in enumerate([(2, "WIN"), (-1, "LOSS"), (-1, "LOSS"), (0.5, "TIMEOUT")])]
        result = summarize(items, bootstrap=False)
        self.assertEqual(0.5, result["win_rate"])
        self.assertEqual(0.25, result["barrier_win_rate"])
        self.assertEqual(1.25, result["average_win_r"])
        self.assertEqual(-1, result["average_loss_r"])
        self.assertEqual(0.125, result["expectancy_r"])
        self.assertEqual(1.25, result["profit_factor"])
        self.assertEqual(2, result["max_drawdown_r"])
        self.assertEqual(2, result["longest_losing_streak"])

    def test_simultaneous_exits_do_not_create_artificial_drawdown(self):
        items = [self.valid_trade(2), replace(self.valid_trade(-1), direction="short")]
        result = summarize(items, bootstrap=False)
        self.assertEqual(0, result["max_drawdown_r"])
        self.assertEqual(1, len(result["equity_curve_r"]))

    def test_bootstrap_deterministic_and_adjustments_widen(self):
        values = [-1] * 20 + [2] * 20 + [-0.3] * 20
        a = block_bootstrap(values, 812, 2, 3)
        b = block_bootstrap(values, 812, 2, 3)
        self.assertEqual(a, b)
        self.assertEqual(10000, a["resamples"])
        self.assertEqual(10, a["block_size"])
        self.assertLessEqual(a["ci95_adjusted_global"][0], a["ci95_adjusted_setup"][0])
        self.assertLessEqual(a["ci95_adjusted_setup"][0], a["ci95_raw"][0])
        self.assertGreaterEqual(a["p_positive_adjusted_global"], a["p_positive_raw"])
        self.assertEqual([0.4, 0.4], block_bootstrap([0.4]*20, 7)["ci95_raw"])

    def test_bootstrap_matches_explicit_circular_blocks(self):
        values = np.array([-1.0, 0.2, 2.0, -0.8, 0.3, -1.0, 0.4])
        n, block, resamples = len(values), 3, 1000
        rng = np.random.RandomState(42)
        means = []
        for start in range(0, resamples, 256):
            size = min(256, resamples-start)
            picks = rng.randint(0, n, size=(size, 3))
            for selected in picks:
                sample = [values[(index+offset) % n] for index in selected for offset in range(block)][:n]
                means.append(sum(sample)/n)
        expected = np.quantile(means, [0.025, 0.975])
        actual = block_bootstrap(values, 42, resamples=resamples, block_size=block)
        np.testing.assert_allclose(expected, actual["ci95_raw"])

    def test_frozen_gate_thresholds_and_b_override(self):
        sample = summarize([self.valid_trade(0.2)] * 300)
        gate = frozen_gate_check(sample, sample)
        self.assertEqual("PASS", gate["status"])
        low = dict(sample, n_trades=299)
        self.assertEqual("FAIL", frozen_gate_check(low, sample)["status"])
        bad = dict(sample, expectancy_r=0.099999)
        self.assertFalse(frozen_gate_check(bad, sample)["checks"]["net_expectancy_ge_0_10r"])
        at_zero = copy.deepcopy(sample)
        at_zero["bootstrap"]["ci95_raw"][0] = 0
        self.assertFalse(frozen_gate_check(at_zero, sample)["checks"]["raw_ci95_lower_gt_zero"])
        limits = dict(sample, n_trades=300, expectancy_r=0.1, profit_factor=1.15,
                      profit_factor_infinite=False, positive_year_fraction=0.7, positive_quarter_fraction=0.7)
        self.assertEqual("PASS", frozen_gate_check(limits, sample)["status"])
        for key in ("positive_year_fraction", "positive_quarter_fraction"):
            self.assertEqual("FAIL", frozen_gate_check(dict(limits, **{key: 0.699999}), sample)["status"])
        self.assertEqual("FAIL", frozen_gate_check(limits, dict(sample, expectancy_r=0))["status"])
        cube = {setup: {scenario: {view: {"combined": sample} for view in ("include_truncated", "exclude_truncated")}
                        for scenario in SCENARIO_FILES} for setup in ("A", "B")}
        decisions = setup_decisions(cube, cube)
        self.assertEqual("PASS", decisions["A"]["status"])
        self.assertEqual("INSUFFICIENT_DATA", decisions["B"]["status"])

    def test_walk_forward_no_overlap_partial_and_crossing_exclusion(self):
        start = self.boundary.validation_start
        end = datetime(2024, 4, 22, 10, 15, tzinfo=timezone.utc)
        first = self.valid_trade(2, start + timedelta(hours=1))
        crossing = replace(self.valid_trade(-1, end - timedelta(minutes=30), 10), exit_timestamp=end)
        second = self.valid_trade(-1, end, 20)
        tail = self.valid_trade(2, datetime(2025, 5, 1, tzinfo=timezone.utc), 30)
        with patch("src.walkforward.metrics_cube", side_effect=lambda scenarios, config, context: scenarios):
            wf = rolling_oos(self.bundle([first, crossing, second, tail]), self.config)
        self.assertEqual(5, wf["complete_windows"])
        self.assertEqual(6, len(wf["windows"]))
        self.assertFalse(wf["windows"][-1]["included_in_primary_oos"])
        self.assertEqual(1, wf["windows"][0]["boundary_crossing_trades_excluded"]["normal"])
        self.assertEqual([first, second], wf["primary_oos"]["normal"])
        for previous, following in zip(wf["windows"], wf["windows"][1:]):
            self.assertEqual(previous["end_exclusive_utc"], following["start_utc"])

    def test_final_test_and_external_data_override_remain_blocked(self):
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(2, run.main(["--stage", "final_test"]))
            self.assertEqual(2, run.main(["--stage", "evaluate", "--data", "E:/rawdata/forbidden.csv"]))


if __name__ == "__main__":
    unittest.main()
