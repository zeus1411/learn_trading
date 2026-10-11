import csv
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .gaps import (
    DAILY_BREAK,
    HOLIDAY_BREAK,
    UNEXPECTED,
    WEEKEND,
    classify_gap,
)
from .models import Bar


class DataQualityError(RuntimeError):
    pass


@dataclass
class Correction:
    action: str
    row_numbers: List[int]
    detail: str


@dataclass
class QualityReport:
    source: str
    input_rows: int = 0
    usable_rows: int = 0
    start_utc: Optional[str] = None
    end_utc: Optional[str] = None
    span_years: float = 0.0
    meets_minimum_span: bool = False
    timezone_status: str = "UNKNOWN"
    out_of_order_rows: int = 0
    exact_duplicate_rows: int = 0
    conflicting_duplicate_timestamps: int = 0
    bad_tick_rows: int = 0
    weekend_rows: int = 0
    total_gap_events: int = 0
    nonweekend_gap_events: int = 0
    scheduled_market_break_events: int = 0
    scheduled_holiday_break_events: int = 0
    weekend_crossing_gap_events: int = 0
    unexpected_gap_events: int = 0
    unexpected_missing_bars: int = 0
    estimated_missing_bars: int = 0
    m1_count_status: str = "NOT_AVAILABLE"
    missing_m1_count_rows: int = 0
    incomplete_m15_rows: int = 0
    severely_incomplete_m15_rows: int = 0
    m1_count_distribution: Dict[str, int] = field(default_factory=dict)
    unexpected_gaps_by_month: Dict[str, Dict[str, int]] = field(default_factory=dict)
    gap_examples: List[Dict[str, object]] = field(default_factory=list)
    bad_tick_examples: List[Dict[str, object]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    fatal_errors: List[str] = field(default_factory=list)
    corrections: List[Correction] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        result = asdict(self)
        result["status"] = "FAIL" if self.fatal_errors else "PASS"
        return result


_TIMESTAMP_NAMES = ("timestamp", "datetime", "date_time", "time")


def _normalise_header(value: str) -> str:
    return "_".join(value.strip().lower().replace("-", " ").split())


def _resolve_columns(headers: Sequence[str]) -> Dict[str, str]:
    normalised = {_normalise_header(h): h for h in headers}
    result: Dict[str, str] = {}
    for canonical in ("open", "high", "low", "close"):
        if canonical not in normalised:
            raise DataQualityError("Missing required CSV column: %s" % canonical)
        result[canonical] = normalised[canonical]
    for candidate in _TIMESTAMP_NAMES:
        if candidate in normalised:
            result["timestamp"] = normalised[candidate]
            break
    if "timestamp" not in result:
        if "date" in normalised and "time" in normalised:
            result["date"] = normalised["date"]
            result["clock"] = normalised["time"]
        else:
            raise DataQualityError(
                "Missing timestamp column (accepted: timestamp/datetime/date_time/time, "
                "or separate date and time columns)"
            )
    return result


def _parse_timestamp(raw: str, assume_utc: bool) -> Tuple[datetime, bool]:
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed: Optional[datetime] = None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        for fmt in (
            "%Y.%m.%d %H:%M:%S",
            "%Y.%m.%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
        ):
            try:
                parsed = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValueError("unparseable timestamp: %r" % raw)
    was_naive = parsed.tzinfo is None or parsed.utcoffset() is None
    if was_naive:
        if not assume_utc:
            raise ValueError(
                "naive timestamp; provide UTC offsets or rerun with --assume-utc"
            )
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    return parsed, was_naive


def validate_m15_csv(
    path: Path, assume_utc: bool = False, minimum_years: float = 3.0
) -> Tuple[List[Bar], QualityReport]:
    """Read and validate an M15 CSV without filling data or repairing prices.

    Exact duplicate rows and ordering are safe deterministic corrections. Both
    are logged. Conflicting duplicate timestamps and bad ticks are fatal.
    """

    report = QualityReport(source=str(path.resolve()))
    parsed_rows: List[Tuple[int, Bar]] = []
    naive_rows: List[int] = []
    m1_count_column_present = False
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise DataQualityError("Cannot open data file: %s" % exc) from exc

    with handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise DataQualityError("CSV has no header")
        m1_count_column_present = "m1_count" in reader.fieldnames
        source_column = next(
            (name for name in reader.fieldnames if _normalise_header(name) == "source"),
            None,
        )
        columns = _resolve_columns(reader.fieldnames)
        for row_number, row in enumerate(reader, start=2):
            report.input_rows += 1
            try:
                raw_timestamp = (
                    row[columns["timestamp"]]
                    if "timestamp" in columns
                    else "%s %s" % (row[columns["date"]], row[columns["clock"]])
                )
                timestamp, was_naive = _parse_timestamp(raw_timestamp, assume_utc)
                if was_naive:
                    naive_rows.append(row_number)
                values = [float(row[columns[name]]) for name in ("open", "high", "low", "close")]
                if not all(math.isfinite(value) for value in values):
                    raise ValueError("OHLC contains NaN or infinity")
                if m1_count_column_present:
                    raw_count = (row.get("m1_count") or "").strip()
                    if raw_count:
                        count = int(raw_count)
                        if count < 1 or count > 15:
                            raise ValueError("m1_count must be between 1 and 15")
                        key = str(count)
                        report.m1_count_distribution[key] = report.m1_count_distribution.get(key, 0) + 1
                        if count < 15:
                            report.incomplete_m15_rows += 1
                        if count < 14:
                            report.severely_incomplete_m15_rows += 1
                    else:
                        report.missing_m1_count_rows += 1
                source = (
                    (row.get(source_column) or "").strip()
                    if source_column is not None
                    else "UNSPECIFIED"
                )
                parsed_rows.append(
                    (row_number, Bar(timestamp, *values, source or "UNSPECIFIED"))
                )
            except (KeyError, TypeError, ValueError) as exc:
                report.bad_tick_rows += 1
                if len(report.bad_tick_examples) < 100:
                    report.bad_tick_examples.append(
                        {"row": row_number, "reason": str(exc), "raw": dict(row)}
                    )

    if naive_rows:
        report.timezone_status = "NAIVE_ASSUMED_UTC"
        report.corrections.append(
            Correction(
                "LOCALIZE_NAIVE_TIMESTAMPS_TO_UTC",
                naive_rows,
                "Applied only because --assume-utc was explicitly supplied.",
            )
        )
    else:
        report.timezone_status = "UTC_OFFSETS_PRESENT"
    if m1_count_column_present:
        report.m1_count_status = (
            "PARTIAL" if report.missing_m1_count_rows else "AVAILABLE"
        )

    original_timestamps = [bar.timestamp for _, bar in parsed_rows]
    report.out_of_order_rows = sum(
        1
        for previous, current in zip(original_timestamps, original_timestamps[1:])
        if current < previous
    )
    if report.out_of_order_rows:
        parsed_rows.sort(key=lambda item: (item[1].timestamp, item[0]))
        report.corrections.append(
            Correction(
                "SORT_BY_TIMESTAMP_ASCENDING",
                [],
                "%d descending timestamp transitions found" % report.out_of_order_rows,
            )
        )

    unique: List[Tuple[int, Bar]] = []
    by_timestamp: Dict[datetime, Tuple[int, Bar]] = {}
    for row_number, bar in parsed_rows:
        previous = by_timestamp.get(bar.timestamp)
        if previous is None:
            by_timestamp[bar.timestamp] = (row_number, bar)
            unique.append((row_number, bar))
        elif previous[1] == bar:
            report.exact_duplicate_rows += 1
            report.corrections.append(
                Correction(
                    "DROP_EXACT_DUPLICATE_ROW",
                    [row_number],
                    "Identical to row %d at %s"
                    % (previous[0], bar.timestamp.isoformat()),
                )
            )
        else:
            report.conflicting_duplicate_timestamps += 1
            report.fatal_errors.append(
                "Conflicting rows %d and %d share timestamp %s"
                % (previous[0], row_number, bar.timestamp.isoformat())
            )

    bars = [bar for _, bar in unique]
    for row_number, bar in unique:
        reasons: List[str] = []
        if min(bar.open, bar.high, bar.low, bar.close) <= 0:
            reasons.append("non-positive price")
        if bar.high < max(bar.open, bar.close, bar.low):
            reasons.append("high is below another OHLC value")
        if bar.low > min(bar.open, bar.close, bar.high):
            reasons.append("low is above another OHLC value")
        if bar.timestamp.minute % 15 != 0 or bar.timestamp.second != 0 or bar.timestamp.microsecond != 0:
            reasons.append("timestamp is off the M15 grid")
        if reasons:
            report.bad_tick_rows += 1
            if len(report.bad_tick_examples) < 100:
                report.bad_tick_examples.append(
                    {
                        "row": row_number,
                        "timestamp": bar.timestamp.isoformat(),
                        "reason": "; ".join(reasons),
                    }
                )

    report.weekend_rows = sum(1 for bar in bars if bar.timestamp.weekday() >= 5)
    for previous, current in zip(bars, bars[1:]):
        delta = current.timestamp - previous.timestamp
        if delta != timedelta(minutes=15):
            missing = max(0, int(delta.total_seconds() // 900) - 1)
            category = classify_gap(previous.timestamp, current.timestamp)
            weekend = category == WEEKEND
            report.total_gap_events += 1
            report.estimated_missing_bars += missing
            if category == WEEKEND:
                report.weekend_crossing_gap_events += 1
            elif category == DAILY_BREAK:
                report.scheduled_market_break_events += 1
            elif category == HOLIDAY_BREAK:
                report.scheduled_holiday_break_events += 1
            elif category == UNEXPECTED:
                report.unexpected_gap_events += 1
                report.unexpected_missing_bars += missing
                month = previous.timestamp.strftime("%Y-%m")
                summary = report.unexpected_gaps_by_month.setdefault(
                    month, {"events": 0, "estimated_missing_bars": 0}
                )
                summary["events"] += 1
                summary["estimated_missing_bars"] += missing
            if not weekend:
                report.nonweekend_gap_events += 1
            if len(report.gap_examples) < 100:
                report.gap_examples.append(
                    {
                        "after": previous.timestamp.isoformat(),
                        "before": current.timestamp.isoformat(),
                        "delta_minutes": delta.total_seconds() / 60.0,
                        "estimated_missing_bars": missing,
                        "crosses_weekend": weekend,
                        "category": category,
                    }
                )

    report.usable_rows = len(bars)
    if bars:
        report.start_utc = bars[0].timestamp.isoformat()
        report.end_utc = bars[-1].timestamp.isoformat()
        report.span_years = (bars[-1].timestamp - bars[0].timestamp).total_seconds() / (365.2425 * 86400)
        report.meets_minimum_span = report.span_years >= minimum_years
    if not report.meets_minimum_span:
        report.fatal_errors.append(
            "History span %.3f years is below required %.3f years"
            % (report.span_years, minimum_years)
        )
    if report.bad_tick_rows:
        report.fatal_errors.append(
            "%d malformed or invalid OHLC/timestamp rows found" % report.bad_tick_rows
        )
    if report.conflicting_duplicate_timestamps:
        report.fatal_errors.append("Conflicting duplicate timestamps require human resolution")
    if report.unexpected_gap_events:
        report.warnings.append(
            "%d unexpected gap events (%d estimated bars) found; no bars were synthesized"
            % (report.unexpected_gap_events, report.unexpected_missing_bars)
        )
    if report.incomplete_m15_rows:
        report.warnings.append(
            "%d M15 bars contain fewer than 15 source M1 bars (%d contain fewer than 14)"
            % (report.incomplete_m15_rows, report.severely_incomplete_m15_rows)
        )
    if report.missing_m1_count_rows:
        report.warnings.append(
            "%d M15 bars come from an already-aggregated source, so m1_count is unavailable"
            % report.missing_m1_count_rows
        )
    if report.weekend_rows:
        report.warnings.append(
            "%d bars have Saturday/Sunday UTC timestamps; they were retained"
            % report.weekend_rows
        )
    return bars, report


def write_quality_outputs(report: QualityReport, reports_dir: Path) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "data_quality.json").write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )
    with (reports_dir / "corrections.jsonl").open("w", encoding="utf-8") as handle:
        for correction in report.corrections:
            handle.write(json.dumps(asdict(correction), sort_keys=True) + "\n")

