"""Normalize HistData XAUUSD M1 BID bars and resample them to M15 UTC.

The downloaded XAUUSD files use an EST-like wall clock whose daylight-saving
calendar follows the European last-Sunday-of-March/October dates. This unusual
rule was verified empirically against independent Dukascopy-derived BID series
for 2022, 2023, and 2024. Raw ZIPs are preserved. Duplicate M1 records are
handled per source day and fully logged.
"""

import csv
import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_FILE = BASE_DIR / "rawdata" / "merged" / "XAUUSD_M1_FULL.csv"
OUTPUT_FILE = BASE_DIR / "data" / "XAUUSD_M15_UTC.csv"
REPORT_FILE = BASE_DIR / "reports" / "histdata_conversion.json"
UTC = timezone.utc
OHLC = Tuple[float, float, float, float]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _last_sunday(year: int, month: int) -> date:
    cursor = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return cursor - timedelta(days=(cursor.weekday() - 6) % 7)


def _source_offset_hours(local_date: date) -> int:
    """Return the empirically verified HistData UTC offset for this dataset."""

    summer_start = _last_sunday(local_date.year, 3)
    summer_end = _last_sunday(local_date.year, 10)
    return -4 if summer_start <= local_date < summer_end else -5


def _parse_timestamp(date_value: str, time_value: str) -> datetime:
    naive = datetime.strptime(
        "%s %s" % (date_value.strip(), time_value.strip()), "%Y.%m.%d %H:%M"
    )
    offset = _source_offset_hours(naive.date())
    source_tz = timezone(
        timedelta(hours=offset),
        name="HISTDATA_EU_DST_UTC%+03d" % offset,
    )
    return naive.replace(tzinfo=source_tz)


