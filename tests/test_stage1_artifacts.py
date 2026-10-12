import csv
import json
import unittest
from datetime import datetime
from pathlib import Path

from src.split_lock import compute_split_lock


ROOT = Path(__file__).resolve().parents[1]


class Stage1ArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(
            (ROOT / "config" / "research.json").read_text(encoding="utf-8")
        )

    def test_locked_split_matches_active_csv(self):
        data_path = ROOT / self.config["data_source"]["normalized_file"]
        split = self.config["split"]
        actual = compute_split_lock(
            data_path,
            float(split["train_fraction"]),
            float(split["validation_fraction"]),
        )
        self.assertEqual(split["test"]["partition_sha256"],
                         actual["test"]["partition_sha256"])
        for partition in ("train", "validation", "test"):
            self.assertEqual(split[partition]["start_utc"],
                             actual[partition]["start_utc"])
            self.assertEqual(split[partition]["end_utc"],
                             actual[partition]["end_utc"])
            self.assertEqual(split[partition]["rows"], actual[partition]["rows"])

    def test_stage1_outputs_stop_before_test_and_keep_source(self):
        test_start = datetime.fromisoformat(self.config["split"]["test"]["start_utc"])
        for filename, timestamp_column, source_columns in (
            ("signals_stage1.csv", "signal_timestamp", ("source", "entry_source")),
            ("trades_stage1.csv", "signal_timestamp",
             ("signal_source", "entry_source", "exit_source")),
        ):
            with (ROOT / "reports" / filename).open(
                "r", encoding="utf-8", newline=""
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(rows)
            self.assertTrue(all(datetime.fromisoformat(row[timestamp_column]) < test_start
                                for row in rows))
            for column in source_columns:
                self.assertTrue(all(row[column] for row in rows))

    def test_cost_scenarios_and_truncation_audit_are_present(self):
        report = json.loads(
            (ROOT / "reports" / "stage1_label.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {"normal", "plus_50_percent", "plus_100_percent"},
            set(report["cost_scenarios"]),
        )
        for scenario, details in report["cost_scenarios"].items():
            self.assertTrue(Path(details["trades_csv"]).exists(), scenario)
            self.assertTrue(Path(details["skipped_signals_csv"]).exists(), scenario)
            counts = details["labeling_audit"]["truncated_trade_counts_by_gap_type"]
            self.assertEqual(
                details["labeling_audit"]["truncated_trade_count"], sum(counts.values())
            )

        with (ROOT / "reports" / "trades_stage1.csv").open(
            "r", encoding="utf-8", newline=""
        ) as handle:
            first = next(csv.DictReader(handle))
        self.assertIn("truncated_by_gap", first)
        self.assertIn("gap_type_after_exit", first)
        self.assertEqual("normal", first["cost_scenario"])

    def test_setup_a_chart_spec_has_required_context(self):
        report = json.loads(
            (ROOT / "reports" / "stage1_label.json").read_text(encoding="utf-8")
        )
        chart = report["chart_spec"]["A"]
        self.assertGreaterEqual(chart["pre_signal_bars"], 60)
        self.assertEqual(
            {"EMA20", "rolling_highest_20", "rolling_lowest_20"},
            set(chart["overlays"]),
        )
        self.assertEqual(
            {"previous_closed_H1_EMA50", "previous_closed_H1_EMA200",
             "previous_closed_H1_ADX14"},
            set(chart["annotation"]),
        )

    def test_setup_b_chart_and_pivot_audit_contract(self):
        report = json.loads(
            (ROOT / "reports" / "stage1_label.json").read_text(encoding="utf-8")
        )
        chart = report["chart_spec"]["B"]
        self.assertGreaterEqual(chart["pre_signal_bars"], 80)
        self.assertEqual(
            {"horizontal_pivot_line_with_price", "highlighted_sweep_bar"},
            set(chart["overlays"]),
        )
        audit = json.loads(
            (ROOT / "reports" / "stage1_setup_b_v2_pivot_audit.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual("v2", audit["strategy_version"])
        self.assertEqual(1222, audit["signals_checked"])
        self.assertEqual(0, audit["strict_pivot_5_5_violations"])
        self.assertEqual(
            0, audit["already_closed_beyond_before_sweep"]["total"]
        )
        self.assertEqual(
            0, audit["prior_pierce_beyond_before_sweep"]["total"]
        )

        gap_audit = json.loads(
            (ROOT / "reports" / "stage1_setup_b_v1_gap_audit.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(4268, gap_audit["signals_checked"])
        self.assertEqual(546, gap_audit["pivot_window"]["signals_with_any_gap"])
        self.assertEqual(1047, gap_audit["pivot_to_sweep"]["signals_with_any_gap"])

    def test_b_v2_counts_and_setup_a_counts_are_stable(self):
        signal_report = json.loads(
            (ROOT / "reports" / "stage1_signals.json").read_text(encoding="utf-8")
        )
        counts = signal_report["signal_audit"]["kept_by_setup_direction"]
        self.assertEqual(4745, counts["A_long"])
        self.assertEqual(3104, counts["A_short"])
        self.assertEqual(624, counts["B_long"])
        self.assertEqual(598, counts["B_short"])

        with (ROOT / "reports" / "signals_stage1.csv").open(
            "r", encoding="utf-8", newline=""
        ) as handle:
            b_rows = [row for row in csv.DictReader(handle) if row["setup"] == "B"]
        self.assertTrue(b_rows)
        self.assertTrue(all(row["strategy_version"] == "v2" for row in b_rows))

        count_report = json.loads(
            (ROOT / "reports" / "stage1_setup_b_v2_counts.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            {"long": 475, "short": 444, "total": 919},
            count_report["signals"]["train"],
        )
        self.assertEqual(
            {"long": 149, "short": 154, "total": 303},
            count_report["signals"]["validation"],
        )
        self.assertEqual(
            {"long": 383, "short": 377, "total": 760},
            count_report["trades_by_cost_scenario"]["normal"]["train"],
        )
        self.assertEqual(
            {"long": 118, "short": 130, "total": 248},
            count_report["trades_by_cost_scenario"]["normal"]["validation"],
        )
        self.assertFalse(count_report["expectancy_computed"])
        self.assertFalse(count_report["profit_factor_computed"])

    def test_exactly_twenty_charts_per_setup(self):
        for setup in ("a", "b"):
            paths = list((ROOT / "reports" / "charts" / ("setup_" + setup)).glob("*.png"))
            self.assertEqual(20, len(paths))


if __name__ == "__main__":
    unittest.main()
