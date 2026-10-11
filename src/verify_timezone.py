"""Verify the HistData timestamp correction against Dukascopy BID M15 prices."""

import csv
import gzip
import hashlib
import json
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
HISTDATA_FILE = ROOT / "data" / "XAUUSD_M15_UTC.csv"
REPORT_FILE = ROOT / "reports" / "timezone_verification_2020_2026.json"
SUMMARY_FILE = ROOT / "reports" / "TIMEZONE_VERIFICATION.md"
SHIFTS_MINUTES = (-60, 0, 60)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("No values")
    return ordered[int((len(ordered) - 1) * probability)]


def _last_sunday(year: int, month: int) -> date:
    if month == 12:
        cursor = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    return cursor - timedelta(days=(cursor.weekday() + 1) % 7)


def _first_sunday(year: int, month: int) -> date:
    cursor = date(year, month, 1)
    return cursor + timedelta(days=(6 - cursor.weekday()) % 7)


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _focus_weeks(year: int) -> Dict[date, str]:
    result: Dict[date, str] = {}
    march = _last_sunday(year, 3)
    for offset in range(-14, 15):
        result[_week_start(march + timedelta(days=offset))] = "LATE_MARCH"
    october = _last_sunday(year, 10)
    november = _first_sunday(year, 11)
    cursor = october - timedelta(days=7)
    end = november + timedelta(days=7)
    while cursor <= end:
        result[_week_start(cursor)] = "LATE_OCT_EARLY_NOV"
        cursor += timedelta(days=1)
    return result


def _read_histdata() -> Dict[datetime, Tuple[float, float, float, float]]:
    result = {}
    with HISTDATA_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            timestamp = datetime.fromisoformat(row["timestamp"]).astimezone(timezone.utc)
            result[timestamp] = tuple(
                float(row[name]) for name in ("open", "high", "low", "close")
            )
    return result


def _reference_file(year: int) -> Tuple[Path, str]:
    if year <= 2025:
        return (
            ROOT / "rawdata" / ("XAUUSD_M15_DUKASCOPY_EVTL_%d.json.gz" % year),
            "EV Trading Labs annual M15 BID derived from Dukascopy ticks",
        )
    return (
        ROOT / "rawdata" / ("XAUUSD_M15_DUKASCOPY_DIRECT_%d.json.gz" % year),
        "Dukascopy chart endpoint M15 BID",
    )


