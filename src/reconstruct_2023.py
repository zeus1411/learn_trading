"""Fill missing 2023 XAUUSD M15 BID bars from a Dukascopy-derived source.

The HistData-normalized file is never overwritten. Existing HistData bars win;
only timestamps absent from HistData are inserted. Every inserted bar keeps an
empty ``m1_count`` because the repair source is already aggregated to M15.
"""

import bisect
import csv
import gzip
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence

BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_FILE = BASE_DIR / "data" / "XAUUSD_M15_UTC.csv"
REPAIR_FILE = BASE_DIR / "rawdata" / "XAUUSD_M15_DUKASCOPY_EVTL_2023.json.gz"
OUTPUT_FILE = BASE_DIR / "data" / "XAUUSD_M15_UTC_RECONSTRUCTED_2023.csv"
REPORT_FILE = BASE_DIR / "reports" / "2023_reconstruction.json"
UTC = timezone.utc
OHLC = ("open", "high", "low", "close")


def _crosses_weekend(start: datetime, end: datetime) -> bool:
    cursor = start
    while cursor.date() <= end.date():
        if cursor.weekday() >= 5:
            return True
        cursor += timedelta(days=1)
    return False


def _is_scheduled_metals_break(start: datetime, end: datetime) -> bool:
    if end - start != timedelta(minutes=75):
        return False
    return (start.hour, start.minute, end.hour, end.minute) in {
        (20, 45, 22, 0),
        (21, 45, 23, 0),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("Cannot calculate a percentile of an empty sequence")
    ordered = sorted(values)
    return ordered[int(probability * (len(ordered) - 1))]


def _summary(values: Sequence[float]) -> Dict[str, float]:
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2.0
    )
    return {
        "median_abs_usd": median,
        "mean_abs_usd": sum(ordered) / len(ordered),
        "p95_abs_usd": _percentile(ordered, 0.95),
        "p99_abs_usd": _percentile(ordered, 0.99),
        "max_abs_usd": ordered[-1],
    }


def _validate_ohlc(values: Mapping[str, float], context: str) -> None:
    prices = [values[name] for name in OHLC]
    if not all(math.isfinite(value) and value > 0 for value in prices):
        raise ValueError("%s has non-finite or non-positive OHLC" % context)
    if values["high"] < max(prices) or values["low"] > min(prices):
        raise ValueError("%s violates OHLC ordering" % context)


def _read_histdata() -> tuple:
    rows: List[MutableMapping[str, str]] = []
    by_timestamp: Dict[datetime, MutableMapping[str, str]] = {}
    with INPUT_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "open", "high", "low", "close", "m1_count"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("Normalized HistData file does not have the expected columns")
        previous = None
        for row_number, raw in enumerate(reader, start=2):
            timestamp = datetime.fromisoformat(raw["timestamp"]).astimezone(UTC)
            if timestamp.minute % 15 or timestamp.second or timestamp.microsecond:
                raise ValueError("HistData row %d is off the M15 grid" % row_number)
            if previous is not None and timestamp <= previous:
                raise ValueError("HistData timestamps are not strictly increasing")
            previous = timestamp
            values = {name: float(raw[name]) for name in OHLC}
            _validate_ohlc(values, "HistData row %d" % row_number)
            if timestamp in by_timestamp:
                raise ValueError("Duplicate HistData timestamp: %s" % timestamp.isoformat())
            row = dict(raw)
            row["timestamp"] = timestamp.isoformat()
            row["source"] = "HISTDATA_M1_BID"
            rows.append(row)
            by_timestamp[timestamp] = row
    return rows, by_timestamp


