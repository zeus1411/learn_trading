"""Compute deterministic chronological split boundaries without evaluating trades."""

import csv
import hashlib
import json
from pathlib import Path
from typing import Dict, List


ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "config" / "research.json"


def compute_split_lock(data_path: Path, train_fraction: float, validation_fraction: float) -> Dict[str, object]:
    with data_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV has no header")
        fields = list(reader.fieldnames)
        rows: List[Dict[str, str]] = [dict(row) for row in reader]
    total = len(rows)
    train_rows = int(total * train_fraction)
    validation_rows = int(total * validation_fraction)
    test_start = train_rows + validation_rows
    if not (0 < train_rows < test_start < total):
        raise ValueError("Invalid chronological split")

    digest = hashlib.sha256()
    definition = (
        "SHA256 of UTF-8 canonical JSON arrays; first array is ordered CSV field names, "
        "followed by one array of ordered string field values per test row, each plus LF"
    )
    digest.update((json.dumps(fields, separators=(",", ":")) + "\n").encode("utf-8"))
    for row in rows[test_start:]:
        values = [row.get(field, "") for field in fields]
        digest.update((json.dumps(values, separators=(",", ":")) + "\n").encode("utf-8"))

    return {
        "method": "chronological_rows_floor_60_20_20",
        "data_file": str(data_path.resolve()),
        "total_rows": total,
        "train": {
            "start_utc": rows[0]["timestamp"],
            "end_utc": rows[train_rows - 1]["timestamp"],
            "rows": train_rows,
        },
        "validation": {
            "start_utc": rows[train_rows]["timestamp"],
            "end_utc": rows[test_start - 1]["timestamp"],
            "rows": validation_rows,
        },
        "test": {
            "start_utc": rows[test_start]["timestamp"],
            "end_utc": rows[-1]["timestamp"],
            "rows": total - test_start,
            "partition_sha256": digest.hexdigest(),
            "hash_definition": definition,
            "locked": True,
            "results_read_during_development": False,
        },
    }


def main() -> int:
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    data_path = ROOT / config["data_source"]["normalized_file"]
    split = config["split"]
    lock = compute_split_lock(
        data_path,
        float(split["train_fraction"]),
        float(split["validation_fraction"]),
    )
    print(json.dumps(lock, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
