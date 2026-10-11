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

    def test_exactly_twenty_charts_per_setup(self):
        for setup in ("a", "b"):
            paths = list((ROOT / "reports" / "charts" / ("setup_" + setup)).glob("*.png"))
            self.assertEqual(20, len(paths))


if __name__ == "__main__":
    unittest.main()