def _read_reference(path: Path) -> Dict[datetime, Tuple[float, float, float, float]]:
    with gzip.open(str(path), "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    result = {}
    for row in payload:
        timestamp = datetime.fromtimestamp(int(row["ts"]), timezone.utc)
        result[timestamp] = tuple(float(row[name]) for name in ("o", "h", "l", "c"))
    return result


def _shift_medians(
    hist: Mapping[datetime, Tuple[float, float, float, float]],
    ref: Mapping[datetime, Tuple[float, float, float, float]],
    timestamps: Iterable[datetime],
) -> Tuple[Dict[str, object], int]:
    differences: Dict[int, List[float]] = {shift: [] for shift in SHIFTS_MINUTES}
    zero_rows = 0
    for timestamp in timestamps:
        hist_prices = hist.get(timestamp)
        if hist_prices is None:
            continue
        for shift in SHIFTS_MINUTES:
            shifted = timestamp + timedelta(minutes=shift)
            ref_prices = ref.get(shifted)
            if ref_prices is not None:
                differences[shift].append(abs(hist_prices[3] - ref_prices[3]))
        if timestamp in ref:
            zero_rows += 1
    medians = {
        str(shift): (
            statistics.median(differences[shift]) if differences[shift] else None
        )
        for shift in SHIFTS_MINUTES
    }
    eligible = {
        shift: value
        for shift, value in ((s, medians[str(s)]) for s in SHIFTS_MINUTES)
        if value is not None and len(differences[shift]) >= 50
    }
    best = min(eligible, key=eligible.get) if eligible else None
    zero = medians["0"]
    disagreement = bool(
        best is not None
        and best != 0
        and zero is not None
        and eligible[best] + 0.02 < zero
    )
    return (
        {
            "overlap_by_shift": {
                str(shift): len(differences[shift]) for shift in SHIFTS_MINUTES
            },
            "median_abs_close_usd_by_histdata_shift_minutes": medians,
            "best_shift_minutes": best,
            "status": (
                "DISAGREES" if disagreement else "AGREES" if best is not None else "INSUFFICIENT_DATA"
            ),
        },
        zero_rows,
    )


def verify() -> Dict[str, object]:
    hist = _read_histdata()
    report: Dict[str, object] = {
        "status": "PASS",
        "histdata_file": str(HISTDATA_FILE.resolve()),
        "histdata_sha256": _sha256(HISTDATA_FILE),
        "rule_under_test": (
            "UTC-05 in winter; UTC-04 from the last Sunday of March through "
            "the day before the last Sunday of October"
        ),
        "method": (
            "Match BID M15 OHLC by UTC timestamp; compare the median close-price "
            "error at HistData timestamp shifts -60, 0, and +60 minutes for every "
            "ISO week. A disagreement requires a nonzero shift to improve the "
            "median by more than 0.02 USD."
        ),
        "years": {},
        "disagreement_weeks": [],
        "unavailable_focus_periods": [],
    }
    all_disagreements: List[Dict[str, object]] = []
    unavailable: List[Dict[str, object]] = []
    for year in range(2020, 2027):
        path, source = _reference_file(year)
        ref = _read_reference(path)
        year_hist = {timestamp: prices for timestamp, prices in hist.items() if timestamp.year == year}
        common = sorted(set(year_hist).intersection(ref))
        ohlc_differences = defaultdict(list)
        for timestamp in common:
            for index, name in enumerate(("open", "high", "low", "close")):
                ohlc_differences[name].append(abs(year_hist[timestamp][index] - ref[timestamp][index]))
        weeks = sorted({_week_start(timestamp.date()) for timestamp in year_hist})
        focus = _focus_weeks(year)
        checked = 0
        insufficient = 0
        transition_rows = []
        year_disagreements = []
        for week in weeks:
            timestamps = [
                timestamp
                for timestamp in year_hist
                if _week_start(timestamp.date()) == week
            ]
            comparison, zero_rows = _shift_medians(year_hist, ref, timestamps)
            comparison.update(
                {
                    "iso_week_start": week.isoformat(),
                    "focus": focus.get(week),
                }
            )
            if comparison["status"] == "INSUFFICIENT_DATA":
                insufficient += 1
            else:
                checked += 1
            if comparison["status"] == "DISAGREES":
                year_disagreements.append(comparison)
                all_disagreements.append({"year": year, **comparison})
            if week in focus:
                transition_rows.append(comparison)

        for week, label in sorted(focus.items()):
            if week not in weeks:
                unavailable.append(
                    {
                        "year": year,
                        "iso_week_start": week.isoformat(),
                        "focus": label,
                        "reason": "HistData range does not include this week",
                    }
                )
        year_summary, _ = _shift_medians(year_hist, ref, year_hist.keys())
        report["years"][str(year)] = {
            "reference_file": str(path.resolve()),
            "reference_sha256": _sha256(path),
            "reference_source": source,
            "histdata_rows": len(year_hist),
            "reference_rows": len(ref),
            "direct_overlap_rows": len(common),
            "direct_overlap_abs_ohlc_usd": {
                name: {
                    "median": statistics.median(values),
                    "p99": _percentile(values, 0.99),
                }
                for name, values in ohlc_differences.items()
            },
            "whole_year_shift_check": year_summary,
            "iso_weeks_checked": checked,
            "iso_weeks_insufficient": insufficient,
            "disagreement_weeks": year_disagreements,
            "transition_focus_weeks": transition_rows,
        }
    report["disagreement_weeks"] = all_disagreements
    report["unavailable_focus_periods"] = unavailable
    if all_disagreements:
        report["status"] = "FAIL"
    return report


def _write_summary(report: Mapping[str, object]) -> None:
    lines = [
        "# Timezone verification against Dukascopy-derived BID M15",
        "",
        "Status: **%s**" % report["status"],
        "",
        "The test compared every available ISO week at timestamp shifts -60, 0,",
        "and +60 minutes. A week disagrees only when a nonzero shift improves the",
        "median close-price match by more than 0.02 USD.",
        "",
        "| Year | Direct overlap | Median close diff (USD) | p99 close diff (USD) | Weeks checked | Disagreements |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    years = report["years"]
    for year in range(2020, 2027):
        item = years[str(year)]
        close = item["direct_overlap_abs_ohlc_usd"]["close"]
        lines.append(
            "| %d | %s | %.6f | %.6f | %d | %d |"
            % (
                year,
                format(item["direct_overlap_rows"], ","),
                close["median"],
                close["p99"],
                item["iso_weeks_checked"],
                len(item["disagreement_weeks"]),
            )
        )
    lines.extend(
        [
            "",
            "No disagreement week means the zero-minute alignment remained the",
            "best price match, including the available late-March and late-October/",
            "early-November focus weeks.",
            "",
            "The 2026 HistData file ends on 2026-10-02, so its late-October and",
            "early-November weeks do not exist and cannot be tested.",
        ]
    )
    SUMMARY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    report = verify()
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    _write_summary(report)
    print(json.dumps({
        "status": report["status"],
        "disagreement_weeks": len(report["disagreement_weeks"]),
        "report": str(REPORT_FILE.resolve()),
    }, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