def _bucket_15m(timestamp_utc: datetime) -> datetime:
    return timestamp_utc.replace(
        minute=(timestamp_utc.minute // 15) * 15, second=0, microsecond=0
    )


def _merge_occurrences(occurrences: Sequence[OHLC]) -> OHLC:
    """Merge partial/duplicate minute bars in original occurrence order."""

    return (
        occurrences[0][0],
        max(values[1] for values in occurrences),
        min(values[2] for values in occurrences),
        occurrences[-1][3],
    )


def _write_bucket(writer: csv.DictWriter, bucket: Dict[str, object]) -> None:
    writer.writerow(
        {
            "timestamp": bucket["timestamp"].isoformat(),  # type: ignore
            "open": format(bucket["open"], ".10g"),
            "high": format(bucket["high"], ".10g"),
            "low": format(bucket["low"], ".10g"),
            "close": format(bucket["close"], ".10g"),
            "m1_count": bucket["m1_count"],
        }
    )


def main() -> None:
    if not INPUT_FILE.exists():
        raise SystemExit("Missing input file: %s" % INPUT_FILE)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT_FILE.with_suffix(".csv.tmp")

    stats = {
        "input_m1_rows": 0,
        "unique_m1_timestamps": 0,
        "exact_duplicate_m1_rows_dropped": 0,
        "conflicting_duplicate_m1_rows_merged": 0,
        "m1_gap_events_including_market_closures": 0,
        "output_m15_rows": 0,
        "m15_rows_with_fewer_than_15_m1_rows": 0,
    }
    exact_examples: List[Dict[str, object]] = []
    conflict_examples: List[Dict[str, object]] = []
    first_source: Optional[datetime] = None
    last_source: Optional[datetime] = None
    previous_unique_source: Optional[datetime] = None
    current_bucket: Optional[Dict[str, object]] = None
    current_day: Optional[date] = None
    day_rows: Dict[datetime, List[OHLC]] = {}

    with INPUT_FILE.open("r", encoding="utf-8-sig", newline="") as source, temporary.open(
        "w", encoding="utf-8", newline=""
    ) as target:
        reader = csv.reader(source)
        writer = csv.DictWriter(
            target,
            fieldnames=("timestamp", "open", "high", "low", "close", "m1_count"),
        )
        writer.writeheader()

        def emit_day() -> None:
            nonlocal previous_unique_source, current_bucket, first_source, last_source
            if not day_rows:
                return
            for source_timestamp in sorted(day_rows):
                occurrences = day_rows[source_timestamp]
                if len(occurrences) > 1:
                    duplicates = len(occurrences) - 1
                    if all(values == occurrences[0] for values in occurrences[1:]):
                        stats["exact_duplicate_m1_rows_dropped"] += duplicates
                        if len(exact_examples) < 20:
                            exact_examples.append(
                                {
                                    "timestamp": source_timestamp.isoformat(),
                                    "occurrences": len(occurrences),
                                }
                            )
                        values = occurrences[0]
                    else:
                        stats["conflicting_duplicate_m1_rows_merged"] += duplicates
                        if len(conflict_examples) < 20:
                            conflict_examples.append(
                                {
                                    "timestamp": source_timestamp.isoformat(),
                                    "occurrences": len(occurrences),
                                    "policy": "open first, high max, low min, close last",
                                }
                            )
                        values = _merge_occurrences(occurrences)
                else:
                    values = occurrences[0]

                stats["unique_m1_timestamps"] += 1
                if previous_unique_source is not None:
                    if source_timestamp <= previous_unique_source:
                        raise ValueError(
                            "Normalized M1 order failed at %s" % source_timestamp.isoformat()
                        )
                    if source_timestamp - previous_unique_source > timedelta(minutes=1):
                        stats["m1_gap_events_including_market_closures"] += 1
                previous_unique_source = source_timestamp
                if first_source is None:
                    first_source = source_timestamp
                last_source = source_timestamp

                timestamp_utc = source_timestamp.astimezone(UTC)
                bucket_timestamp = _bucket_15m(timestamp_utc)
                if current_bucket is None:
                    current_bucket = {
                        "timestamp": bucket_timestamp,
                        "open": values[0],
                        "high": values[1],
                        "low": values[2],
                        "close": values[3],
                        "m1_count": 1,
                    }
                elif current_bucket["timestamp"] == bucket_timestamp:
                    current_bucket["high"] = max(current_bucket["high"], values[1])  # type: ignore
                    current_bucket["low"] = min(current_bucket["low"], values[2])  # type: ignore
                    current_bucket["close"] = values[3]
                    current_bucket["m1_count"] = int(current_bucket["m1_count"]) + 1
                else:
                    _write_bucket(writer, current_bucket)
                    stats["output_m15_rows"] += 1
                    if int(current_bucket["m1_count"]) < 15:
                        stats["m15_rows_with_fewer_than_15_m1_rows"] += 1
                    current_bucket = {
                        "timestamp": bucket_timestamp,
                        "open": values[0],
                        "high": values[1],
                        "low": values[2],
                        "close": values[3],
                        "m1_count": 1,
                    }

        for row_number, row in enumerate(reader, start=1):
            stats["input_m1_rows"] += 1
            if len(row) != 7:
                raise ValueError("Row %d has %d fields; expected 7" % (row_number, len(row)))
            timestamp = _parse_timestamp(row[0], row[1])
            values: OHLC = (float(row[2]), float(row[3]), float(row[4]), float(row[5]))
            if not all(math.isfinite(value) and value > 0 for value in values):
                raise ValueError("Row %d has non-finite or non-positive OHLC" % row_number)
            if values[1] < max(values) or values[2] > min(values):
                raise ValueError("Row %d violates OHLC ordering" % row_number)
            if current_day is None:
                current_day = timestamp.date()
            elif timestamp.date() < current_day:
                raise ValueError("Row %d moves to an earlier source date" % row_number)
            elif timestamp.date() != current_day:
                emit_day()
                day_rows.clear()
                current_day = timestamp.date()
            day_rows.setdefault(timestamp, []).append(values)

        emit_day()
        if current_bucket is not None:
            _write_bucket(writer, current_bucket)
            stats["output_m15_rows"] += 1
            if int(current_bucket["m1_count"]) < 15:
                stats["m15_rows_with_fewer_than_15_m1_rows"] += 1

    temporary.replace(OUTPUT_FILE)
    report = {
        "status": "PASS",
        "source": str(INPUT_FILE.resolve()),
        "output": str(OUTPUT_FILE.resolve()),
        "source_sha256": _sha256(INPUT_FILE),
        "output_sha256": _sha256(OUTPUT_FILE),
        "source_format": "HistData MetaTrader M1 bars",
        "price_side": "BID",
        "source_timezone": (
            "empirical HistData EST-like clock: UTC-05 in winter, UTC-04 between "
            "the last Sundays of March and October"
        ),
        "timezone_correction": (
            "Applied +5 hours in winter and +4 hours in the empirically observed "
            "summer interval, then converted to UTC"
        ),
        "timezone_evidence": (
            "Empirical BID-price alignment against Dukascopy-derived M15 data for "
            "2022-2024; the provider's generic fixed-EST documentation and standard "
            "America/New_York DST dates do not match this downloaded dataset"
        ),
        "output_timezone": "UTC",
        "timestamp_semantics": "bar open",
        "volume_policy": "ignored and omitted from normalized output",
        "source_start": first_source.isoformat() if first_source else None,
        "source_end": last_source.isoformat() if last_source else None,
        "utc_start": first_source.astimezone(UTC).isoformat() if first_source else None,
        "utc_end": last_source.astimezone(UTC).isoformat() if last_source else None,
        **stats,
        "exact_duplicate_examples": exact_examples,
        "conflicting_duplicate_examples": conflict_examples,
        "corrections": [
            {
                "action": "APPLY_EMPIRICAL_HISTDATA_EU_DST_CLOCK_AND_CONVERT_TO_UTC",
                "detail": "UTC-05 in winter; UTC-04 from the last Sunday of March through the day before the last Sunday of October.",
            },
            {
                "action": "DROP_EXACT_DUPLICATE_M1_ROWS",
                "count": stats["exact_duplicate_m1_rows_dropped"],
            },
            {
                "action": "MERGE_CONFLICTING_DUPLICATE_M1_ROWS",
                "count": stats["conflicting_duplicate_m1_rows_merged"],
                "detail": "For a repeated minute: open first, high max, low min, close last.",
            },
            {
                "action": "RESAMPLE_M1_TO_M15",
                "detail": "Left-labelled, left-closed OHLC; empty buckets not synthesized.",
            },
            {
                "action": "DROP_VOLUME",
                "detail": "Research rules prohibit volume features.",
            },
        ],
    }
    REPORT_FILE.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