def _read_repair_source() -> Dict[datetime, Dict[str, float]]:
    with gzip.open(REPAIR_FILE, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError("Repair source must contain a JSON list")
    result: Dict[datetime, Dict[str, float]] = {}
    for index, raw in enumerate(payload):
        timestamp = datetime.fromtimestamp(int(raw["ts"]), UTC)
        if timestamp.year != 2023:
            raise ValueError("Repair row %d is outside 2023" % index)
        if timestamp.minute % 15 or timestamp.second or timestamp.microsecond:
            raise ValueError("Repair row %d is off the M15 grid" % index)
        values = {
            "open": float(raw["o"]),
            "high": float(raw["h"]),
            "low": float(raw["l"]),
            "close": float(raw["c"]),
        }
        _validate_ohlc(values, "repair row %d" % index)
        if timestamp in result:
            raise ValueError("Duplicate repair timestamp: %s" % timestamp.isoformat())
        result[timestamp] = values
    return result


def _classify_insertions(
    hist_2023: Sequence[datetime], insertions: Iterable[datetime]
) -> Dict[str, int]:
    counts: Counter = Counter()
    for timestamp in sorted(insertions):
        index = bisect.bisect_left(hist_2023, timestamp)
        if index == 0 or index == len(hist_2023):
            counts["OUTSIDE_HISTDATA_2023_RANGE"] += 1
            continue
        previous, current = hist_2023[index - 1], hist_2023[index]
        if _is_scheduled_metals_break(previous, current):
            counts["SCHEDULED_DAILY_BREAK"] += 1
        elif _crosses_weekend(previous, current):
            counts["WEEKEND_CROSSING_LATE_REOPEN"] += 1
        else:
            counts["UNEXPECTED_INTRASESSION_GAP"] += 1
    return dict(sorted(counts.items()))


def main() -> None:
    for path in (INPUT_FILE, REPAIR_FILE):
        if not path.exists():
            raise SystemExit("Missing input file: %s" % path)

    rows, histdata = _read_histdata()
    repair = _read_repair_source()
    hist_2023 = {timestamp: row for timestamp, row in histdata.items() if timestamp.year == 2023}
    common = sorted(set(hist_2023) & set(repair))
    hist_only = sorted(set(hist_2023) - set(repair))
    repair_only = sorted(set(repair) - set(hist_2023))

    differences: Dict[str, List[float]] = {name: [] for name in OHLC}
    outlier_examples = []
    for timestamp in common:
        hist_values = {name: float(hist_2023[timestamp][name]) for name in OHLC}
        for name in OHLC:
            differences[name].append(abs(hist_values[name] - repair[timestamp][name]))
        maximum = max(abs(hist_values[name] - repair[timestamp][name]) for name in OHLC)
        if maximum > 0.10 and len(outlier_examples) < 20:
            outlier_examples.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "max_abs_usd": maximum,
                    "histdata": hist_values,
                    "repair_source": repair[timestamp],
                    "histdata_m1_count": hist_2023[timestamp]["m1_count"],
                }
            )

    overlap_stats = {name: _summary(differences[name]) for name in OHLC}
    gates = {
        "at_least_20000_overlap_bars": len(common) >= 20000,
        "all_histdata_2023_timestamps_present_in_repair_source": not hist_only,
        "median_close_difference_at_most_0_01_usd": (
            overlap_stats["close"]["median_abs_usd"] <= 0.01
        ),
        "all_ohlc_p99_differences_at_most_0_02_usd": all(
            overlap_stats[name]["p99_abs_usd"] <= 0.02 for name in OHLC
        ),
    }
    if not all(gates.values()):
        raise SystemExit("Repair-source compatibility gate failed: %s" % gates)

    output_rows = list(rows)
    inserted_by_month: Counter = Counter()
    for timestamp in repair_only:
        values = repair[timestamp]
        output_rows.append(
            {
                "timestamp": timestamp.isoformat(),
                "open": format(values["open"], ".10g"),
                "high": format(values["high"], ".10g"),
                "low": format(values["low"], ".10g"),
                "close": format(values["close"], ".10g"),
                "m1_count": "",
                "source": "EVTL_DUKASCOPY_M15_BID_FILL",
            }
        )
        inserted_by_month[timestamp.strftime("%Y-%m")] += 1
    output_rows.sort(key=lambda row: datetime.fromisoformat(row["timestamp"]))

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT_FILE.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("timestamp", "open", "high", "low", "close", "m1_count", "source"),
        )
        writer.writeheader()
        writer.writerows(output_rows)
    temporary.replace(OUTPUT_FILE)

    report = {
        "status": "PASS",
        "policy": "Preserve every HistData bar; insert only absent 2023 timestamps from repair source",
        "input_file": str(INPUT_FILE.resolve()),
        "input_sha256": _sha256(INPUT_FILE),
        "repair_file": str(REPAIR_FILE.resolve()),
        "repair_sha256": _sha256(REPAIR_FILE),
        "repair_source": {
            "publisher": "EV Trading Labs",
            "upstream": "Dukascopy ticks",
            "instrument": "XAUUSD",
            "timeframe": "M15",
            "price_side_used": "BID (o, h, l, c); ASK fields were not used",
            "timezone": "UTC",
            "timestamp_semantics": "bar open",
            "catalog_url": "https://evtradelabs.com/data",
            "download_url": "https://evtradelabs.com/api/simulator/data/XAUUSD/M15/2023.json.gz",
            "license_note": "Free use with attribution to EV Trading Labs",
        },
        "histdata_2023_rows": len(hist_2023),
        "repair_source_2023_rows": len(repair),
        "overlap_rows": len(common),
        "histdata_only_rows": len(hist_only),
        "inserted_rows": len(repair_only),
        "inserted_by_month": dict(sorted(inserted_by_month.items())),
        "insertion_context": _classify_insertions(sorted(hist_2023), repair_only),
        "compatibility_gates": gates,
        "overlap_abs_difference_usd": overlap_stats,
        "overlap_outlier_examples": outlier_examples,
        "filled_timestamp_examples": [timestamp.isoformat() for timestamp in repair_only[:30]],
        "output_file": str(OUTPUT_FILE.resolve()),
        "output_sha256": _sha256(OUTPUT_FILE),
        "output_rows": len(output_rows),
        "m1_count_policy": (
            "Preserved for HistData rows; blank for inserted pre-aggregated M15 rows"
        ),
    }
    REPORT_FILE.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
