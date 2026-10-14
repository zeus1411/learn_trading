"""Frozen-rule stability in rolling three-month validation-only OOS blocks."""

import calendar
from datetime import timedelta

from .stage2_metrics import metrics_cube
from .stage2_data import DevelopmentTrades


def add_months(timestamp, count):
    month_index = timestamp.year * 12 + timestamp.month - 1 + count
    year, month = divmod(month_index, 12)
    month += 1
    return timestamp.replace(year=year, month=month,
                             day=min(timestamp.day, calendar.monthrange(year, month)[1]))


def rolling_oos(bundle: DevelopmentTrades, config):
    if not isinstance(bundle, DevelopmentTrades):
        raise TypeError("Walk-forward requires guarded DevelopmentTrades")
    bundle.__post_init__()
    start = bundle.boundary.validation_start
    development_end_exclusive = bundle.boundary.end + timedelta(minutes=15)
    aggregate = {name: [] for name, _ in bundle.scenarios}
    windows = []
    while start < development_end_exclusive:
        planned_end = add_months(start, 3)
        end = min(planned_end, development_end_exclusive)
        complete = planned_end <= development_end_exclusive
        selected = {}
        excluded = {}
        for scenario, trades in bundle.scenarios:
            candidates = [t for t in trades if start <= t.signal_timestamp < end]
            selected[scenario] = [t for t in candidates if t.entry_timestamp >= start and t.exit_timestamp < end]
            excluded[scenario] = len(candidates) - len(selected[scenario])
            if complete:
                aggregate[scenario].extend(selected[scenario])
        label = "OOS_%02d" % (len(windows) + 1)
        windows.append({"id": label, "start_utc": start.isoformat(), "end_exclusive_utc": end.isoformat(),
                        "complete_3_months": complete, "included_in_primary_oos": complete,
                        "boundary_crossing_trades_excluded": excluded,
                        "metrics": metrics_cube(selected, config, label)})
        start = planned_end
    return {"method": "3 calendar months; stride=3 months; start=validation start; no fitting",
            "overlap": False, "parameter_search": False, "windows": windows,
            "complete_windows": sum(w["complete_3_months"] for w in windows),
            "primary_oos": metrics_cube(aggregate, config, "oos_combined")}

