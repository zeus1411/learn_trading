"""Guarded development trades and a separate opaque test-integrity checker."""

import csv
import hashlib
import json
import math
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Mapping, Tuple

from .labeling import Trade


SCENARIO_FILES = {
    "normal": "trades_stage1.csv",
    "plus_50_percent": "trades_stage1_cost_plus_50_percent.csv",
    "plus_100_percent": "trades_stage1_cost_plus_100_percent.csv",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class DevelopmentBoundary:
    start: datetime
    train_end: datetime
    validation_start: datetime
    end: datetime
    test_start: datetime
    n_rows: int

    @classmethod
    def from_config(cls, config):
        split = config["split"]
        return cls(
            datetime.fromisoformat(split["train"]["start_utc"]),
            datetime.fromisoformat(split["train"]["end_utc"]),
            datetime.fromisoformat(split["validation"]["start_utc"]),
            datetime.fromisoformat(split["validation"]["end_utc"]),
            datetime.fromisoformat(split["test"]["start_utc"]),
            int(split["train"]["rows"]) + int(split["validation"]["rows"]),
        )

    def check_time(self, value: datetime) -> None:
        if value.tzinfo is None or not self.start <= value <= self.end or value >= self.test_start:
            raise ValueError("Stage 2 rejects a timestamp outside development")

    def check_trade(self, trade: Trade) -> None:
        for value in (trade.signal_timestamp, trade.entry_timestamp, trade.exit_timestamp):
            self.check_time(value)
        for value in (trade.reference_timestamp, trade.sweep_timestamp):
            if value:
                self.check_time(datetime.fromisoformat(value))
        if not (0 <= trade.signal_index < trade.entry_index <= trade.exit_index < self.n_rows):
            raise ValueError("Stage 2 rejects an index outside development")
        if not trade.signal_timestamp < trade.entry_timestamp <= trade.exit_timestamp:
            raise ValueError("Invalid trade time order")
        if trade.setup not in ("A", "B") or trade.direction not in ("long", "short"):
            raise ValueError("Unknown setup/direction")
        if trade.setup == "B" and trade.strategy_version != "v2":
            raise ValueError("B_v1 evaluation is prohibited")
        if trade.outcome not in ("WIN", "LOSS", "TIMEOUT"):
            raise ValueError("Unknown trade outcome")
        if not all(math.isfinite(v) for v in (trade.result_r, trade.mae_r, trade.mfe_r)):
            raise ValueError("Non-finite trade result")


@dataclass(frozen=True)
class DevelopmentTrades:
    boundary: DevelopmentBoundary
    scenarios: Tuple[Tuple[str, Tuple[Trade, ...]], ...]

    def __post_init__(self):
        if {name for name, _ in self.scenarios} != set(SCENARIO_FILES):
            raise ValueError("All three cost scenarios are required")
        for name, trades in self.scenarios:
            for trade in trades:
                self.boundary.check_trade(trade)
                if trade.cost_scenario != name:
                    raise ValueError("Scenario lineage mismatch")


def parse_development_trade(row: Mapping[str, str], boundary: DevelopmentBoundary) -> Trade:
    # Check boundaries BEFORE touching result or price fields.
    timestamps = {}
    for key in ("signal_timestamp", "entry_timestamp", "exit_timestamp"):
        timestamps[key] = datetime.fromisoformat(row[key])
        boundary.check_time(timestamps[key])
    for key in ("reference_timestamp", "sweep_timestamp"):
        if row[key]:
            boundary.check_time(datetime.fromisoformat(row[key]))
    indexes = {key: int(row[key]) for key in ("signal_index", "entry_index", "exit_index")}
    if any(not 0 <= value < boundary.n_rows for value in indexes.values()):
        raise ValueError("Stage 2 rejects an index outside development")
    values = dict(timestamps, **indexes)
    integers = {"bars_held"}
    floats = {"entry_bid_open", "entry_price", "stop_loss", "take_profit", "risk_usd",
              "result_r", "mae_r", "mfe_r", "spread_usd", "slippage_usd", "cost_multiplier"}
    for field in fields(Trade):
        if field.name in values:
            continue
        raw = row[field.name]
        if field.name in integers:
            values[field.name] = int(raw)
        elif field.name in floats:
            values[field.name] = float(raw)
        elif field.name == "truncated_by_gap":
            if raw not in ("True", "False"):
                raise ValueError("Invalid truncation flag")
            values[field.name] = raw == "True"
        else:
            values[field.name] = raw
    trade = Trade(**values)
    boundary.check_trade(trade)
    return trade


def load_development_trades(reports_dir: Path, config) -> DevelopmentTrades:
    """Only fixed Stage 1 trade filenames are opened; no market-data path API."""
    boundary = DevelopmentBoundary.from_config(config)
    scenarios = []
    for scenario, filename in SCENARIO_FILES.items():
        with (reports_dir / filename).open("r", encoding="utf-8", newline="") as handle:
            trades = tuple(parse_development_trade(row, boundary) for row in csv.DictReader(handle))
        scenarios.append((scenario, trades))
    return DevelopmentTrades(boundary, tuple(scenarios))


def assert_locked_integrity(data_path: Path, config):
    """Stream opaque locked strings for SHA-256 only; expose hashes/counts only.

    SHA-256 verification necessarily reads locked bytes. No test price is
    interpreted or delivered to the evaluator. No in-memory test partition.
    """
    split = config["split"]
    train_end = int(split["train"]["rows"]) - 1
    validation_start = train_end + 1
    test_start = validation_start + int(split["validation"]["rows"])
    total = int(split["total_rows"])
    boundary_points = {
        0: split["train"]["start_utc"], train_end: split["train"]["end_utc"],
        validation_start: split["validation"]["start_utc"],
        test_start - 1: split["validation"]["end_utc"],
        test_start: split["test"]["start_utc"], total - 1: split["test"]["end_utc"],
    }
    digest = hashlib.sha256()
    count = 0
    with data_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("Missing data header")
        headers = reader.fieldnames
        digest.update((json.dumps(headers, separators=(",", ":")) + "\n").encode("utf-8"))
        for index, row in enumerate(reader):
            count += 1
            if index in boundary_points and row["timestamp"] != boundary_points[index]:
                raise ValueError("Locked split timestamp mismatch")
            if index >= test_start:
                digest.update((json.dumps([row.get(key, "") for key in headers],
                                          separators=(",", ":")) + "\n").encode("utf-8"))
    if count != total or count - test_start != int(split["test"]["rows"]):
        raise ValueError("Locked split row-count mismatch")
    actual = digest.hexdigest()
    if actual != split["test"]["partition_sha256"]:
        raise ValueError("Locked test SHA-256 mismatch")
    dataset_hash = file_sha256(data_path)
    if dataset_hash != config["data_source"]["normalized_file_sha256"]:
        raise ValueError("Frozen dataset SHA-256 mismatch")
    return {"status": "PASS", "test_partition_sha256": actual,
            "dataset_sha256": dataset_hash, "test_rows_hashed": count - test_start,
            "test_rows_exposed_to_evaluator": 0}
